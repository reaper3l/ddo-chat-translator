"""把旧版程序里积累的词典/纠错记录导入新版学习库。

默认从旧项目目录读取：
    D:\\DDO翻译助手项目\\20260926_2.0.8\\ddo-chat-translator-main\\ddo_final_clean

用法：
    python tools/import_old_data.py
    python tools/import_old_data.py --from "D:\\别的路径\\ddo_final_clean"
    python tools/import_old_data.py --merge-extra     # 顺便刷新 assets/glossary_extra.json
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import paths, textutil                 # noqa: E402
from app.store import MemoryStore               # noqa: E402

DEFAULT_OLD_DIR = Path(r"D:\DDO翻译助手项目\20260926_2.0.8\ddo-chat-translator-main\ddo_final_clean")


def import_terms_from(store: MemoryStore, file_path: Path, source: str) -> int:
    data = paths.read_json(file_path, {})
    if not isinstance(data, dict):
        return 0
    count = 0
    for term, translation in data.items():
        if not isinstance(term, str) or not isinstance(translation, str):
            continue
        term = term.strip()
        translation = translation.strip()
        if not term or not translation:
            continue
        store.set_term(term, translation, source=source)
        count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description="导入旧版词典到新版学习库")
    parser.add_argument("--from", dest="source_dir", default=str(DEFAULT_OLD_DIR))
    parser.add_argument("--merge-extra", action="store_true",
                        help="把旧版 ddo_default_dict.json 复制成 assets/glossary_extra.json")
    args = parser.parse_args()

    old_dir = Path(args.source_dir)
    if not old_dir.exists():
        print("找不到旧项目目录：%s" % old_dir)
        print("用 --from 指定旧项目所在目录（包含 custom_dict.json 的那个目录）")
        return 1

    store = MemoryStore()
    total = 0
    for name, source in (("custom_dict.json", "旧版自定义词典"),
                         ("corrections.json", "旧版纠错记录")):
        file_path = old_dir / name
        if not file_path.exists():
            print("跳过（没有这个文件）：%s" % file_path)
            continue
        count = import_terms_from(store, file_path, source)
        total += count
        print("导入 %s：%d 条" % (file_path.name, count))

    if args.merge_extra:
        legacy = old_dir / "ddo_default_dict.json"
        if legacy.exists():
            data = paths.read_json(legacy, {})
            if isinstance(data, dict) and data:
                paths.write_json(paths.GLOSSARY_EXTRA_PATH, data)
                print("已刷新扩展术语表：%s（%d 条）" % (paths.GLOSSARY_EXTRA_PATH, len(data)))

    if total:
        store.flush(force=True)
        print("\n共导入 %d 条，已写入 %s" % (total, paths.MEMORY_PATH))
        print("这些词现在会参与术语保护，也可以在程序的「学习中心 → 已学习」里看到。")
    else:
        print("\n没有导入任何数据。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
