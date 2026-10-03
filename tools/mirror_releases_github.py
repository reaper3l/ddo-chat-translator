"""把 Gitee 上的发行版镜像到 GitHub（同一个 zip、同一个 sha256、签名块原样保留）。

用法：
    python tools\\mirror_releases_github.py v3.0.27 v3.0.28 v3.0.29
    python tools\\mirror_releases_github.py --all-recent      # 自动挑最近几个有附件的

做四件事：
 1. 从 Gitee 读发行版的**名字和说明**（说明里带作者签名块，必须原样搬过去）；
 2. 找到对应的 zip：优先用本地 `dist\\DDO翻译助手_vX.Y.Z.zip`，没有就从 Gitee 下载；
 3. **先验证**：zip 的 sha256 必须和签名块里写的一致（不一致就停下，绝不发出去）；
 4. 在 GitHub 建同名发行版并上传这个 zip。

token 从 `work\\github_token.txt` 读（工作目录参数 `--token-file`），不打印明文。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import update          # noqa: E402

GITEE_API = "https://gitee.com/api/v5/repos/git55236/ddo-chat-translator"
GH_API = "https://api.github.com/repos/reaper3l/ddo-chat-translator"
UA = {"User-Agent": "DDOTranslator-mirror", "Accept": "application/vnd.github+json"}
ZIP_DIR = ROOT / "dist"
CACHE_DIR = ROOT.parent / "work" / "镜像缓存"


def _json(url: str, token: str = "", method: str = "GET", payload=None, extra=None):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload else None
    headers = dict(UA)
    if token:
        headers["Authorization"] = "Bearer " + token
    if data:
        headers["Content-Type"] = "application/json; charset=utf-8"
    if extra:
        headers.update(extra)
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read().decode("utf-8", "replace")
            return response.status, (json.loads(body) if body.strip() else {})
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")[:300]


def _download(url: str, target: Path) -> bool:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": UA["User-Agent"]})
        with urllib.request.urlopen(request, timeout=300) as response, \
                open(target, "wb") as handle:
            while True:
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                handle.write(chunk)
        return True
    except Exception as exc:                       # noqa: BLE001
        print("   下载失败：%s" % exc)
        return False


def _local_zip(tag: str):
    version = tag.lstrip("vV")
    for candidate in (ZIP_DIR / ("DDO翻译助手_v%s.zip" % version),
                      CACHE_DIR / ("DDO翻译助手_v%s.zip" % version)):
        if candidate.exists():
            return candidate
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="镜像发行版到 GitHub")
    parser.add_argument("tags", nargs="*", help="要镜像的标签，例如 v3.0.27 v3.0.28")
    parser.add_argument("--all-recent", action="store_true", help="自动挑最近有附件的发行版")
    parser.add_argument("--token-file", default=str(ROOT.parent / "work" / "github_token.txt"))
    parser.add_argument("--dry-run", action="store_true", help="只检查，不创建发行版")
    args = parser.parse_args()

    token = ""
    try:
        token = Path(args.token_file).read_text(encoding="utf-8").strip()
    except Exception as exc:                        # noqa: BLE001
        print("读不到 GitHub token：%s" % exc)
        return 2

    tags = list(args.tags)
    if args.all_recent or not tags:
        status, releases = _json(GITEE_API + "/releases?per_page=20")
        if status != 200:
            print("读 Gitee 发行版列表失败：%s" % status)
            return 2
        tags = [r["tag_name"] for r in releases if r.get("assets")]
        tags = list(reversed(tags))[:6]
        print("自动挑选：%s" % "、".join(tags))

    print("%-10s %-28s %-10s %s" % ("标签", "安装包", "sha256", "结果"))
    ok_count = 0
    for tag in tags:
        status, release = _json("%s/releases/tags/%s" % (GITEE_API, tag))
        if status != 200 or not isinstance(release, dict):
            print("%-10s 读 Gitee 发行版失败：%s" % (tag, status))
            continue
        body = str(release.get("body") or "")
        if update.SIGNATURE_MARK not in body:
            print("%-10s **说明里没有签名块，跳过（不能镜像没签名的东西）**" % tag)
            continue
        parsed = update.parse_signature(body) or {}
        assets = release.get("assets") or []
        package = None
        for asset in assets:
            name = str(asset.get("name") or "")
            if name.lower().endswith(".zip"):
                package = asset
                break
        if not package:
            print("%-10s 这个发行版没有安装包附件，跳过" % tag)
            continue
        name = str(package["name"])
        local = _local_zip(tag)
        if local is None:
            local = CACHE_DIR / name
            if not local.exists():
                print("%-10s 本地没有，从 Gitee 下载…" % tag)
                if not _download(str(package.get("browser_download_url") or ""), local):
                    continue
        digest = update.sha256_file(local)
        if digest.lower() != str(parsed.get("sha256", "")).strip().lower():
            print("%-10s **sha256 和签名块不一致，跳过！**（本地 %s vs 签名 %s）"
                  % (tag, digest[:12], str(parsed.get("sha256"))[:12]))
            continue
        if args.dry_run:
            print("%-10s %-28s %-10s 校验通过（dry-run，不发布）" % (tag, name, digest[:8]))
            continue

        gh_status, existing = _json("%s/releases/tags/%s" % (GH_API, tag), token)
        payload = {"tag_name": tag, "name": str(release.get("name") or tag),
                   "body": body, "draft": False, "prerelease": False}
        if gh_status == 200 and isinstance(existing, dict):
            gh_status, result = _json("%s/releases/%s" % (GH_API, existing["id"]), token,
                                      method="PATCH", payload=payload)
            action = "更新"
        else:
            gh_status, result = _json(GH_API + "/releases", token, method="POST",
                                      payload=payload)
            action = "新建"
        if gh_status not in (200, 201) or not isinstance(result, dict):
            print("%-10s GitHub %s发行版失败：%s %s" % (tag, action, gh_status, result))
            continue

        release_id = result["id"]
        have = {a.get("name") for a in (result.get("assets") or [])}
        if name in have:
            print("%-10s %-28s %-10s 已存在，跳过上传" % (tag, name, digest[:8]))
            ok_count += 1
            continue
        upload_url = ("https://uploads.github.com/repos/reaper3l/ddo-chat-translator/"
                      "releases/%s/assets?name=%s" % (release_id, urllib.parse.quote(name)))
        headers = dict(UA)
        headers["Authorization"] = "Bearer " + token
        headers["Content-Type"] = "application/zip"
        request = urllib.request.Request(upload_url, data=local.read_bytes(),
                                         method="POST", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=900) as response:
                uploaded = json.loads(response.read().decode("utf-8"))
            print("%-10s %-28s %-10s %s上传成功（%d 字节）"
                  % (tag, name, digest[:8], action, uploaded.get("size", 0)))
            ok_count += 1
        except urllib.error.HTTPError as exc:
            print("%-10s 上传失败：%s %s" % (tag, exc.code, exc.read().decode('utf-8', 'replace')[:160]))

    print("\n完成 %d/%d 个发行版" % (ok_count, len(tags)))
    return 0 if ok_count == len(tags) else 1


if __name__ == "__main__":
    raise SystemExit(main())
