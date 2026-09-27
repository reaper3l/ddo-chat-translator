"""发布前检查：确认 dist 里的 exe 真的能跑（不是"能打包"就算完）。

用法：
    python tools\\verify_build.py            # 检查 dist 里最新的那个版本
    python tools\\verify_build.py v3.0.6     # 检查指定版本

为什么要这个工具：v3.0.0~v3.0.5 的 exe 全都打坏了（缺 tkinter、缺 OCR 模型、
读不到术语表）但没人发现，因为打包本身"成功"了、报错又是静默的。这里做两件事：

1. 查 `_internal` 里的关键文件在不在（Tcl/Tk、OCR 模型、术语表、图标）；
2. 直接运行 `exe --self-check --no-dialog`，把它的自检报告读回来。

注意：**打包必须在普通（非沙箱/非受限）环境下执行** —— 受限环境下 PyInstaller
的 tkinter 钩子会静默失败，打出来的 exe 一闪就退出。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"

# 缺任何一个都会让 exe "看着能启动、实际不能用"
REQUIRED_INTERNAL = (
    "_tkinter.pyd",                       # 界面库（缺了窗口建不起来）
    "tcl86t.dll",
    "tk86t.dll",
    "_tcl_data/init.tcl",
    "assets/glossary.json",               # 术语表（缺了术语保护整块失效）
    "assets/glossary_extra.json",
    "app_icon.ico",
    "rapidocr_onnxruntime/models/ch_PP-OCRv4_det_infer.onnx",
    "rapidocr_onnxruntime/models/ch_PP-OCRv4_rec_infer.onnx",
)


def find_exe(version: str | None) -> Path:
    if version:
        candidates = sorted(DIST.glob("DDO翻译助手_%s/DDO翻译助手_%s.exe" % (version, version)))
    else:
        candidates = sorted(DIST.glob("DDO翻译助手_v*/DDO翻译助手_v*.exe"),
                            key=lambda p: p.stat().st_mtime)
    if not candidates:
        raise SystemExit("dist 里找不到 exe，先打包：python -m PyInstaller --noconfirm build.spec")
    return candidates[-1]


def check_files(exe: Path) -> list:
    internal = exe.parent / "_internal"
    problems = []
    for rel in REQUIRED_INTERNAL:
        if not (internal / rel).exists():
            problems.append("缺少 %s" % rel)
    return problems


def run_selfcheck(exe: Path) -> list:
    report_path = exe.parent / "data" / "selfcheck.txt"
    try:
        # 只清掉上一次的自检报告：**不要动 data 目录本身**（里面有用户的配置和
        # 学习库，哪怕是在 dist 里也不能删）。
        report_path.unlink(missing_ok=True)
        print("运行 %s --self-check --no-dialog …" % exe.name)
        completed = subprocess.run(
            [str(exe), "--self-check", "--no-dialog"],
            cwd=str(exe.parent), timeout=300,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        text = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
        if not text:
            return ["自检没有写出报告（进程返回码 %s，输出：%r）"
                    % (completed.returncode, completed.stdout[-400:])]
        print(text)
        problems = []
        for line in text.splitlines():
            match = re.match(r"\s*\[FAIL\]\s*(.*)", line)
            if match:
                problems.append("自检失败：" + match.group(1))
        if completed.returncode != 0 and not problems:
            problems.append("自检返回码 %s" % completed.returncode)
        return problems
    except subprocess.TimeoutExpired:
        return ["自检超时（300 秒）"]
    finally:
        # 别把自检报告留在发布目录里（否则会被打进压缩包）
        report_path.unlink(missing_ok=True)


def main() -> int:
    version = sys.argv[1] if len(sys.argv) > 1 else None
    exe = find_exe(version)
    print("=" * 62)
    print("发布包检查：%s" % exe)
    print("=" * 62)

    problems = check_files(exe)
    for rel in REQUIRED_INTERNAL:
        mark = "FAIL" if ("缺少 %s" % rel) in problems else "ok  "
        print("  %s  %s" % (mark, rel))

    problems += run_selfcheck(exe)

    print("\n" + "=" * 62)
    if problems:
        print("检查未通过：")
        for problem in problems:
            print("  - %s" % problem)
    else:
        print("检查通过：exe 能启动、能截图、OCR 模型齐全、术语表读得到。")
    print("=" * 62)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
