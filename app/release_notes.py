"""发行说明的合并与校验（纯逻辑，方便单测；联网那部分在 tools/release_notes.py）。

为什么要有它：发行说明里带着**发布包签名块**（版本号 / 文件名 / sha256 / 签名），
程序自动升级时靠它验签。改说明的时候很容易出事：

* 手抖把签名块删了/改坏了 → 所有用户都装不了更新（程序验签失败）；
* 重打了包却没改签名块里的 sha256 → 用户下载后校验不过；
* 用 PowerShell 改中文 → Invoke-RestMethod 读 JSON 时可能按 Latin-1 解码，
  写回去就变乱码（本项目真踩过：签名块里的文件名变成 `DDOç¿»è¯…`，验签立刻失败）。

所以：改之前先本地校验、改之后再联网复核，全走这个模块。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

from . import update

ROOT = Path(__file__).resolve().parent.parent


def root() -> Path:
    return ROOT


def merge_body(body: str, extra: str = "") -> str:
    """把额外说明插到**签名块之前** —— 签名块始终留在最末尾（发布规范如此）。

    `extra` 为空就只规范化一下末尾换行；正文里没有签名块时，extra 直接追加到末尾。
    """
    body = (body or "").rstrip()
    extra = (extra or "").strip()
    mark = update.SIGNATURE_MARK
    if not extra:
        return body + "\n"
    if extra in body:                 # 已经加过了，别重复插入
        return body + "\n"
    if mark in body:
        head, _, tail = body.partition(mark)
        return "%s\n\n%s\n\n%s%s\n" % (head.rstrip(), extra, mark, tail)
    return "%s\n\n%s\n" % (body, extra)


def validate(text: str, version: str = "", package: Optional[Path] = None,
             check_digest: bool = True) -> Tuple[bool, str]:
    """检查一段发行说明能不能被程序接受（返回 (是否通过, 说明)）。

    * 必须有签名块，且四行齐全；
    * 传了 `version` 就核对签名块里的版本号；
    * 传了 `package`（本地安装包）就核对文件名，以及**文件真实的 sha256**
      （`check_digest=False` 可以只比文件名，用于包还没打出来的场合）。
    """
    parsed = update.parse_signature(text or "")
    if not parsed:
        return False, ("没有签名块（或格式不对）—— 发布说明这样发出去，"
                       "程序会拒绝安装，所有用户都升不了级")
    if version:
        left = parsed["version"].lstrip("vV")
        right = str(version).lstrip("vV")
        if not right:
            return False, "版本号是空的"
        if left != right:
            return False, "签名里的版本号（%s）和这次发布（%s）对不上" % (
                parsed["version"], right)
    if package is not None:
        package = Path(package)
        if parsed["file"] != package.name:
            return False, "签名里的文件名（%s）和本地安装包（%s）对不上" % (
                parsed["file"], package.name)
        if check_digest:
            if not package.exists():
                return False, "找不到本地安装包：%s" % package
            digest = update.sha256_file(package)
            if digest.lower() != parsed["sha256"].strip().lower():
                return False, ("本地安装包的 sha256（%s…）和签名块里的（%s…）对不上 —— "
                               "是不是重打了包没重新签名？"
                               % (digest[:12], parsed["sha256"][:12]))
    return True, "签名块正常（sha256 %.12s…）" % parsed["sha256"]


def find_package(tag: str, base: Optional[Path] = None) -> Optional[Path]:
    """按 tag（v3.0.21）在 dist\\ 里找对应的安装包。"""
    version = str(tag or "").lstrip("vV")
    if not version:
        return None
    folder = Path(base) if base else ROOT / "dist"
    if not folder.is_dir():
        return None
    matches = sorted(folder.glob("DDO*_v%s.zip" % version))
    return matches[-1] if matches else None
