"""自检：不打开主界面，把「能不能用」的每一环走一遍，并把结论写成报告。

为什么需要它：打包版出问题时是**静默**的（窗口一闪就没了），用户看不到报错。
所以给程序加一个命令行开关：

    DDO翻译助手_v3.0.6.exe --self-check

它会把结果写到程序目录的 `data\\selfcheck.txt`（源码运行则写项目里的 data\\），
内容包含：Python/打包信息、配置与术语表、Tk（界面库）、截图、OCR（含一次真实识别）、
翻译引擎、显示过滤的样例结论。出问题就把这个文件发出来。
"""
from __future__ import annotations

import sys
import traceback
from typing import Callable, List, Tuple


def _image_with_text(text: str, size: int = 30):
    """造一张带字的图，用来验证 OCR 真的能跑（模型文件都在）。"""
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (760, size + 26), "white")
    draw = ImageDraw.Draw(image)
    font = None
    for name in ("arial.ttf", "segoeui.ttf", "msyh.ttc", "simhei.ttf"):
        try:
            font = ImageFont.truetype(name, size)
            break
        except Exception:
            continue
    if font is None:
        try:
            font = ImageFont.load_default(size)
        except Exception:
            font = None
    draw.text((12, 10), text, fill="black", font=font)
    return image


