"""生成并签名公共词典（dictionary/public.json + .sig）。

用法：
    python tools\\build_public_dict.py                     # 用内置精选表做第一版
    python tools\\build_public_dict.py --add extra.json    # 再并进一份词表（后写的覆盖）
    python tools\\build_public_dict.py --batch 2           # 指定批次号（默认加 1）

为什么单独一个工具：词典要挂在仓库的 `dict` 分支上，和代码历史分开；
每次更新只需要重跑这个脚本 → 推送那两个文件，**不用发版**。

写文件之前会先跑一遍**语料回放**（app/replay.py）：拿 tools\corpus\chat_samples.txt
加上本机记忆库里的真实句子，比对「加词前 / 加词后」的术语匹配。某个新词如果在常见
句子里到处乱改（命中 >= 3 行、且占语料 >= 5%），就**不写文件**并报出来。
确认那些改动没问题，再加 --replay-force 重跑；不想跑就 --no-replay。

签名用的是和安装包同一把发布私钥（%USERPROFILE%\\.ddo-release\\release.key），
私钥不进仓库；验签逻辑在 app/public_dict.py，和客户端共用一份实现。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from app import ed25519, paths, public_dict, replay      # noqa: E402
from sign_release import DEFAULT_KEY_FILE, _read_key   # noqa: E402

DEFAULT_CORPUS = ROOT / "tools" / "corpus" / "chat_samples.txt"


def _load_terms(path: Path) -> dict:
    data = paths.read_json(path, {})
    if not isinstance(data, dict):
        return {}
    terms = data.get("terms", data)
    if not isinstance(terms, dict):
        return {}
    return {str(k): str(v) for k, v in terms.items() if k and v}


def _replay_check(terms: dict, target: Path, args) -> int:
    """上线前拿语料回放一遍。返回 0 = 继续，2 = 停下不写文件。

    比的是"用户手上那一份（现有 public.json）"和"这次要发的"。第一次生成
    public.json 时没有旧版可比 → 跳过（那时是新词库的整体上线，回放没有基准）。
    """
    before = _load_terms(target) if Path(target).exists() else {}
    new_terms = {k: v for k, v in terms.items() if before.get(k) != v}
    if not new_terms:
        print("语料回放：没有新增或改动的词，跳过。")
        return 0

    lines = replay.collect_lines([DEFAULT_CORPUS] + [Path(p) for p in args.corpus],
                                 use_memory=True)
    report = replay.compare(before, new_terms, lines,
                            min_hits=args.replay_min_hits,
                            max_ratio=args.replay_max_ratio)
    print(replay.format_report(report))
    if report.ok:
        return 0
    if args.replay_force:
        print("  （--replay-force 已放行；上面的句子请自己确认没被改坏）")
        return 0
    print("回放不通过 → 没有写文件。确认没问题就加 --replay-force 重跑。")
    return 2


def main() -> int:
    parser = argparse.ArgumentParser(description="生成并签名公共词典")
    parser.add_argument("--out", default=str(ROOT / "dictionary"),
                        help="输出目录（默认仓库里的 dictionary/）")
    parser.add_argument("--add", action="append", default=[],
                        help="额外并入的词表文件（可多次；JSON，形如 {'terms': {...}}）")
    parser.add_argument("--batch", type=int, default=0, help="批次号（默认自动 +1）")
    parser.add_argument("--retire", action="append", default=[],
                        help="要下架的词表文件（聚合脚本产出的「建议下架.json」）")
    parser.add_argument("--rollout", type=int, default=100,
                        help="灰度百分比：新批次先只对这么比例的用户生效（默认 100 = 全量）")
    parser.add_argument("--hold-hours", type=float, default=0,
                        help="灰度观察时长（小时）：窗口结束后所有客户端都会采用（默认 0）")
    parser.add_argument("--key", default=str(DEFAULT_KEY_FILE), help="发布私钥路径")
    parser.add_argument("--no-replay", action="store_true",
                        help="跳过语料回放自检（不建议）")
    parser.add_argument("--replay-force", action="store_true",
                        help="回放判定过度泛化也照发（会打印警告）")
    parser.add_argument("--corpus", action="append", default=[],
                        help="额外语料文件（可多次；纯文本一行一句，或 JSON 数组）")
    parser.add_argument("--replay-min-hits", type=int, default=replay.DEFAULT_MIN_HITS,
                        help="少于这么多行命中就不判过度泛化（默认 %d）"
                             % replay.DEFAULT_MIN_HITS)
    parser.add_argument("--replay-max-ratio", type=float, default=replay.DEFAULT_MAX_RATIO,
                        help="命中比例阈值（默认 %.2f）" % replay.DEFAULT_MAX_RATIO)
    args = parser.parse_args()

    out_dir = Path(args.out)
    target = out_dir / "public.json"
    sig_target = out_dir / "public.json.sig"

    terms = _load_terms(paths.GLOSSARY_PATH)
    if not terms:
        print("读不到内置精选表：%s" % paths.GLOSSARY_PATH)
        return 1
    print("内置精选表：%d 条" % len(terms))
    for extra in args.add:
        incoming = _load_terms(Path(extra))
        print("并入 %s：%d 条（同名的覆盖）" % (extra, len(incoming)))
        terms.update(incoming)

    retired = []
    for path in args.retire:
        data = paths.read_json(Path(path), {}) or {}
        names = data.get("retire") or data.get("terms") or []
        if isinstance(names, dict):
            names = list(names)
        for name in names:
            key = str(name)
            for existing in list(terms):
                if existing.strip().lower() == key.strip().lower():
                    terms.pop(existing)
                    retired.append(key)
                    break
    if retired:
        print("下架 %d 条：%s" % (len(retired), "、".join(retired[:10])))

    old = paths.read_json(target, {}) or {}
    old_batch = int(old.get("batch", 0) or 0)
    batch = args.batch or (old_batch + 1)

    if not args.no_replay:
        code = _replay_check(terms, target, args)
        if code:
            return code

    payload = {
        "version": 1,
        "batch": batch,
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "note": "公共词典：由作者整理/发布。只用于补充内置表里没有的词，不覆盖内置表，"
                "也不会覆盖用户自己的词。",
        "rollout": {"percent": max(0, min(100, int(args.rollout))),
                    "hold_hours": max(0.0, float(args.hold_hours))},
        "terms": dict(sorted(terms.items())),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False)
    data = text.encode("utf-8")

    key_file = Path(args.key)
    if not key_file.exists():
        print("找不到签名私钥：%s" % key_file)
        return 1
    seed = _read_key(key_file)
    digest = hashlib.sha256(data).hexdigest()
    signature = ed25519.sign(public_dict.signature_message(1, digest), seed)

    out_dir.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    sig_text = "\n".join([
        public_dict.SIGNATURE_MARK,
        "version: 1",
        "file: public.json",
        "sha256: %s" % digest,
        "sig: %s" % base64.b64encode(signature).decode("ascii"),
        "",
    ])
    sig_target.write_text(sig_text, encoding="utf-8")

    # 立刻用客户端那套逻辑自检一遍，避免"签出来自己都验不过"
    ok, reason = public_dict.verify(data, sig_text)
    print("写入：%s（%d 字节）、%s" % (target, len(data), sig_target.name))
    print("批次：%d，词条：%d 条，sha256：%s" % (batch, len(terms), digest[:16] + "…"))
    print("自检：%s" % ("验签通过" if ok else "**验签失败：%s**" % reason))
    print("公钥指纹：%s" % pubkey_fingerprint_text())
    return 0 if ok else 1


def pubkey_fingerprint_text() -> str:
    """打印当前配置的公钥指纹（和设置→关于 里显示的一致）。"""
    from app import update

    keys = update.pubkeys()
    return update.pubkey_fingerprint(keys[0]) if keys else "（无）"


if __name__ == "__main__":
    raise SystemExit(main())
