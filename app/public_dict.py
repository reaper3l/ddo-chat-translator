"""公共词典：从网上下发一份**签名过的**术语表，比内置的新就用它。

为什么要它：现在想加一个术语，得走"改文件 → 打包 → 签名 → 上传 95MB → 用户升级"，
太重。改成仓库里一个签名 JSON 之后，用户启动时顺手拉一下（约 100KB），
几小时内就生效，不用发版。

三条硬规则：

1. **必须验签**：词典内容会直接影响所有人的翻译，所以用和安装包同一把发布私钥签名，
   验签不过就整份丢掉（防止仓库或链路被人动手脚）。
2. **只做加法**：公共词典只用于补"内置表里还没有的词"，不覆盖内置精选表，
   更不会覆盖用户自己的词 —— 合并逻辑在 app/glossary.py 里。
3. **永不阻塞**：网络失败就用上次缓存，缓存也没有就用内置表；全程静默、只写日志。

这一步只**下载**，不上传任何东西（贡献回传是后续的事）。
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from . import ed25519, paths, update

# 默认地址：仓库 dict 分支上的词典（和代码历史分开，链接稳定，随时可换镜像）
DEFAULT_URL = ("https://gitee.com/git55236/ddo-chat-translator/"
               "raw/dict/dictionary/public.json")
SIGNATURE_MARK = "---- DDO-DICT-SIGNATURE ----"
USER_AGENT = "DDOTranslator-publicdict (+%s)" % update.HOMEPAGE
REQUEST_TIMEOUT = 8
MAX_BYTES = 2 * 1024 * 1024          # 词典文件不该超过 2MB，超了直接不认
RETRY_AFTER_FAILURE = 30 * 60        # 本次失败后 30 分钟内不再重试

CACHE_PATH = paths.DATA_DIR / "public_glossary.json"
CACHE_SIG_PATH = paths.DATA_DIR / "public_glossary.json.sig"

_terms_cache: Optional[Dict[str, str]] = None
_last_attempt = 0.0


# --------------------------------------------------------------- 签名与校验
def signature_message(version: str, digest: str) -> bytes:
    """签名覆盖的原文。和安装包用同一把私钥，但报文不同（不能互相顶替）。

    把版本号也签进去，防止有人拿一份**旧的**签名词典冒充新版下发。
    """
    return ("ddo-public-dict-v1\n%s\n%s"
            % (str(version).strip(), str(digest).strip().lower())).encode("utf-8")


def parse_signature(text: str) -> Optional[dict]:
    """解析签名文件里的 `key: value` 段，缺字段返回 None。"""
    if not text or SIGNATURE_MARK not in text:
        return None
    data: Dict[str, str] = {}
    for line in text.split(SIGNATURE_MARK, 1)[1].splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        if key.strip() in ("version", "file", "sha256", "sig"):
            data[key.strip()] = value.strip()
    if not all(k in data for k in ("version", "file", "sha256", "sig")):
        return None
    return data


def verify(payload: bytes, sig_text: str,
           pubkey: Optional[str] = None) -> Tuple[bool, str]:
    """校验词典内容。返回 (是否可信, 不可信的原因)。"""
    if not payload:
        return False, "词典内容为空"
    parsed = parse_signature(sig_text)
    if parsed is None:
        return False, "签名文件格式不对（缺少 version/file/sha256/sig）"
    digest = hashlib.sha256(payload).hexdigest()
    if digest.lower() != parsed["sha256"].strip().lower():
        return False, "词典内容的 sha256 和签名里写的不一致（可能被动过）"
    try:
        signature = base64.b64decode(parsed["sig"])
    except Exception:
        return False, "签名不是合法的 base64"
    message = signature_message(parsed["version"], parsed["sha256"])
    for key_hex in update.pubkeys(pubkey):
        try:
            key = bytes.fromhex(key_hex)      # Ed25519 公钥固定 32 字节
        except Exception:
            continue
        if len(key) != 32:
            continue
        try:
            if ed25519.verify(signature, message, key):
                return True, ""
        except Exception:
            continue
    return False, "签名验证没通过（不是作者发布的词典）"


# ------------------------------------------------------------------ 本地缓存
def _write_bytes(path: Path, data: bytes) -> bool:
    """原子写二进制（和 paths.write_json 一个套路：先写临时文件再替换）。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".",
                                        suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return True
    except Exception:
        return False


def _read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except Exception:
        return b""


