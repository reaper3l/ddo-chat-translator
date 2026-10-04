"""贡献总览（作者侧）：现在有多少人在维护公共词典、攒到什么程度。

用法：
    python tools\\contribute_stats.py --url https://ddo-contrib.ddo-tools.workers.dev/ ^
        --token-file ..\\work\\contribute_token.txt
    python tools\\contribute_stats.py --json 贡献.json          # 收件端导出的 JSON
    python tools\\contribute_stats.py --inbox ..\\work\\收件箱    # 一堆贡献码

回答三个问题：
* **有多少人在维护**：收到的份数 + 不同的匿名标识数（匿名标识是哈希，认不出是谁）；
* **各人给了多少 / 什么时候给的**：按人、按天的分布；
* **离入库还差多少**：每个词还差几个"不同的人"、还差几次，够门槛的会单列出来。

口径：客户端发过的条目不会再发（本机按内容哈希去重），所以这里是"每个人新确认过的
内容"的累计，不是他确认过的总数。数据只有作者能看（收件端要口令）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from app import contribute                      # noqa: E402
from collect_contributions import (_fetch, _load_json_records,   # noqa: E402
                                   _scan_inbox)


def _short(uid: str) -> str:
    """匿名标识只显示前 8 位 —— 够分辨是哪一份，又不会把整串贴出去。"""
    uid = str(uid or "?")
    return (uid[:8] + "…") if len(uid) > 8 else uid


def render(data: dict) -> str:
    """把 overview() 的结果排成人扫一眼就够的报告。"""
    out: List[str] = []
    out.append("公共词典 · 贡献总览")
    out.append("=" * 52)
    out.append("收到 %d 份贡献，来自 %d 个不同的匿名标识（= 多少人在维护）"
               % (data.get("records", 0), data.get("users", 0)))
    days = data.get("by_day") or {}
    if days:
        span = ("%s → %s" % (list(days)[0], list(days)[-1])
                if len(days) > 1 else list(days)[0])
        out.append("日期跨度：%s" % span)
        out.append("按天：" + "、".join("%s 共 %d 份" % (day, count)
                                        for day, count in days.items()))
    else:
        out.append("日期跨度：（这条记录的 payload 里没写日期）")
    out.append("")

    out.append("【贡献者】名称只显示前 8 位（匿名标识是哈希，认不出是谁）")
    users = data.get("by_user") or {}
    if not users:
        out.append("  （还没有人贡献过）")
    for uid, info in sorted(users.items(),
                            key=lambda kv: -(kv[1]["terms"] + kv[1]["phrases"])):
        out.append("  %-10s %d 份 · 术语 %d 条 · 句子 %d 条 · 否定票 %d 条%s"
                   % (_short(uid), info["records"], info["terms"], info["phrases"],
                      info["negatives"],
                      ("  （%s ~ %s）" % (info["first"], info["last"])
                       if info["first"] and info["first"] != info["last"]
                       else ("  （%s）" % info["first"] if info["first"] else ""))))
    out.append("")

    gate = data.get("gates") or contribute.DEFAULT_GATES
    out.append("【够门槛、可以进公共词典的】%d 条" % len(data.get("terms") or {}))
    for term, zh in sorted((data.get("terms") or {}).items()):
        out.append("  %s = %s" % (term, zh))
    if not data.get("terms"):
        out.append("  （无）")
    out.append("")

    gaps = data.get("gaps") or []
    if gaps:
        out.append("【离门槛还差多少】门槛：不同用户 ≥%d、出现 ≥%d 次"
                   % (gate["min_users"], gate["min_count"]))
        for item in gaps[:15]:
            note = "（内置表里已有，不会重复收）" if item["known"] else ""
            out.append("  %-14s 现有 %d 人 / %d 次，还差 %d 人、%d 次 %s"
                       % (item["term"], item["users"], item["count"],
                          item["need_users"], item["need_count"], note))
        if len(gaps) > 15:
            out.append("  …（还有 %d 条，完整名单见 collect_contributions 的报告）"
                       % (len(gaps) - 15))
        out.append("")

    retire = data.get("retire") or {}
    if retire:
        out.append("【建议下架】")
        for term, users in sorted(retire.items(), key=lambda kv: -kv[1]):
            out.append("  %s（%d 个不同的人改掉了它）" % (term, users))
        out.append("")

    alerts = data.get("alerts") or []
    if alerts:
        out.append("【提醒】")
        for item in alerts:
            out.append("  · %s" % item)
        out.append("  （人少的时候「单个贡献者占比高」是正常的，不用紧张；"
                   "等参与的人多了这一条自然会消失）")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="公共词典·贡献总览（作者侧）")
    parser.add_argument("--url", help="收件端地址（配合 --token / --token-file）")
    parser.add_argument("--token", default="", help="收件端口令（不写进命令行更好）")
    parser.add_argument("--token-file", default=str(ROOT.parent / "work"
                                                   / "contribute_token.txt"))
    parser.add_argument("--json", help="收件端导出的 JSON 文件")
    parser.add_argument("--inbox", help="贡献码所在目录（或单个文件）")
    args = parser.parse_args()

    records: List[dict] = []
    if args.json:
        records += _load_json_records(Path(args.json))
    if args.inbox:
        target = Path(args.inbox)
        if target.is_dir():
            records += _scan_inbox(target)
        else:
            records += contribute.find_codes(
                target.read_text(encoding="utf-8", errors="replace"))
    if args.url:
        token = args.token
        if not token:
            try:
                lines = [line.strip() for line in
                         Path(args.token_file).read_text(encoding="utf-8").splitlines()]
                token = [line for line in lines if line and ":" not in line][-1]
            except Exception as exc:               # noqa: BLE001
                print("读口令文件失败：%s" % exc)
                return 2
        try:
            records += _fetch(args.url, token)
        except Exception as exc:                   # noqa: BLE001
            print("从收件端拉取失败：%s" % exc)
            return 2
    if not records:
        print("没有收到任何贡献（检查 --url / --json / --inbox）")
        return 1
    print(render(contribute.overview(records)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
