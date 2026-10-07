"""发版前腾空间：Gitee 发行版附件的**总配额只有 1GB**，快满时自动删最旧的附件。

为什么需要它：每版安装包约 95~98MB，Gitee 的发行版附件是按仓库累计算的 ——
发到第 11 个版本左右就会顶到 1GB，上传直接被拒（v3.0.34 发版时就是这么卡住的）。

用法（在仓库目录下）：

    python tools\\prune_gitee_releases.py                  # 只看情况，不删（默认）
    python tools\\prune_gitee_releases.py --apply          # 需要放得下"最新那个 dist 包"就删到放得下
    python tools\\prune_gitee_releases.py --need 98 --apply
    python tools\\prune_gitee_releases.py --keep 4 --apply  # 至少保留最新 4 个版本

规则（都是为了让"自动更新"永远不受影响）：
  * **只从最旧的开始删**，而且**永远不动最新 keep 个版本**（默认 6）——
    自动更新只认最新版那个包，老版本的手动下载链接失效不影响任何人升级；
  * 可以 `--skip v3.0.30` 指定"这一版也别动"（比如正在补发的那个版本）；
  * 删之前逐条打印"删了什么、省了多少、还剩多少"，`--apply` 才会真的删；
  * **不动 GitHub 镜像**（那边不占这个配额，历代版本在镜像上一直能下）。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

REPO = "git55236/ddo-chat-translator"
API = "https://gitee.com/api/v5/repos/%s/releases" % REPO
QUOTA_MB = 1024.0            # Gitee 发行版附件总配额（超出会报"文件大小已超出仓库附件配额：1 GB"）
RESERVE_MB = 24.0            # 至少留这么多余量，别贴着上限
DEFAULT_KEEP = 6             # 最近这么多版本不删（自动更新只认最新版，老包删了不影响升级）


def _token() -> str:
    """从 git 凭据里取 Gitee 口令（和发版脚本用的是同一份，不落到命令行/日志里）。"""
    try:
        out = subprocess.run(["git", "credential", "fill"],
                             input="protocol=https\nhost=gitee.com\n\n",
                             capture_output=True, text=True, timeout=30).stdout
    except Exception as exc:                    # noqa: BLE001
        raise SystemExit("取 Gitee 凭据失败：%s" % exc)
    for line in out.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1]
    raise SystemExit("git 凭据里没有 gitee.com 的口令（先在别处 pull/push 一次让它记住）")


def _get(url: str, token: str):
    request = urllib.request.Request(
        url, headers={"User-Agent": "DDO-prune", "Authorization": "token " + token})
    with urllib.request.urlopen(request, timeout=60) as response:
        body = response.read().decode("utf-8", "replace")
    return json.loads(body) if body.strip() else {}


def _delete(url: str, token: str):
    request = urllib.request.Request(
        url, headers={"User-Agent": "DDO-prune", "Authorization": "token " + token},
        method="DELETE")
    with urllib.request.urlopen(request, timeout=60) as response:
        response.read()


def _newest_local_package_mb() -> float:
    """dist 里最新那个安装包有多大（默认按它算"需要多少空间"）。"""
    try:
        dist = ROOT / "dist"
        zips = sorted(dist.glob("DDO*_v*.zip"), key=lambda p: p.stat().st_mtime)
        if zips:
            return zips[-1].stat().st_size / 1048576.0
    except Exception:                           # noqa: BLE001
        pass
    return 100.0


def main() -> int:
    parser = argparse.ArgumentParser(description="Gitee 发行版附件腾空间")
    parser.add_argument("--need", type=float, default=None,
                        help="这次要放进去多大（MB），默认取 dist 里最新的包")
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP,
                        help="最近这么多版本不动（默认 %d）" % DEFAULT_KEEP)
    parser.add_argument("--quota-mb", type=float, default=QUOTA_MB)
    parser.add_argument("--skip", action="append", default=[],
                        help="指定不要动的 tag（可多次）")
    parser.add_argument("--apply", action="store_true", help="真的删（不加只预览）")
    args = parser.parse_args()

    token = _token()
    releases = _get("%s?access_token=%s&per_page=100" % (API, token), token)
    if not isinstance(releases, list):
        print("没取到发行版列表：", releases)
        return 1

    # 每个版本的附件和大小（Gitee 返回的顺序不保证，按创建时间排）
    rows = []
    for release in sorted(releases, key=lambda item: str(item.get("created_at") or "")):
        files = _get("%s/%s/attach_files?access_token=%s" % (API, release["id"], token),
                     token)
        size = sum(int(f.get("size") or 0) for f in (files or []))
        rows.append({"tag": release["tag_name"], "id": release["id"],
                     "files": files or [], "mb": size / 1048576.0})

    total = sum(row["mb"] for row in rows)
    need = float(args.need if args.need is not None else _newest_local_package_mb())
    keep = max(0, int(args.keep))
    print("Gitee 发行版附件：共 %d 个版本、%.1f MB / %.0f MB"
          % (len(rows), total, args.quota_mb))
    print("这次要放进去：%.1f MB；上限留 %.0f MB 余量；最近 %d 个版本不动"
          % (need, RESERVE_MB, keep))
    print("-" * 58)
    for row in rows:
        print("  %-9s %8.1f MB  %d 个附件" % (row["tag"], row["mb"], len(row["files"])))
    print("-" * 58)

    room = args.quota_mb - RESERVE_MB - total
    if room >= need:
        print("够放得下（还余 %.1f MB），不需要删任何东西。" % (room - need))
        return 0

    protected = {row["tag"] for row in rows[-keep:]} | set(args.skip or [])
    freed = 0.0
    plan = []
    for row in rows:                            # 从最旧的开始挑
        if room + freed >= need:
            break
        if row["tag"] in protected or not row["files"]:
            continue
        plan.append(row)
        freed += row["mb"]

    if room + freed < need:
        print("！！把能删的都删了还是放不下：还需要 %.1f MB —— "
              "可以调小 --keep，或把包做小一点" % (need - room - freed))
    if not plan:
        print("没有可删的（都被 --keep / --skip 保护着）。")
        return 1

    for row in plan:
        for item in row["files"]:
            print("%s %s 的附件 %s（%.1f MB）"
                  % ("删除" if args.apply else "准备删除", row["tag"],
                     item["name"], int(item.get("size") or 0) / 1048576.0))
            if args.apply:
                _delete("%s/%s/attach_files/%s?access_token=%s"
                        % (API, row["id"], item["id"], token), token)
    print("-" * 58)
    print("%s共腾出 %.1f MB；新的占用约 %.1f MB / %.0f MB"
          % ("已" if args.apply else "（预览，未真删）", freed, total - freed + need,
             args.quota_mb))
    if not args.apply:
        print("确认没问题就加 --apply 再跑一次。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