def cached() -> Tuple[Optional[bytes], str, float]:
    """上次下载并验签通过的词典：(内容, 版本, 缓存时间)；没有就是 (None, "", 0)。"""
    payload = _read_bytes(CACHE_PATH)
    if not payload:
        return None, "", 0.0
    sig_text = _read_bytes(CACHE_SIG_PATH).decode("utf-8", errors="replace")
    ok, reason = verify(payload, sig_text)
    if not ok:
        logging.getLogger("ddo").warning("公共词典缓存不可信，忽略：%s", reason)
        return None, "", 0.0
    parsed = parse_signature(sig_text) or {}
    try:
        when = CACHE_PATH.stat().st_mtime
    except OSError:
        when = 0.0
    return payload, parsed.get("version", ""), when


def load_terms() -> Dict[str, str]:
    """当前可用的公共词条（只读缓存，不联网）。拿不到就返回空字典。"""
    global _terms_cache
    if _terms_cache is not None:
        return _terms_cache
    payload, _version, _when = cached()
    terms: Dict[str, str] = {}
    if payload:
        try:
            data = json.loads(payload.decode("utf-8"))
            raw = data.get("terms", {}) if isinstance(data, dict) else {}
            if isinstance(raw, dict):
                for term, translation in raw.items():
                    if isinstance(term, str) and isinstance(translation, str):
                        terms[term] = translation
        except Exception as exc:
            logging.getLogger("ddo").warning("公共词典解析失败：%s", exc)
            terms = {}
    _terms_cache = terms
    return terms


def invalidate() -> None:
    """丢掉内存里的缓存（词典文件更新后调用）。"""
    global _terms_cache
    _terms_cache = None


# -------------------------------------------------------------------- 联网
def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("词典超过 %d 字节，不认" % MAX_BYTES)
    return data


def status(config: Optional[dict] = None) -> dict:
    """给界面用的一份状态（不联网）。"""
    config = config or {}
    payload, version, when = cached()
    terms = load_terms()
    return {
        "enabled": bool(config.get("public_dict_enabled", True)),
        "has_data": bool(payload),
        "version": version,
        "terms": len(terms),
        "updated_at": when,
        "url": effective_url(config),
        "interval_hours": int(config.get("public_dict_interval_hours", 6) or 6),
    }


def effective_url(config: Optional[dict] = None) -> str:
    """实际使用的地址：配置里填了就用配置的（方便换镜像），否则用内置默认。"""
    config = config or {}
    return str(config.get("public_dict_url") or "").strip() or DEFAULT_URL


def needs_sync(config: dict, now: Optional[float] = None) -> bool:
    """距上次成功更新够久了没（失败后 30 分钟内不再试）。"""
    if not config.get("public_dict_enabled", True):
        return False
    now = time.time() if now is None else now
    if now - _last_attempt < RETRY_AFTER_FAILURE and not cached()[0]:
        return False
    _payload, _version, when = cached()
    hours = float(config.get("public_dict_interval_hours", 6) or 6)
    return (now - when) >= hours * 3600


def sync(config: Optional[dict] = None,
         fetcher: Optional[Callable[[str], bytes]] = None,
         force: bool = False,
         pubkey: Optional[str] = None) -> dict:
    """拉一次公共词典并更新缓存。

    fetcher / pubkey 可以注入（测试用）；返回一份状态字典，绝不抛异常。
    """
    global _last_attempt
    config = config or {}
    url = effective_url(config)
    out = status(config)
    out.update({"ok": False, "updated": False, "reason": ""})
    if not config.get("public_dict_enabled", True):
        out["reason"] = "设置里关掉了公共词典"
        return out
    _last_attempt = time.time()
    if not force and not needs_sync(config):
        out["ok"] = True
        out["reason"] = "还没到下次检查时间"
        return out
    fetch = fetcher or _fetch
    try:
        payload = fetch(url)
        sig_text = fetch(url + ".sig").decode("utf-8", errors="replace")
    except Exception as exc:
        out["reason"] = "下载失败：%s" % exc
        logging.getLogger("ddo").info("公共词典下载失败（用缓存/内置）：%s", exc)
        return out
    ok, reason = verify(payload, sig_text, pubkey=pubkey)
    if not ok:
        out["reason"] = reason
        logging.getLogger("ddo").warning("公共词典验签失败，丢弃：%s", reason)
        return out
    old_terms = cached()[0]
    if old_terms == payload:
        out.update({"ok": True, "reason": "已经是最新的"})
        return out
    if not (_write_bytes(CACHE_PATH, payload) and _write_bytes(CACHE_SIG_PATH,
                                                              sig_text.encode("utf-8"))):
        out["reason"] = "写缓存失败（磁盘问题？）"
        return out
    invalidate()
    fresh = status(config)
    out.update({"ok": True, "updated": True,
                "version": fresh["version"], "terms": fresh["terms"],
                "updated_at": fresh["updated_at"], "reason": "已更新"})
    logging.getLogger("ddo").info("公共词典已更新：版本 %s，%d 条",
                                  fresh["version"], fresh["terms"])
    return out
