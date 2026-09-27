"""全流程追踪：把真实聊天内容从头走一遍，逐步打印，用来定位"为什么没翻译/没颜色"。

用法：
    python tools\trace.py                     # 抓当前区域的真实画面
    python tools\trace.py --text "(小队): [小队] Dorqeth: elite right?"
    python tools\trace.py --no-translate      # 只看到解析结果，不调用接口

打印内容依次是：引擎状态 → OCR 原始行 → 解析结果（聊天/系统）→ 送翻译的文本 →
接口返回 → 最终在窗口里显示的样子。把输出整段发出来就能定位问题。
"""
from __future__ import annotations

import argparse
import queue
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import capture, textutil             # noqa: E402
from app.config import load_config            # noqa: E402
from app.glossary import build_glossary       # noqa: E402
from app.pipeline import Job, Pipeline        # noqa: E402
from app.store import MemoryStore             # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="翻译全流程追踪")
    parser.add_argument("--text", action="append",
                        help="直接给一行聊天文本（可多次传），不抓屏")
    parser.add_argument("--file", help="从文本文件读聊天行（每行一条），不抓屏")
    parser.add_argument("--no-translate", action="store_true", help="不调用翻译接口")
    args = parser.parse_args()

    config = load_config()
    memory = MemoryStore()
    glossary = build_glossary(config, memory)
    pipeline = Pipeline(config, memory, glossary, queue.Queue())

    print("=" * 70)
    print("引擎：%s  可用=%s  备注=%s"
          % (pipeline.engine.describe(), pipeline.engine.available(),
             pipeline.engine_note or "无"))
    print("术语表：%d 条（扩展表 %s）  学习库：%s"
          % (len(glossary), config.get("use_extra_glossary"), memory.summary()))

    tk_screen = None
    try:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        tk_screen = (root.winfo_screenwidth(), root.winfo_screenheight())
        root.destroy()
    except Exception as exc:
        print("（拿不到 Tk 屏幕尺寸：%s）" % exc)

    region = config.get("region")
    lines = []
    if args.file:
        lines = [line.strip() for line in
                 Path(args.file).read_text(encoding="utf-8").splitlines() if line.strip()]
        print("输入文本：%d 行（来自 %s）" % (len(lines), args.file))
    elif args.text:
        lines = list(args.text)
        print("输入文本：%d 行（命令行给出）" % len(lines))
    else:
        print("区域：%s" % (region,))
        if not region:
            print("没有配置区域，先启动程序框选，或用 --text 给一行文本")
            return 1
        image = capture.grab(region, tk_screen)
        if image is None:
            print("截图失败")
            return 1
        print("截图：%dx%d  换算比例：%s"
              % (image.width, image.height, capture.active_scale(tk_screen)))
        items = pipeline.ocr.recognize(image, pipeline._upscale_for(image))
        print("OCR 放大倍数：%.2f" % pipeline._upscale_for(image))
        if config.get("merge_same_row", True):
            from app.ocr import group_rows

            items = group_rows(items)
        lines = [text for text, _box in items]

    print("\n---- OCR / 输入的行 ----")
    for index, line in enumerate(lines, 1):
        print("%2d. %s" % (index, line))

    events = pipeline.parser.parse(lines)
    print("\n---- 解析结果 ----")
    for event in events:
        print("   %-7s 频道=%-4s 玩家=%-12s 前缀=%r" %
              (event.kind, event.channel or "-", event.speaker or "-", event.prefix_text))
        print("           正文=%r" % event.text)

    print("\n---- 逐条翻译流程 ----")
    for event in events:
        if not event.is_chat:
            print("   [系统/其它] %s%s"
                  % (event.prefix_text, event.text))
            continue
        job = Job(seq=1, channel=event.channel, speaker=event.speaker,
                  source=event.text, prefix=event.prefix_text)
        protected, urls = textutil.protect_urls(event.text)
        masked, mapping, unknown = glossary.protect(protected)
        print("\n   玩家发言：%s" % event.text)
        print("   链接保护后：%s   （链接 %d 个）" % (protected, len(urls)))
        print("   术语保护后：%s" % masked)
        if unknown:
            print("   生词：%s" % ", ".join(sorted(set(unknown))))

        if args.no_translate:
            print("   （按参数要求跳过翻译）")
            continue

        item = pipeline._process(job)
        if item.note == "记忆命中" or item.note == "词典直译":
            print("   未调用接口（%s）" % item.note)
        print("   接口返回：%r" % item.translated)
        if item.error:
            print("   ！！错误：%s" % item.error)
        print("   窗口里会显示：%s%s%s"
              % (item.prefix, (item.speaker + ": ") if item.speaker else "", item.translated))

    print("\n" + "=" * 70)
    print("原文 → 译文 对照：")
    for event in events:
        if not event.is_chat:
            continue
        job = Job(seq=1, channel=event.channel, speaker=event.speaker,
                  source=event.text, prefix=event.prefix_text)
        if args.no_translate:
            print("  %-60s → （未翻译）" % event.text[:60])
            continue
        item = pipeline._process(job)
        print("  %-60s → %s" % (event.text[:60], item.translated))
    print("\n" + "=" * 70)
    print("如果上面「接口返回」是英文原文且没有错误，说明接口没生效；")
    print("如果「解析结果」里玩家发言被标成了 system，说明是解析/OCR 的问题。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
