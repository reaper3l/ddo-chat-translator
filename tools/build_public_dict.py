"""生成并签名公共词典（dictionary/public.json + .sig）。

用法：
    python tools\\build_public_dict.py                     # 用内置精选表做第一版
    python tools\\build_public_dict.py --add extra.json    # 再并进一份词表（后写的覆盖）
    python tools\\build_public_dict.py --batch 2           # 指定批次号（默认加 1）

为什么单独一个工具：词典要挂在仓库的 `dict` 分支上，和代码历史分开；
每次更新只需要重跑这个脚本 → 推送那两个文件，**不用发版**。

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

from app import ed25519, paths, public_dict      # noqa: E402
from sign_release import DEFAULT_KEY_FILE, _read_key   # noqa: E402


def _load_terms(path: Path) -> dict:
    data = paths.read_json(path, {})
    if not isinstance(data, dict):
        return {}
    terms = data.get("terms", data)
    if not isinstance(terms, dict):
        return {}
    return {str(k): str(v) for k, v in terms.items() if k and v}


def main() -> int:
    parser = argparse.ArgumentParser(description="生成并签名公共词典")
    parser.add_argument("--out", default=str(ROOT / "dictionary"),
                        help="输出目录（默认仓库里的 dictionary/）")
    parser.add_argument("--add", action="append", default=[],
                        help="额外并入的词表文件（可多次；JSON，形如 {'terms': {...}}）")
    parser.add_argument("--batch", type=int, default=0, help="批次号（默认自动 +1）")
    parser.add_argument("--key", default=str(DEFAULT_KEY_FILE), help="发布私钥路径")
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

    old = paths.read_json(target, {}) or {}
    old_batch = int(old.get("batch", 0) or 0)
    batch = args.batch or (old_batch + 1)

    payload = {
        "version": 1,
        "batch": batch,
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "note": "公共词典：由作者整理/发布。只用于补充内置表里没有的词，不覆盖内置表，"
                "也不会覆盖用户自己的词。",
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
