"""环境自检 / 诊断脚本。

用法：
    python tools/doctor.py              # 只做本地检查
    python tools/doctor.py --api        # 额外做一次真实的接口连通性测试
    python tools/doctor.py --region 0,800,600,1000

它会依次检查：Python 环境、依赖库、tkinter 能否建窗口、配置、屏幕截图、
OCR 模型、聊天行解析、术语表、翻译引擎、学习库。
界面出问题时先跑这个，大多数问题能直接定位。
"""
from __future__ import annotations

import argparse
import platform
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import paths, textutil                       # noqa: E402
from app.glossary import build_glossary               # noqa: E402
from app.parser import ChatParser                     # noqa: E402
from app.store import MemoryStore                     # noqa: E402


class Report:
    def __init__(self) -> None:
        self.failures = 0
        self.warnings = 0

    def step(self, title: str) -> None:
        print("\n=== %s ===" % title)

    def ok(self, message: str) -> None:
        print("  [OK]   %s" % message)

    def warn(self, message: str) -> None:
        self.warnings += 1
        print("  [警告] %s" % message)

    def fail(self, message: str, exc: Exception = None) -> None:
        self.failures += 1
        print("  [失败] %s" % message)
        if exc is not None:
            print("         %s: %s" % (type(exc).__name__, exc))

    def info(self, message: str) -> None:
        print("         %s" % message)


def check_python(report: Report) -> None:
    report.step("Python 环境")
    report.ok("Python %s (%s)" % (platform.python_version(), platform.architecture()[0]))
    report.info("解释器：%s" % sys.executable)
    if sys.version_info < (3, 9):
        report.fail("需要 Python 3.9 或更高版本")
    else:
        report.ok("版本满足要求")


def check_dependencies(report: Report) -> None:
    report.step("依赖库")
    for module, hint in (
        ("PIL", "pip install Pillow"),
        ("numpy", "pip install numpy"),
        ("requests", "pip install requests"),
        ("rapidocr_onnxruntime", "pip install rapidocr_onnxruntime"),
        ("onnxruntime", "pip install onnxruntime"),
    ):
        try:
            __import__(module)
            report.ok(module)
        except Exception as exc:
            report.fail("%s 没装好（%s）" % (module, hint), exc)


def check_tkinter(report: Report) -> None:
    report.step("图形界面（tkinter）")
    try:
        import tkinter as tk

        report.ok("import tkinter 成功")
    except Exception as exc:
        report.fail("import tkinter 失败，无法运行界面", exc)
        return
    try:
        root = tk.Tk()
        root.withdraw()
        root.update_idletasks()
        root.destroy()
        report.ok("可以创建窗口")
    except Exception as exc:
        report.fail("创建窗口失败（Tcl/Tk 安装可能不完整）", exc)
        report.info("常见原因：Python 的 tcl 目录缺失 init.tcl，重装 Python 时勾选 tcl/tk 即可")


def check_files(report: Report) -> tuple:
    report.step("目录与配置")
    try:
        paths.ensure_dirs()
        probe = paths.DATA_DIR / "_write_test.tmp"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        report.ok("数据目录可写：%s" % paths.DATA_DIR)
    except Exception as exc:
        report.fail("数据目录不可写：%s" % paths.DATA_DIR, exc)

    config = {}
    try:
        from app import config as config_module

        config = config_module.load_config()
        report.ok("配置文件读取成功：%s" % paths.CONFIG_PATH)
    except Exception as exc:
        report.fail("配置读取失败", exc)

    memory = None
    try:
        memory = MemoryStore()
        report.ok("学习库：%s" % memory.summary())
    except Exception as exc:
        report.fail("学习库读取失败", exc)

    try:
        glossary = build_glossary(config, memory)
        report.ok("术语表：共 %d 条（扩展表开关：%s）"
                  % (len(glossary), config.get("use_extra_glossary")))
    except Exception as exc:
        glossary = None
        report.fail("术语表构建失败", exc)

    return config, memory, glossary


def check_capture(report: Report, region) -> object:
    report.step("屏幕截图")
    if not region:
        report.warn("还没有设置聊天区域，先启动程序点「区域」框选，或用 --region 传入")
        return None
    try:
        from app import capture

        image = capture.grab(region)
        if image is None:
            report.fail("截图失败：区域 %s 是否在屏幕范围内？" % (region,))
            return None
        report.ok("截图成功：%dx%d 像素，区域 %s" % (image.width, image.height, region))
        return image
    except Exception as exc:
        report.fail("截图异常", exc)
        return None


