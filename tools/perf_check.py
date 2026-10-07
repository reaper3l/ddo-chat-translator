"""性能自检：在你自己机器上量一下"监听时的开销"到底花在哪。

用法：
    python tools\\perf_check.py            （跑完窗口会等着，按回车关闭）
    python tools\\perf_check.py --no-pause （脚本里调用时用）

会依次测：截图耗时 → 画面变化检测耗时 → （整帧 vs 只识别下半条）OCR 耗时，
最后估算按当前设置的最坏 CPU 占用。卡不卡、该调哪个参数，看这个就清楚了。

两件事为用户体验专门做了（反馈：结果一闪而过没看清）：
* 结尾有一段**结论**，直接告诉你"程序实际会用哪种抓屏方式"和该调什么；
* 整份结果同时存到 `data\\perf_report.txt`，随时能打开看。
"""
from __future__ import annotations

import argparse
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


def _launched_by_double_click() -> bool:
    """是不是双击运行的（控制台是系统临时给我们开的一个）。

    双击时跑完必须等一下，否则窗口一闪就没了 —— 用户就是这么反馈的。
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        if not kernel32.GetConsoleWindow():
            return False                    # 没有控制台（从 IDE 跑）→ 不用等
        count = ctypes.c_uint(0)
        kernel32.GetConsoleProcessList(ctypes.byref(count), 1)
        return int(count.value) <= 1        # 这个控制台里只有我们自己
    except Exception:                       # noqa: BLE001
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="监听性能自检")
    parser.add_argument("--pause", action="store_true", help="跑完等按回车再关闭")
    parser.add_argument("--no-pause", action="store_true", help="跑完直接退出")
    args = parser.parse_args()

    lines: list = []

    def say(text: str = "") -> None:
        """打印 + 记下来（最后会存成文件，方便回头再看）。"""
        print(text)
        lines.append(str(text))

    def finish(code: int) -> int:
        """存报告 → 需要的话等一下，别让窗口一闪而过。"""
        try:
            from app import paths

            paths.ensure_dirs()
            report = paths.DATA_DIR / "perf_report.txt"
            report.write_text("\n".join(lines) + "\n", encoding="utf-8")
            print("\n（这份结果也存成文件了：%s）" % report)
        except Exception as exc:                  # noqa: BLE001
            print("\n（结果文件没写成：%s）" % exc)
        if not args.no_pause and (args.pause or _launched_by_double_click()):
            try:
                input("\n按回车键关闭…")
            except Exception:                     # noqa: BLE001
                pass
        return code

    config = load_config()
    # 关键一步：跟程序本体一样先设好 DPI 感知。
    # 不设的话 Tk 报的屏幕尺寸是"按显示缩放虚拟化过"的（125% 下 2560×1440 会报成
    # 2048×1152），抓屏却是物理像素 —— 于是区域会被多乘一次 1.25，工具就会误报
    # "框选区域跑到屏幕外面了"（用户实测踩到）。main.py 启动时做的就是这件事。
    try:
        from app import dpi

        dpi.enable(str(config.get("dpi_mode", "auto")))
    except Exception:                              # noqa: BLE001
        pass
    region = config.get("region")
    if not region:
        say("还没有框选聊天区域，先在程序里点「⊞ 区域」框选一次，再跑这个脚本。")
        return finish(1)
    tk_screen = _tk_screen()
    if not capture.box_on_screen(capture.convert_region(region, tk_screen)):
        say("！！框选区域跑到屏幕外面了（改过分辨率或显示器？）")
        say("　 现在抓到的会是黑图，程序会一直提示你重新框选 —— 请先回程序里点「⊞ 区域」重框。")
        say("　（下面还是会把各项耗时量出来，但数字没有意义，先重框再跑一次。）")
    threads = int(config.get("ocr_threads", 2) or 2)
    interval = max(300, int(config.get("interval_ms", 1200) or 1200)) / 1000.0

    say("=" * 62)
    say("监听性能自检")
    say("=" * 62)
    say("区域：%s" % (region,))
    say("设置：间隔 %.1f 秒 · OCR 线程 %d · 放大 %s · 变化检测 %s · 局部识别 %s"
          " · 背景压平 %s · 检测限幅 %s"
          % (interval, threads, config.get("ocr_upscale", "auto"),
             "开" if config.get("skip_identical_frame", True) else "关",
             "开" if config.get("band_ocr", True) else "关",
             "开" if config.get("flatten_background", True) else "关",
             "开" if config.get("ocr_det_cap", True) else "关"))

    # 1) 截图：程序实际走的是"只抓指定区域"的快速方式（失败才回退 Pillow）
    samples = []
    image = None
    for _ in range(8):
        started = time.perf_counter()
        image = capture.grab(region, tk_screen)
        samples.append((time.perf_counter() - started) * 1000.0)
        if image is None:
            say("截图失败：区域是否在屏幕范围内？")
            return finish(1)
    fast_samples = []
    fast_image = None
    for _ in range(8):
        started = time.perf_counter()
        fast_image = capture.grab_fast(region, tk_screen)
        fast_samples.append((time.perf_counter() - started) * 1000.0)
    say("\n截图：%dx%d" % (image.width, image.height))
    if fast_image is not None:
        say("　只抓区域（程序默认走这条）：平均 %.1f ms"
              % (sum(fast_samples) / len(fast_samples)))
    say("　Pillow 兜底（先抓整屏再裁剪）：平均 %.1f ms"
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
    say("抓屏固定开销：抓 8×8 那么大也要 %.1f ms（和抓整块差不多）——" % tiny_ms)
    say("　　　　　　　这就是 GDI 那条路：慢的是「等桌面合成器」，不是拷像素。")
    say("　　　　　　　所以程序①只在必要时抓屏、②「只识别变化的那几行」是从已抓到的那一帧")
    say("　　　　　　　上裁（不再多抓一次）、③默认优先用下面这条 DXGI（它不用等合成器）。")

    # 1c) DXGI 抓屏（装了 dxcam 才有）：程序默认走的其实是这条 —— 它不用等合成器。
    # 这里不只量速度，还要**跟系统截图比对一次**，因为程序的选择逻辑就是这么定的：
    # 对得上才用 DXGI，对不上就退回 GDI。所以这里给出的结论 = 程序实际会用哪条。
    dxgi_ms = 0.0
    dxgi_state = "unavailable"      # unavailable / mismatch / ok
    try:
        box = capture.convert_region(region, tk_screen)
        first = None
        for _attempt in range(10):                 # 刚开机/画面静止时可能还没有新帧
            first = capture.grab_dxgi(box)
            if first is not None:
                break
            time.sleep(0.1)
        if first is None:
            say("\nDXGI 抓屏：没拿到画面 —— %s"
                % (capture.dxgi_note() or "这块屏暂时没有可用帧（画面完全没动过）"))
        else:
            dxgi_samples = []
            for _ in range(20):
                started = time.perf_counter()
                capture.grab_dxgi(box)
                dxgi_samples.append((time.perf_counter() - started) * 1000.0)
            dxgi_samples.sort()
            dxgi_ms = dxgi_samples[len(dxgi_samples) // 2]
            fast_ms = sum(fast_samples) / len(fast_samples)
            same = capture.images_similar(first, image)
            dxgi_state = "ok" if same else "mismatch"
            say("\nDXGI 抓屏：平均 %.2f ms、中位 %.2f ms（%dx%d）"
                % (sum(dxgi_samples) / len(dxgi_samples), dxgi_ms,
                   first.width, first.height))
            say("　　对照「只抓区域（GDI）」的 %.1f ms：快了约 %.1f 倍"
                % (fast_ms, fast_ms / max(0.01, dxgi_ms)))
            say("　　和系统截图比对：%s" % ("一致 ✅" if same else "不一致 ❌（程序会退回 GDI）"))
    except Exception as exc:                       # noqa: BLE001
        say("\nDXGI 抓屏：没试成（%s）—— 程序会自动退回 GDI，不影响使用" % exc)

    # 2) 变化检测（每帧都要做的廉价步骤）
    signature = capture.frame_signature(image)
    started = time.perf_counter()
    rounds = 300
    for _ in range(rounds):
        frame.analyse_frame(signature, signature)
    per_frame = (time.perf_counter() - started) / rounds * 1000.0
    say("画面变化检测：%.3f ms/帧（可忽略不计）" % per_frame)

    # 3) OCR：整帧 vs 只识别下面 30%
    engine = OcrEngine()
    if not engine.load(threads):
        say("OCR 加载失败：%s" % engine.error)
        return finish(1)
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

    say("\nOCR 整帧识别：%.0f ms" % full_ms)
    say("OCR 只识别下面 30%%（滚动/新消息的常见情况）：%.0f ms" % band_ms)

    fast_ms = sum(fast_samples) / max(1, len(fast_samples))
    say("\n" + "=" * 62)
    say("结论")
    say("=" * 62)
    if dxgi_state == "ok":
        say("抓屏方式：程序会用 **DXGI 桌面复制**（本次实测中位 %.2f ms）—— 最快的那条。"
            % dxgi_ms)
    elif dxgi_state == "mismatch":
        say("抓屏方式：程序会用 **只抓区域（GDI）**（本次实测 %.1f ms）。"
            % fast_ms)
        say("　　　　DXGI 抓到的画面和系统截图对不上，程序会自动跳过它（宁慢不错）。")
    else:
        say("抓屏方式：程序会用 **只抓区域（GDI）**（本次实测 %.1f ms）。" % fast_ms)
        say("　　　　DXGI 这次没量到（原因见上面那几行）—— 想让它最快，"
            "确认装的是 `pip install dxcam`。")
    say("　　　　想强制换：设置 → 监控 →「截图方式」选 auto / gdi / pillow。")
    say("")
    say("整帧识别 %.0f ms　局部识别（只识别下面 30%%）%.0f ms" % (full_ms, band_ms))
    if band_ms > full_ms * 1.15:
        say("注意：这台机器上「局部识别」反而更慢 —— OCR 库会把窄条按最小边放大后再识别，")
        say("　　　条越窄放大越多。建议在 设置 → 监控 里打开「OCR 检测限幅」，")
        say("　　　或者干脆取消「只识别变化的那几行」。")
    say("按当前截图间隔 %.1f 秒算，最坏情况（每帧都在变）约占单核 %.0f%%。"
          % (interval, min(full_ms, band_ms) / (interval * 1000) * 100))
    say("实际聊天框多数时间是静止的 → 被「变化检测」跳过，几乎不花 CPU。")
    if dxgi_state == "ok":
        say("抓屏已经是最快的那条了 → 如果游戏还卡，多半是别的原因（同时开的东西太多、")
        say("显卡驱动、游戏本身），可以试：截图间隔 1500~2000、OCR 线程数调成 1。")
    else:
        say("如果游戏仍卡：先试「设置 → 监控」把截图间隔调成 1500~2000（GDI 每次抓屏都要")
        say("　　　　　　等合成器一个刷新周期，间隔大了打扰就少），再试 OCR 线程数调成 1。")
    say("注：「只识别变化的那几行」是**从已经抓到的那一帧上裁**的，不会再抓一次屏。")
    return finish(0)


if __name__ == "__main__":
    raise SystemExit(main())
