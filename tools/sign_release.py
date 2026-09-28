"""给发行包签名 / 生成签名密钥对。

为什么需要它：自动更新如果只看"从哪下载的"，一旦仓库或账号被人动了手，程序就会
把动了手脚的版本装到用户机器上，而发布页上显示的却是你的账号 —— 你有理说不清。
加上签名之后规则变成：**只有用你的私钥签过的安装包，程序才会自动安装**。

用法
----
    # 1) 生成一对密钥（只做一次）。私钥默认放在 %USERPROFILE%\\.ddo-release\\release.key
    #    —— 它**不进仓库、不进 exe**，请自己备份好（丢了就只能重新生成一对，
    #    并把新公钥填进 app/update.py 后重新打包发布）。
    python tools\\sign_release.py init

    # 2) 每次发布前给安装包签名，并把打印出来的签名块粘到 Gitee 发行说明末尾
    python tools\\sign_release.py sign dist\\DDO翻译助手_v3.0.20.zip --version 3.0.20

    # 3) 想验证某个包（比如别人发给你的）是不是你签的
    python tools\\sign_release.py verify dist\\xxx.zip --pubkey <公钥hex>
"""
from __future__ import annotations

import argparse
import base64
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import ed25519, update  # noqa: E402

DEFAULT_KEY_DIR = Path(os.path.expanduser("~")) / ".ddo-release"
DEFAULT_KEY_FILE = DEFAULT_KEY_DIR / "release.key"


def _read_key(path: Path) -> bytes:
    text = path.read_text(encoding="utf-8").strip()
    # 允许 hex / base64 两种写法
    try:
        data = bytes.fromhex(text)
        if len(data) == 32:
            return data
    except Exception:
        pass
    data = base64.b64decode(text)
    if len(data) != 32:
        raise SystemExit("私钥文件格式不对（应该是 32 字节的 hex 或 base64）")
    return data


def cmd_init(args) -> int:
    key_file = Path(args.key) if args.key else DEFAULT_KEY_FILE
    if key_file.exists() and not args.force:
        raise SystemExit("私钥已经存在：%s\n（确要重新生成加 --force；注意换新公钥后"
                         "要重新打包发布，老版本的 exe 只认老公钥）" % key_file)
    seed = secrets.token_bytes(32)
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_text(seed.hex() + "\n", encoding="utf-8")
    try:
        os.chmod(key_file, 0o600)
    except Exception:
        pass
    public = ed25519.publickey(seed)
    fingerprint = update.pubkey_fingerprint(public.hex())
    print("=" * 70)
    print("私钥已写好（**不要**提交到仓库、不要发给任何人；请自己备份）：")
    print("   ", key_file)
    print("    指纹：%s" % fingerprint)
    print("    备份建议：至少两处离线存放（U 盘 + 密码管理器/抄下来），别放网盘同步目录。")
    print()
    print("公钥（把这一行填到 app/update.py 的 RELEASE_PUBKEY，然后重新打包）：")
    print()
    print('RELEASE_PUBKEY = "%s"' % public.hex())
    print()
    print("=" * 70)
    return 0


def cmd_sign(args) -> int:
    package = Path(args.package)
    if not package.exists():
        raise SystemExit("找不到安装包：%s" % package)
    key_file = Path(args.key) if args.key else DEFAULT_KEY_FILE
    if not key_file.exists():
        raise SystemExit("找不到私钥：%s\n先跑一次：python tools\\sign_release.py init"
                         % key_file)
    seed = _read_key(key_file)
    version = args.version or _guess_version(package.name)
    digest = update.sha256_file(package)
    message = update.signature_message(version, package.name, digest)
    signature = ed25519.sign(message, seed)
    block = update.make_signature_block(version, package.name, digest, signature)
    out = package.with_suffix(package.suffix + ".sig")
    out.write_text(block + "\n", encoding="utf-8")
    public = ed25519.publickey(seed).hex()
    print("已签名：%s" % package.name)
    print("版本  ：%s" % version)
    print("sha256：%s" % digest)
    print("签名文件：%s" % out)
    print()
    print("把下面这几行**整段**粘到 Gitee 发行说明的末尾（程序靠它验签）：")
    print()
    print(block)
    print()
    print("（当前公钥：%s）" % public)
    configured = update.pubkeys()
    if configured and public not in configured:
        print("！注意：这把私钥的公钥不在 app/update.py 的 RELEASE_PUBKEY 里 —— "
              "程序会拒绝这个包。要么换回原来的私钥，要么把这把公钥加进去"
              "（例如追加成第 %d 把）再重新打包发布。" % (len(configured) + 1))
    elif configured:
        which = configured.index(public) + 1
        print("（这把公钥在 app/update.py 里排第 %d 位：%s）"
              % (which, "主密钥" if which == 1 else "备用密钥 #%d" % which))
    return 0


def cmd_verify(args) -> int:
    package = Path(args.package)
    sig_file = Path(args.sig) if args.sig else package.with_suffix(
        package.suffix + ".sig")
    if not sig_file.exists():
        raise SystemExit("找不到签名文件：%s" % sig_file)
    notes = sig_file.read_text(encoding="utf-8")
    info = update.UpdateInfo(version=args.version or _guess_version(package.name),
                             notes=notes, asset_name=package.name)
    ok, reason = update.verify_package(package, info, pubkey=args.pubkey or None)
    # 用 √ / × 而不是 ✓ / ✗：中文 Windows 控制台是 GBK，写不出的字符会直接让脚本
    # 崩在"打印"这一步（UnicodeEncodeError），而 √ × 在 GBK 里都有。
    print(("√ 签名有效：" if ok else "× 验证失败：") + reason)
    return 0 if ok else 1


def _guess_version(filename: str) -> str:
    import re

    match = re.search(r"_v(\d+(?:\.\d+)+)", filename)
    return match.group(1) if match else ""


def main() -> int:
    # 控制台按系统代码页编码（中文 Windows 是 GBK），遇到写不出的字符会中断 —— 
    # 退化成替代字符，至少别让脚本因为"打印一行字"而失败。
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="发行包签名工具")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="生成一对签名密钥（私钥存在用户目录，不进仓库）")
    p_init.add_argument("--key", help="私钥保存路径（默认 %s）" % DEFAULT_KEY_FILE)
    p_init.add_argument("--force", action="store_true", help="已存在也重新生成")
    p_init.set_defaults(func=cmd_init)

    p_sign = sub.add_parser("sign", help="给安装包签名并打印要贴到发行说明的签名块")
    p_sign.add_argument("package", help="安装包路径（zip）")
    p_sign.add_argument("--version", help="版本号（默认从文件名里猜）")
    p_sign.add_argument("--key", help="私钥路径")
    p_sign.set_defaults(func=cmd_sign)

    p_verify = sub.add_parser("verify", help="验证某个包是不是你签的")
    p_verify.add_argument("package", help="安装包路径（zip）")
    p_verify.add_argument("--sig", help="签名文件路径（默认 <包>.sig）")
    p_verify.add_argument("--pubkey", help="公钥 hex（默认用 app/update.py 里那把）")
    p_verify.add_argument("--version", help="版本号（默认从文件名里猜）")
    p_verify.set_defaults(func=cmd_verify)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