def collect() -> Tuple[List[str], List[str]]:
    """返回 (报告行, 问题行)。"""
    lines: List[str] = []
    problems: List[str] = []

    def step(name: str, function: Callable[[], str]) -> None:
        try:
            detail = function()
            lines.append("  [ok]   %s%s" % (name, ("：" + detail) if detail else ""))
        except Exception as exc:
            lines.append("  [FAIL] %s：%s: %s" % (name, type(exc).__name__, exc))
            lines.append("         " + traceback.format_exc(limit=3).replace(
                "\n", "\n         ").strip())
            problems.append("%s -> %s: %s" % (name, type(exc).__name__, exc))

    from app import AUTHOR, __version__, capture, paths
    from app.config import load_config
    from app.glossary import build_glossary
    from app.store import MemoryStore

    lines.append("DDO 聊天翻译助手 自检报告")
    lines.append("版本：v%s　作者：%s" % (__version__, AUTHOR))
    frozen = bool(getattr(sys, "frozen", False))
    lines.append("运行方式：%s" % ("打包版 exe" if frozen else "源码 python"))
    lines.append("Python：%s" % sys.version.split()[0])
    lines.append("程序目录：%s" % paths.APP_DIR)
    lines.append("启动命令：%s" % (sys.executable if frozen else sys.argv[0]))
    # 把"这份 exe 认哪把发布公钥"写进报告：这是自动更新的信任根，
    # 用 `exe --self-check --no-dialog` 就能问出来，方便和发行页上的指纹核对。
    from app import update as update_module

    keys = update_module.pubkeys()
    lines.append("更新来源：%s" % update_module.HOMEPAGE)
    lines.append("签名公钥指纹：%s" % (update_module.pubkey_fingerprint(keys[0])
                                 if keys else "（未配置，不会自动安装任何更新）"))
    lines.append("")

    paths.ensure_dirs()

    def check_files() -> str:
        wanted = (paths.GLOSSARY_PATH, paths.GLOSSARY_EXTRA_PATH, paths.icon_path())
        missing = [str(p) for p in wanted if not p.exists()]
        if missing:
            raise AssertionError("缺少文件：%s" % ", ".join(missing))
        return "术语表/图标齐全（术语表目录 %s）" % paths.ASSETS_DIR

    step("程序文件", check_files)

    config = load_config()

    def check_config() -> str:
        return ("引擎=%s　模型=%s　Key=%s　区域=%s　只显示有用系统提示=%s"
                % (config.get("engine"), config.get("deepseek_model"),
                   "已填" if config.get("deepseek_key") else "未填",
                   config.get("region"), config.get("system_whitelist")))

    step("配置", check_config)

    def check_tk() -> str:
        import tkinter as tk

        root = tk.Tk()
        try:
            screen = "%dx%d" % (root.winfo_screenwidth(), root.winfo_screenheight())
            scaling = root.tk.call("tk", "scaling")
        finally:
            root.destroy()
        return "Tk %s　屏幕 %s　scaling %s" % (tk.TkVersion, screen, scaling)

    step("界面库 Tk（打包漏 Tcl/Tk 就会在这里失败）", check_tk)

    def check_capture() -> str:
        region = config.get("region") or [0, 0, 200, 60]
        image = capture.grab(region, None)
        if image is None:
            raise AssertionError("截图返回空（区域 %s）" % region)
        return "区域 %s → %dx%d，截图空间 %s" % (region, image.width, image.height,
                                              capture.capture_space())

    step("截图", check_capture)

    memory = MemoryStore()
    glossary = build_glossary(config, memory)

    def check_glossary() -> str:
        return "术语 %d 条　%s" % (len(glossary), memory.summary())

    step("术语表 / 学习库", check_glossary)

    import queue as queue_module

    from app.pipeline import Pipeline

    pipeline = Pipeline(config, memory, glossary, queue_module.Queue())

    def check_engine() -> str:
        return "%s（%s）可用=%s" % (pipeline.engine.describe(),
                                   pipeline.engine_note or "无备注",
                                   pipeline.engine.available())

    step("翻译引擎", check_engine)

    def check_ocr() -> str:
        if not pipeline._ensure_ocr():
            raise AssertionError("OCR 模型加载失败：%s" % (pipeline.ocr.error or "未知原因"))
        items = pipeline.ocr.recognize(_image_with_text("elite right?"), 1.0)
        if not items:
            raise AssertionError("OCR 没识别出文字（模型文件可能缺失，exe 打包最常踩这个坑）")
        return "识别到 %d 段：%r" % (len(items), " ".join(t for t, _box in items))

    step("OCR（含一次真实识别）", check_ocr)
    pipeline.ocr.close()

    def check_filter() -> str:
        samples = (
            ("(聊天): 战利品:vyarzar舟", False),
            ("(聊天): 玉相你寸里且时间:0天19时2刀5/秒", False),
            ("(小队):[小队]S Sinoke:guihuo,nikyireddoor", True),
            ("(小队):你加入了Longdd的队伍", True),
        )
        bad = []
        for line, expected in samples:
            shown = False
            for event in pipeline.parser.parse([line]):
                if Pipeline.is_panel_text(event.text):
                    continue
                if event.kind == "chat":
                    shown = True
                elif event.kind == "system" and not (
                        config.get("system_whitelist", True)
                        and not Pipeline.is_useful_notice(event.text)):
                    shown = True
            if shown != expected:
                bad.append("%s（期望%s）" % (line, "显示" if expected else "过滤"))
        if bad:
            raise AssertionError("过滤结论不对：%s" % "；".join(bad))
        return "%d 条样例结论全部正确" % len(samples)

    step("显示过滤（面板噪音/玩家发言）", check_filter)

    def check_engine_call() -> str:
        """真发一次最小请求，确认 Key 有效（离线引擎跳过）。"""
        from app.engines import OfflineEngine
        from app.prompt import build_messages, build_system_prompt

        if not pipeline.engine.available():
            return "跳过（引擎不可用）"
        if isinstance(pipeline.engine, OfflineEngine):
            return "离线引擎，不请求网络"
        # 必须带上真正的提示词：不带的话模型会把 "omw" 当成"解释这个缩写"，
        # 返回一大段英文说明，看着像接口坏了。
        messages = build_messages(
            build_system_prompt(str(config.get("translate_mode", "quality")), memory),
            (), (), "omw")
        result = pipeline.engine.translate(
            "omw", messages, timeout=float(config.get("timeout_seconds", 20) or 20))
        if not result.ok:
            raise AssertionError("接口调用失败：%s" % (result.error or "未知错误"))
        return "接口返回：%r（这会消耗一次 API 调用）" % result.text

    step("翻译接口实调", check_engine_call)

    lines.append("")
    if problems:
        lines.append("结论：有 %d 项没通过，请把本文件整份发出来。" % len(problems))
    else:
        lines.append("结论：全部通过，程序可以正常使用。")
    lines.append("（自检只读取屏幕上一小块区域做识别验证，不会保存截图内容。）")
    return lines, problems


def run() -> int:
    lines, problems = collect()
    report = "\n".join(lines)
    target = None
    try:
        from app import paths

        paths.ensure_dirs()
        target = paths.DATA_DIR / "selfcheck.txt"
        target.write_text(report + "\n", encoding="utf-8")
        print("报告已写入：%s" % target)
    except Exception as exc:
        print("写报告失败：%s" % exc)
    print(report)
    # 打包版没有控制台，所以再弹一个框把结论告诉用户；
    # 源码运行（python main.py --self-check）有终端，弹框只会挡路，所以不弹。
    # 加 --no-dialog 可以强制不弹（自动化脚本用）。
    if not getattr(sys, "frozen", False) or "--no-dialog" in sys.argv:
        return 1 if problems else 0
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        if problems:
            messagebox.showwarning("自检完成（有问题）",
                                   "%s\n\n报告：%s" % ("\n".join(problems[:6]), target))
        else:
            messagebox.showinfo("自检通过", "各项检查全部通过。\n报告：%s" % target)
        root.destroy()
    except Exception:
        pass
    return 1 if problems else 0
