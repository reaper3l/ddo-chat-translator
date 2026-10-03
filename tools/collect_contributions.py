"""把收到的贡献聚合成"可以进公共词典的候选词表"，并给出人扫一眼就够的报告。

三种收件方式（可以一起用）：

    # 1) 收件箱目录：里面 .txt/.md/.json 文件里夹着的贡献码（DDO1:...）都会被解出来
    python tools\\collect_contributions.py --inbox 收件箱 --out 候选

    # 2) 收件端导出的 JSON（Cloudflare Worker / tools\\contribute_server.py）
    python tools\\collect_contributions.py --json 贡献.json --out 候选

    # 3) 直接从收件端拉（需要作者本机的口令）
    python tools\\collect_contributions.py --url https://xxx.workers.dev --token <口令> --out 候选

输出（默认写到 `候选\\`）：

    候选术语.json    ← 直接喂给 build_public_dict.py --add
    候选句子.json    ← 只供参考（整句不自动进公共库）
    报告.md          ← 新增/被拒的原因、报警项

**自动闸门**全在 `app/contribute.py: aggregate()` 里：至少 N 个不同的人独立同意、
一致率够高、单人占比不过线、格式与广告特征过滤。有报警时退出码是 3，
方便挂到定时任务里："等于 3 就停下来等人看一眼，其它情况直接推"。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import contribute, paths      # noqa: E402

TEXT_SUFFIX = {".txt", ".md", ".json", ".log", ".csv"}


def _load_json_records(path: Path) -> list:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:                       # noqa: BLE001
        print("读不了 %s：%s" % (path, exc))
        return []
    if isinstance(data, dict):
        data = data.get("records") or data.get("data") or [data]
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]


def _scan_inbox(folder: Path) -> list:
    """把目录里所有文本文件里的贡献码都解出来。"""
    records = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIX:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        found = contribute.find_codes(text)
        if found:
            print("  %s：%d 份" % (path.name, len(found)))
            records.extend(found)
    return records


def _fetch(url: str, token: str) -> list:
    request = urllib.request.Request(
        "%s%s%s" % (url.rstrip("/"), "/?token=", token),
        headers={"User-Agent": "DDOTranslator-collect"})
    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.loads(response.read().decode("utf-8"))
    return data if isinstance(data, list) else []


def _known_terms() -> list:
    """已经在用的词（内置精选表 + 当前公共词典），候选中要排除。"""
    known = set()
    for path in (paths.GLOSSARY_PATH,
                 Path(ROOT) / "dictionary" / "public.json"):
        data = paths.read_json(path, {}) or {}
        terms = data.get("terms", {}) if isinstance(data, dict) else {}
        if isinstance(terms, dict):
            known.update(str(k).strip().lower() for k in terms)
    return sorted(known)


def main() -> int:
    parser = argparse.ArgumentParser(description="聚合公共词典贡献")
    parser.add_argument("--inbox", help="贡献码所在目录（或单个文件）")
    parser.add_argument("--json", help="收件端导出的 JSON")
    parser.add_argument("--url", help="收件端地址（配合 --token）")
    parser.add_argument("--token", default="", help="收件端导出口令（只在你本机用）")
    parser.add_argument("--token-file",
                        help="从文件里读口令（推荐：口令就不会出现在命令行/历史记录里）")
    parser.add_argument("--out", default="候选", help="输出目录")
    parser.add_argument("--min-users", type=int, default=0, help="覆盖默认门槛：不同用户数")
    parser.add_argument("--min-count", type=int, default=0, help="覆盖默认门槛：出现次数")
    args = parser.parse_args()
    if args.token_file and not args.token:
        try:
            lines = [line.strip() for line
                     in Path(args.token_file).read_text(encoding="utf-8").splitlines()]
            args.token = [line for line in lines if line and ":" not in line
                          and "：" not in line][-1]
        except Exception as exc:                   # noqa: BLE001
            print("读口令文件失败：%s" % exc)
            return 2

    records: list = []
    if args.json:
        records += _load_json_records(Path(args.json))
    if args.inbox:
        target = Path(args.inbox)
        if target.is_dir():
            print("扫描收件箱：%s" % target)
            records += _scan_inbox(target)
        else:
            records += contribute.find_codes(
                target.read_text(encoding="utf-8", errors="replace"))
    if args.url:
        if not args.token:
            print("--url 需要同时给 --token")
            return 2
        try:
            records += _fetch(args.url, args.token)
        except Exception as exc:                   # noqa: BLE001
            print("从收件端拉取失败：%s" % exc)
            return 2

    if not records:
        print("没有收到任何贡献（检查 --inbox / --json / --url）")
        return 1
    print("共收到 %d 份贡献" % len(records))

    gates = {}
    if args.min_users:
        gates["min_users"] = args.min_users
    if args.min_count:
        gates["min_count"] = args.min_count
    result = contribute.aggregate(records, gates=gates, known_terms=_known_terms())

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "候选术语.json").write_text(
        json.dumps({"terms": result["terms"]}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    (out_dir / "候选句子.json").write_text(
        json.dumps({"phrases": result["phrases"]}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    report = contribute.report_text(result, gates=gates)
    (out_dir / "报告.md").write_text(report, encoding="utf-8")
    retire = result.get("retire") or {}
    if retire:
        (out_dir / "建议下架.json").write_text(
            json.dumps({"retire": sorted(retire)}, ensure_ascii=False, indent=2),
            encoding="utf-8")

    print(report)
    print("")
    print("输出目录：%s" % out_dir.resolve())
    if result["terms"]:
        print("下一步（想发布的话）：")
        print("  python tools\\build_public_dict.py --add \"%s\""
              % (out_dir / "候选术语.json"))
        print("  powershell -ExecutionPolicy Bypass -File ..\\work\\push_dict_branch.ps1")
    if retire:
        print("\n有 %d 条词被多个用户改掉，建议下架（已写进 建议下架.json）：" % len(retire))
        print("  python tools\\build_public_dict.py --retire \"%s\""
              % (out_dir / "建议下架.json"))
    if result["alerts"]:
        print("\n有报警项 —— 建议先看一眼报告，确认没问题再推。")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
