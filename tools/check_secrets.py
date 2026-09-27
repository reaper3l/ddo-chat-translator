"""提交前自检：仓库里（包括全部 git 历史）有没有混进 API Key / Token。

用法：
    python tools\check_secrets.py

退出码 0 = 干净，1 = 发现可疑内容。建议每次提交前跑一次。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SELF = Path(__file__).resolve()

# 通用特征，不写死任何真实密钥（否则这个文件本身就成了泄漏点）
PATTERNS = [
    (r"sk-[A-Za-z0-9_\-]{16,}", "疑似大模型 API Key（sk- 开头）"),
    (r"(?i)(api[_-]?key|access[_-]?token|secret[_-]?key|password)"
     r"[\"'\s:=]{1,6}[\"'][A-Za-z0-9_\-+/=]{20,}[\"']", "疑似硬编码的密钥/令牌"),
]
SKIP_SUFFIX = {".png", ".jpg", ".ico", ".zip", ".exe", ".dll", ".pyd", ".onnx", ".pyc"}


def _run(args) -> str:
    try:
        result = subprocess.run(args, cwd=str(ROOT), capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
        return result.stdout or ""
    except Exception:
        return ""


def scan_tracked_files():
    problems = []
    for name in _run(["git", "ls-files"]).splitlines():
        name = name.strip()
        if not name or name == "tools/check_secrets.py":
            continue
        path = ROOT / name
        if path.suffix.lower() in SKIP_SUFFIX or not path.exists():
            continue
        try:
            if path.stat().st_size > 2_000_000:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for line_number, line in enumerate(text.splitlines(), 1):
            for pattern, label in PATTERNS:
                if re.search(pattern, line):
                    problems.append("%s:%d  %s" % (name, line_number, label))
    return problems


def scan_history():
    problems = []
    for pattern, label in PATTERNS:
        output = _run(["git", "log", "--all", "-G", pattern, "--oneline"])
        for line in output.splitlines():
            if line.strip():
                problems.append("历史提交 %s  %s" % (line.strip()[:60], label))
    return problems


def main() -> int:
    print("=" * 62)
    print("密钥自检：扫描受版本控制的文件和全部 git 历史")
    print("=" * 62)
    problems = scan_tracked_files() + scan_history()
    if problems:
        print("发现可疑内容（请立刻处理，不要把密钥提交上去）：")
        for item in sorted(set(problems)):
            print("  [!] %s" % item)
        print("\n如果确实误提交了：删掉文件并 commit，然后**重置对应的 Key**（历史里的内容"
              "即使删掉也还能被翻出来）。")
        return 1
    print("干净：受版本控制的文件和全部历史里都没有发现 API Key / Token。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
