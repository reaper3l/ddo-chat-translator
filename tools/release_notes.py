"""改线上发行说明（Gitee）—— 带签名块校验，中文不会写成乱码。

用法
----
    # 看现在线上是什么样、签名块还正不正常
    python tools\\release_notes.py show --tag v3.0.21

    # 用本地文件整篇替换（改中文就用这个，别用 PowerShell）
    python tools\\release_notes.py push --tag v3.0.21 --file ..\\work\\release_v321.md

    # 只在末尾（签名块之前）追加一段说明
    python tools\\release_notes.py push --tag v3.0.21 --append note.md

    # 只复核：线上签名块 + 本地安装包能不能对上
    python tools\\release_notes.py verify --tag v3.0.21

要点
----
* 表单一律按 UTF-8 编码后提交（PowerShell 的 Invoke-RestMethod 读 JSON 时可能按
  Latin-1 解码，写回去中文就坏了 —— 本项目踩过这个坑，签名块里的文件名变成乱码，
  验签直接失败）；
* push 之前先本地校验（有签名块、版本号对得上、包名和 sha256 对得上），
  push 之后再联网复核一遍（程序怎么验，这里就怎么验）；
* 令牌从 Windows 凭据管理器取，不打印、不落盘。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import release_notes, update      # noqa: E402


def _stdout() -> None:
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass


def _token() -> str:
    done = subprocess.run(["git", "credential", "fill"],
                          input="protocol=https\nhost=gitee.com\n\n",
                          capture_output=True, text=True, cwd=str(ROOT))
    for line in (done.stdout or "").splitlines():
        if line.startswith("password="):
            return line[len("password="):]
    raise SystemExit("没拿到 Gitee 令牌：先确认 `git credential fill` 里存了 gitee.com 的凭据")


def _api(path: str) -> str:
    return "https://gitee.com/api/v5/repos/git55236/ddo-chat-translator" + path


def _get(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "DDOReleaseNotes"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _patch(url: str, fields: dict) -> dict:
    data = urllib.parse.urlencode(fields, encoding="utf-8").encode("ascii")
    request = urllib.request.Request(url, data=data, method="PATCH")
    request.add_header("Content-Type",
                       "application/x-www-form-urlencoded; charset=utf-8")
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def _release(tag: str, token: str) -> dict:
    return _get(_api("/releases/tags/%s?access_token=%s" % (tag, token)))


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8").strip()


def _report(text: str, tag: str, token: str, package: Optional[Path]) -> bool:
    """照程序自己的逻辑复核一遍（返回是否通过）。"""
    ok, reason = release_notes.validate(text, version=tag, package=package)
    print("  签名块校验：%s %s" % ("√" if ok else "×", reason))
    if not ok:
        return False
    if package is None:
        print("  （没找到本地安装包，跳过 Ed25519 验签；用 --asset 指定）")
        return True
    info = update.UpdateInfo(version=str(tag).lstrip("vV"), tag=tag, notes=text,
                             asset_name=package.name)
    ok, reason = update.verify_package(package, info)
    print("  完整验签　：%s %s" % ("√" if ok else "×", reason))
    return ok


def cmd_show(args) -> int:
    token = _token()
    release = _release(args.tag, token)
    body = str(release.get("body") or "")
    package = Path(args.asset) if args.asset else release_notes.find_package(args.tag)
    print("=" * 66)
    print("发行版 %s（id=%s）" % (args.tag, release.get("id")))
    print("说明字符数：%d" % len(body))
    print("=" * 66)
    print(body[:args.head] + ("\n…（后面还有 %d 字）" % (len(body) - args.head)
                             if len(body) > args.head else ""))
    print("-" * 66)
    return 0 if _report(body, args.tag, token, package) else 1


def cmd_push(args) -> int:
    token = _token()
    release = _release(args.tag, token)
    current = str(release.get("body") or "")
    if args.file:
        body = _read(args.file)
        print("用 %s 整篇替换发行说明" % args.file)
    else:
        body = current
        print("保留现有发行说明，只在签名块之前插入说明")
    if args.append:
        body = release_notes.merge_body(body, _read(args.append))
        print("追加：%s" % args.append)
    if update.SIGNATURE_MARK not in body:
        raise SystemExit("合并后的发行说明里没有签名块 —— 拒绝提交（这会让所有用户升不了级）")

    package = Path(args.asset) if args.asset else release_notes.find_package(args.tag)
    print("[1/3] 本地校验")
    if not _report(body, args.tag, token, package):
        raise SystemExit("本地校验没过，没动线上内容")
    if args.dry_run:
        print("[dry-run] 校验通过，未提交。合并后的正文如下：\n")
        print(body)
        return 0

    print("[2/3] 写回 Gitee（UTF-8 表单）")
    name = args.name or str(release.get("name") or "")
    updated = _patch(_api("/releases/%s" % release.get("id")),
                     {"access_token": token, "tag_name": args.tag, "name": name,
                      "body": body})
    print("  已更新，返回说明字符数：%d" % len(str(updated.get("body") or "")))

    print("[3/3] 联网复核（拿刚写回去的内容，按程序的方式验）")
    fresh = str(_release(args.tag, token).get("body") or "")
    ok = _report(fresh, args.tag, token, package)
    if not ok:
        print("\n！复核没过 —— 请立刻用 --file 重新提交正确的说明（附件没动过，别慌）")
        return 1
    print("\n完成：线上说明已更新，签名块和安装包仍然对得上。")
    return 0


def cmd_verify(args) -> int:
    token = _token()
    body = str(_release(args.tag, token).get("body") or "")
    package = Path(args.asset) if args.asset else release_notes.find_package(args.tag)
    print("复核发行版 %s（说明 %d 字符，安装包 %s）"
          % (args.tag, len(body), package.name if package else "未找到"))
    return 0 if _report(body, args.tag, token, package) else 1


def main() -> int:
    _stdout()
    parser = argparse.ArgumentParser(description="改 / 复核 Gitee 发行说明（带签名块校验）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_show = sub.add_parser("show", help="看线上说明 + 校验签名块")
    p_show.add_argument("--tag", required=True)
    p_show.add_argument("--head", type=int, default=800, help="打印前多少个字符")
    p_show.add_argument("--asset", help="本地安装包（默认自动在 dist 里找）")
    p_show.set_defaults(func=cmd_show)

    p_push = sub.add_parser("push", help="提交新的发行说明（--file 换整篇 / --append 追加）")
    p_push.add_argument("--tag", required=True)
    p_push.add_argument("--file", help="用这个文件整篇替换发行说明")
    p_push.add_argument("--append", help="只追加这个文件的内容（插在签名块之前）")
    p_push.add_argument("--name", help="发行版标题（默认沿用现在的）")
    p_push.add_argument("--asset", help="本地安装包（默认自动在 dist 里找）")
    p_push.add_argument("--dry-run", action="store_true", help="只校验和打印，不提交")
    p_push.set_defaults(func=cmd_push)

    p_verify = sub.add_parser("verify", help="只复核线上签名块 + 本地安装包")
    p_verify.add_argument("--tag", required=True)
    p_verify.add_argument("--asset", help="本地安装包（默认自动在 dist 里找）")
    p_verify.set_defaults(func=cmd_verify)

    args = parser.parse_args()
    if args.command == "push" and not args.dry_run:
        if not getattr(args, "file", None) and not getattr(args, "append", None):
            raise SystemExit("push 需要 --file 或 --append"
                             "（只想校验不提交请加 --dry-run）")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