def check_ocr(report: Report, image) -> list:
    report.step("OCR 识别")
    if image is None:
        report.warn("没有截图，跳过 OCR 测试")
        return []
    try:
        from app.ocr import OcrEngine, group_rows

        engine = OcrEngine()
        if not engine.load():
            report.fail("OCR 模型加载失败：%s" % engine.error)
            return []
        report.ok("OCR 模型加载成功（PP-OCRv5 mobile）")
        items = engine.recognize(image)
        report.ok("识别到 %d 个文本块" % len(items))
        grouped = group_rows(items)
        lines = [text for text, _box in grouped]
        for line in lines[:20]:
            report.info("行：%s" % line)
        engine.close()
        return lines
    except Exception as exc:
        report.fail("OCR 测试异常", exc)
        report.info(traceback.format_exc(limit=2))
        return []


def check_parser(report: Report, lines) -> None:
    report.step("聊天行解析")
    parser = ChatParser()
    samples = lines if lines else [
        "(常规)Alice: OMW, running to the quest now",
        "(小队):[小队] Sckham: Guys, do you play other",
        "games on Steam?",
        "Grelik加入了你的队伍",
        "[战利品] 你获得了 100 金币",
    ]
    if not lines:
        report.warn("没有真实识别结果，用内置样例测试")
    events = parser.parse(samples)
    chats = [event for event in events if event.is_chat]
    systems = [event for event in events if event.kind == "system"]
    report.ok("解析出：聊天 %d 条，系统 %d 条" % (len(chats), len(systems)))
    for event in chats:
        report.info("聊天 [%s] %s: %s" % (event.channel, event.speaker, event.text))
    for event in systems[:5]:
        report.info("系统 %s" % event.text)


def check_glossary(report: Report, glossary) -> None:
    report.step("术语保护")
    if glossary is None:
        report.warn("术语表不可用，跳过")
        return
    sentence = "need heals for shroud on elite, tr pls"
    masked, mapping, unknown = glossary.protect(sentence)
    report.info("原句  : %s" % sentence)
    report.info("保护后: %s" % masked)
    report.info("还原后: %s" % glossary.restore(masked, mapping))
    if unknown:
        report.info("没见过的词（会进待学习列表）: %s" % ", ".join(sorted(set(unknown))))
    if masked == sentence:
        report.warn("这句一个术语都没匹配上，检查术语表是否加载成功")


def check_engine(report: Report, config, with_api: bool) -> None:
    report.step("翻译引擎")
    from app.engines import create_engine

    engine, note = create_engine(config)
    report.ok("当前引擎：%s" % engine.describe())
    if note:
        report.warn(note)
    if not engine.available():
        report.warn("引擎不可用（DeepSeek 需要 API Key）")
        return
    if not with_api:
        report.info("加 --api 参数可以做一次真实翻译测试")
        return
    try:
        result = engine.translate("hello world", None, timeout=20)
        if result.ok:
            report.ok("接口连通：hello world → %s" % result.text)
        else:
            report.fail("接口调用失败：%s" % result.error)
    except Exception as exc:
        report.fail("接口调用异常", exc)


def check_text_utils(report: Report) -> None:
    report.step("文本工具")
    sample = "Hey! check https: //store.steampowered.com/app/1 out"
    protected, urls = textutil.protect_urls(sample)
    restored = textutil.restore_urls(protected, urls)
    report.info("URL 保护：%s" % protected)
    report.info("URL 还原：%s" % restored)
    report.ok("指纹：%s" % textutil.fingerprint("Hello, World!"))


def main() -> int:
    parser = argparse.ArgumentParser(description="DDO 翻译助手环境自检")
    parser.add_argument("--api", action="store_true", help="做一次真实的接口翻译测试")
    parser.add_argument("--region", help="临时指定区域，格式 x1,y1,x2,y2")
    args = parser.parse_args()

    report = Report()
    print("=" * 62)
    print("DDO 聊天翻译助手 · 环境自检")
    print("=" * 62)
    print("项目目录：%s" % paths.APP_DIR)

    check_python(report)
    check_dependencies(report)
    check_tkinter(report)
    config, _memory, glossary = check_files(report)
    check_text_utils(report)

    region = None
    if args.region:
        try:
            region = [int(part) for part in args.region.split(",")]
        except Exception:
            report.warn("--region 格式不对，应形如 0,800,600,1000")
    if not region:
        region = config.get("region")

    image = check_capture(report, region)
    lines = check_ocr(report, image)
    check_parser(report, lines)
    check_glossary(report, glossary)
    check_engine(report, config, args.api)

    print("\n" + "=" * 62)
    if report.failures:
        print("自检结果：%d 项失败，%d 项警告 —— 先解决失败项" %
              (report.failures, report.warnings))
    elif report.warnings:
        print("自检结果：全部通过，%d 项警告" % report.warnings)
    else:
        print("自检结果：全部通过，可以启动程序了")
    print("=" * 62)
    return 1 if report.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
