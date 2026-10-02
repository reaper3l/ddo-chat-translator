"""跑全部纯逻辑测试（不需要界面、不需要网络）。

用法：python tests/run_all.py
"""
from __future__ import annotations

import importlib
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for path in (str(ROOT), str(HERE)):
    if path not in sys.path:
        sys.path.insert(0, path)

MODULES = [
    "test_textutil",
    "test_config",
    "test_channels",
    "test_replies",
    "test_disclaimer",
    "test_diagnose",
    "test_release_notes",
    "test_update",
    "test_ed25519",
    "test_parser",
    "test_zh_regression",
    "test_dedup",
    "test_glossary",
    "test_glossary_io",
    "test_prompt",
    "test_store",
    "test_pipeline",
    "test_style",
    "test_capture",
    "test_preprocess",
    "test_ocr_filter",
    "test_frame",
    "test_capture_similar",
]


def main() -> int:
    total = 0
    passed = 0
    failed = []

    for name in MODULES:
        module = importlib.import_module(name)
        print("\n[%s]" % name)
        for attribute in sorted(dir(module)):
            if not attribute.startswith("test_"):
                continue
            function = getattr(module, attribute)
            if not callable(function):
                continue
            total += 1
            try:
                function()
                passed += 1
                print("  ok   %s" % attribute)
            except Exception as exc:
                failed.append((name, attribute, exc))
                print("  FAIL %s -> %s: %s" % (attribute, type(exc).__name__, exc))
                traceback.print_exc(limit=3)

    print("\n" + "=" * 56)
    print("通过 %d/%d" % (passed, total))
    if failed:
        print("失败清单：")
        for name, attribute, exc in failed:
            print("  - %s.%s: %s" % (name, attribute, exc))
    print("=" * 56)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
