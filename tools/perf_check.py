"""性能自检：在你自己机器上量一下"监听时的开销"到底花在哪。

用法：
    python tools\perf_check.py

会依次测：截图耗时 → 画面变化检测耗时 → （整帧 vs 只识别下半条）OCR 耗时，
最后估算按当前设置的最坏 CPU 占用。卡不卡、该调哪个参数，看这个就清楚了。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import capture, frame                 # noqa: E402
from app.config import load_config             # noqa: E402
from app.ocr import OcrEngine                  # noqa: E402


def _tk_screen():
    try:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        size = (root.winfo_screenwidth(), root.winfo_screenheight())
        root.destroy()
        return size
    except Exception:
        return None


def main() -> int:
    config = load_config()
    region = config.get("region")
    if not region:
        print("还没有框选聊天区域，先在程序里点「区域」框选一次，再跑这个脚本。")
        return 1
    threads = int(config.get("ocr_threads", 2) or 2)
    interval = max(300, int(config.get("interval_ms", 1200) or 1200)) / 1000.0

    print("=" * 62)
    print("监听性能自检")
    print("=" * 62)
    print("区域：%s" % (region,))
    print("设置：间隔 %.1f 秒 · OCR 线程 %d · 放大 %s · 变化检测 %s · 局部识别 %s"
          " · 背景压平 %s · 检测限幅 %s"
          % (interval, threads, config.get("ocr_upscale", "auto"),
             "开" if config.get("skip_identical_frame", True) else "关",
             "开" if config.get("band_ocr", True) else "关",
             "开" if config.get("flatten_background", True) else "关",
             "开" if config.get("ocr_det_cap", True) else "关"))

    tk_screen = _tk_screen()

    # 1) 截图：程序实际走的是"只抓指定区域"的快速方式（失败才回退 Pillow）
    samples = []
    image = None
    for _ in range(8):
        started = time.perf_counter()
        image = capture.grab(region, tk_screen)
        samples.append((time.perf_counter() - started) * 1000.0)
        if image is None:
            print("截图失败：区域是否在屏幕范围内？")
            return 1
    fast_samples = []
    fast_image = None
    for _ in range(8):
        started = time.perf_counter()
        fast_image = capture.grab_fast(region, tk_screen)
        fast_samples.append((time.perf_counter() - started) * 1000.0)
    print("\n截图：%dx%d" % (image.width, image.height))
    if fast_image is not None:
        print("　只抓区域（程序默认走这条）：平均 %.1f ms"
              % (sum(fast_samples) / len(fast_samples)))
    print("　Pillow 兜底（先抓整屏再裁剪）：平均 %.1f ms"
          % (sum(samples) / len(samples)))

    # 1b) 抓屏的"固定开销"：抓一小块和抓一大块花的时间差不多，说明慢的不是
    #     拷像素，而是在等桌面合成器（游戏是无边框全屏时画面由它合成）。
    #     结论很实用：**少抓一次屏**比"抓小一点"有用得多 —— 程序里"只识别变化
    #     的那几行"就是从已经抓到的那一帧上裁，不再为它多抓一次。
    tiny = [region[0], region[1], region[0] + 8, region[1] + 8]
    tiny_samples = []
    for _ in range(8):
        started = time.perf_counter()
        capture.grab_fast(tiny, tk_screen)
        tiny_samples.append((time.perf_counter() - started) * 1000.0)
    tiny_ms = sum(tiny_samples) / len(tiny_samples)
    print("抓屏固定开销：抓 8×8 那么大也要 %.1f ms（和抓整块差不多）——" % tiny_ms)
    print("　　　　　　　慢的是「等桌面合成器」，不是拷像素；所以程序只在必要时抓屏，")
    print("　　　　　　　而且「只识别变化的那几行」是从已抓到的那一帧上裁，不再多抓一次。")

    # 2) 变化检测（每帧都要做的廉价步骤）
    signature = capture.frame_signature(image)
    started = time.perf_counter()
    rounds = 300
    for _ in range(rounds):
        frame.analyse_frame(signature, signature)
    per_frame = (time.perf_counter() - started) / rounds * 1000.0
    print("画面变化检测：%.3f ms/帧（可忽略不计）" % per_frame)

    # 3) OCR：整帧 vs 只识别下面 30%
    engine = OcrEngine()
    if not engine.load(threads):
        print("OCR 加载失败：%s" % engine.error)
        return 1
    setting = str(config.get("ocr_upscale", "auto")).lower()
    try:
        scale = float(config.get("ocr_upscale")) if setting not in ("", "auto") else 1.5
    except Exception:
        scale = 1.5
    engine.recognize(image, scale)                      # 预热

    started = time.perf_counter()
    engine.recognize(image, scale)
    full_ms = (time.perf_counter() - started) * 1000.0

    band = image.crop((0, int(image.height * 0.7), image.width, image.height))
    engine.recognize(band, scale)
    started = time.perf_counter()
    engine.recognize(band, scale)
    band_ms = (time.perf_counter() - started) * 1000.0
    engine.close()

    print("\nOCR 整帧识别：%.0f ms" % full_ms)
    print("OCR 只识别下面 30%%（滚动/新消息的常见情况）：%.0f ms" % band_ms)

    print("\n" + "=" * 62)
    print("整帧识别 %.0f ms　局部识别（只识别下面 30%%）%.0f ms" % (full_ms, band_ms))
    if band_ms > full_ms * 1.15:
        print("注意：这台机器上「局部识别」反而更慢 —— OCR 库会把窄条按最小边放大后再识别，")
        print("　　　条越窄放大越多。建议在 设置 → 监控 里打开「OCR 检测限幅」，")
        print("　　　或者干脆取消「只识别变化的那几行」。")
    print("按当前截图间隔 %.1f 秒算，最坏情况（每帧都在变）约占单核 %.0f%%。"
          % (interval, min(full_ms, band_ms) / (interval * 1000) * 100))
    print("实际聊天框多数时间是静止的 → 被「变化检测」跳过，几乎不花 CPU。")
    print("如果游戏仍卡：先试「设置 → 监控」把截图间隔调成 1500~2000（每次抓屏都要")
    print("　　　　　　等合成器一个刷新周期，间隔大了打扰就少），再试 OCR 线程数调成 1。")
    print("注：「只识别变化的那几行」是**从已经抓到的那一帧上裁**的，不会再抓一次屏。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
