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
NEGATIVE_PATH = paths.DATA_DIR / "public_dict_negatives.json"

# 同一个公共词被用户自己改了这么多次 → 本机先停用它（不影响别人）
DISABLE_AFTER_NEGATIVES = 2

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
    disabled = disabled_terms()
    if disabled:
        terms = {term: zh for term, zh in terms.items()
                 if term.strip().lower() not in disabled}
    _terms_cache = terms
    return terms


# ---------------------------------------------------------- 本机自保护（否定票）
def _load_negatives() -> dict:
    data = paths.read_json(NEGATIVE_PATH, {})
    return data if isinstance(data, dict) else {}


def negatives() -> Dict[str, int]:
    """本机记录下来的"某个公共词被用户改掉"的次数。"""
    data = _load_negatives()
    out = {}
    for term, item in data.items():
        if isinstance(item, dict):
            out[str(term)] = int(item.get("neg", 0) or 0)
    return out


def disabled_terms() -> set:
    """本机已停用的公共词（被用户反复改掉的）。"""
    return {term.strip().lower() for term, count in negatives().items()
            if count >= DISABLE_AFTER_NEGATIVES}


def record_negative(term: str, user_zh: str = "") -> int:
    """用户自己给某个公共词换了译法 → 记一张否定票（只记在本机）。

    累计到 DISABLE_AFTER_NEGATIVES 次就在**这台机器**上停用该词（不影响其他人），
    下次贡献时会把这张否决票一起发给作者，用来把不合用的词从公共库里退掉。
    返回这个条目当前的否定票数。
    """
    key = str(term or "").strip()
    if not key:
        return 0
    public_terms = load_terms()
    mine = public_terms.get(key) or public_terms.get(key.lower())
    if not mine:
        for candidate, zh in public_terms.items():       # 大小写不敏感地找一遍
            if candidate.strip().lower() == key.lower():
                mine, key = zh, candidate
                break
    if not mine:
        return 0                                          # 不是公共词，不关这事
    if user_zh and user_zh.strip() == str(mine).strip():
        return 0                                          # 译法和公共库一样，不算否定
    data = _load_negatives()
    item = data.get(key) if isinstance(data.get(key), dict) else {}
    count = int(item.get("neg", 0) or 0) + 1
    data[key] = {"neg": count, "last": time.strftime("%Y-%m-%d %H:%M"),
                 "public": str(mine)[:40], "mine": str(user_zh)[:40]}
    paths.write_json(NEGATIVE_PATH, data)
    invalidate()                                          # 可能刚好达到停用门槛
    return count


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
        "negatives": len(negatives()),        # 本机给公共词投过多少次否定票
        "disabled": len(disabled_terms()),    # 被本机停用的公共词
    }


def effective_url(config: Optional[dict] = None) -> str:
    """实际使用的地址：配置里填了就用配置的（方便换镜像），否则用内置默认。"""
    config = config or {}
    return str(config.get("public_dict_url") or "").strip() or DEFAULT_URL


def _install_bucket(batch: str) -> int:
    """本机在灰度分桶里的位置（0~99）。

    用匿名安装 ID + 批次号算哈希：同一个人对同一批的归属是稳定的，
    不同批次之间又是打散的（不会总是同一批用户先拿到）。
    """
    try:
        from . import contribute

        uid = contribute.install_uid()
    except Exception:
        uid = "anonymous"
    digest = hashlib.sha256(("%s\x1f%s" % (uid, batch)).encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 100


def _rollout_gate(data: dict, now: Optional[float] = None) -> Tuple[bool, str]:
    """灰度判定：这一批要不要现在就对本机生效。

    * `rollout.percent >= 100` 或没写 → 全量，直接生效；
    * 否则先只放给一部分用户；**观察窗口结束**（updated + hold_hours）后所有客户端都会采用
      —— 客户端不需要服务端配合，靠文件里的时间自己判断。
    """
    rollout = data.get("rollout") if isinstance(data, dict) else None
    if not isinstance(rollout, dict):
        return True, ""
    try:
        percent = int(rollout.get("percent", 100) or 100)
        hold_hours = float(rollout.get("hold_hours", 0) or 0)
    except (TypeError, ValueError):
        return True, ""
    if percent >= 100 or hold_hours <= 0:
        return True, ""
    updates = str(data.get("updated") or "")
    when = None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            when = time.mktime(time.strptime(updates, fmt))
            break
        except ValueError:
            continue
    now = time.time() if now is None else now
    if when is not None and now >= when + hold_hours * 3600:
        return True, ""                       # 观察窗口结束 → 全量采用
    batch = str(data.get("batch", ""))
    if _install_bucket(batch) < max(0, percent):
        return True, ""                       # 本机在首批里
    wait = 0.0
    if when is not None:
        wait = max(0.0, (when + hold_hours * 3600 - now) / 3600.0)
    return False, "灰度中（本机不在首批，约 %.0f 小时后自动采用）" % wait


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
    try:
        incoming = json.loads(payload.decode("utf-8"))
    except Exception as exc:                          # noqa: BLE001
        out["reason"] = "词典内容不是合法 JSON：%s" % exc
        return out
    if not isinstance(incoming, dict):
        out["reason"] = "词典内容格式不对"
        return out
    adopted, why = _rollout_gate(incoming)
    if not adopted:
        out.update({"ok": True, "reason": why})
        logging.getLogger("ddo").info("公共词典暂缓采用：%s", why)
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
