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
import re
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import ed25519, paths, update

# 默认地址：仓库 dict 分支上的词典（和代码历史分开，链接稳定，随时可换镜像）
DEFAULT_URL = ("https://gitee.com/git55236/ddo-chat-translator/"
               "raw/dict/dictionary/public.json")
# 备用地址（GitHub 镜像仓库的同一个分支）：Gitee 打不开时自动改用这个，
# 内容完全一样、签名也一样（两边都要验签，验不过就丢弃）。
MIRROR_URL = ("https://raw.githubusercontent.com/reaper3l/ddo-chat-translator/"
              "dict/dictionary/public.json")
SIGNATURE_MARK = "---- DDO-DICT-SIGNATURE ----"
USER_AGENT = "DDOTranslator-publicdict (+%s)" % update.HOMEPAGE
REQUEST_TIMEOUT = 8
MAX_BYTES = 2 * 1024 * 1024          # 词典文件不该超过 2MB，超了直接不认
RETRY_AFTER_FAILURE = 30 * 60        # 本次失败后 30 分钟内不再重试

CACHE_PATH = paths.DATA_DIR / "public_glossary.json"
CACHE_SIG_PATH = paths.DATA_DIR / "public_glossary.json.sig"
NEGATIVE_PATH = paths.DATA_DIR / "public_dict_negatives.json"

# 多词典源（v3.0.30 起）：官方源沿用上面那个老缓存文件（升级时不用重新下载），
# 用户自己加的源各用一个缓存文件，互不影响。
SOURCE_DIR = paths.DATA_DIR / "dict_sources"
SOURCE_STATE_PATH = SOURCE_DIR / "state.json"
OFFICIAL_ID = "official"
OFFICIAL_NAME = "官方公共词典"
KIND_OFFICIAL = "official"
KIND_URL = "url"
KIND_FILE = "file"
SOURCE_KINDS = (KIND_OFFICIAL, KIND_URL, KIND_FILE)
MAX_SOURCES = 12                     # 源太多会把术语表撑乱，也给界面留个上限

# 同一个公共词被用户自己改了这么多次 → 本机先停用它（不影响别人）
DISABLE_AFTER_NEGATIVES = 2

# 合并后的词条缓存：(源指纹, 词条)。源一变（开关/顺序/地址变了）就重算。
_terms_cache: Optional[Tuple[str, Dict[str, str]]] = None
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


# ------------------------------------------------------------------ 词典源
def _norm_source(raw, index: int) -> Optional[dict]:
    """把配置里的一项整理成标准字段；认不出来（少了地址/路径）就返回 None。"""
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in SOURCE_KINDS:
        return None
    # id 会当成缓存文件名用（data/dict_sources/<id>.json），只留安全字符
    raw_id = re.sub(r"[^A-Za-z0-9_.-]", "", str(raw.get("id") or ""))[:32]
    item = {
        "id": raw_id.strip(".") or ("src%d" % index),
        "name": str(raw.get("name") or "").strip() or ("词典源 %d" % index),
        "kind": kind,
        "enabled": bool(raw.get("enabled", True)),
        "ack": bool(raw.get("ack", False)),
        "url": str(raw.get("url") or "").strip(),
        "path": str(raw.get("path") or "").strip(),
        "pubkey": str(raw.get("pubkey") or "").strip(),
    }
    if kind == KIND_URL and not item["url"]:
        return None
    if kind == KIND_FILE and not item["path"]:
        return None
    return item


def sources(config: Optional[dict] = None) -> List[dict]:
    """当前配置里的词典源（官方源永远排第一）。

    老配置只有 `public_dict_url` 一个地址、没有 `dict_sources`：这里等值迁移成
    「官方源 + 那个自定义地址」，老用户手改过的镜像地址不会丢。
    用户加的源按添加顺序排在官方源后面，同名冲突时先到的赢（见 load_terms）。
    """
    config = config or {}
    raw = config.get("dict_sources")
    items: List[dict] = []
    if isinstance(raw, list):
        for index, entry in enumerate(raw, 1):
            item = _norm_source(entry, index)
            if item is not None:
                items.append(item)
    if not any(item["kind"] == KIND_OFFICIAL for item in items):
        items.insert(0, {
            "id": OFFICIAL_ID, "name": OFFICIAL_NAME, "kind": KIND_OFFICIAL,
            "enabled": True, "ack": True, "pubkey": "",
            "url": str(config.get("public_dict_url") or "").strip(), "path": "",
        })
    ordered = ([item for item in items if item["kind"] == KIND_OFFICIAL][:1]
               + [item for item in items if item["kind"] != KIND_OFFICIAL])
    out: List[dict] = []
    used = set()
    for item in ordered[:MAX_SOURCES]:
        base = item["id"]
        suffix = 2
        while item["id"] in used:                 # id 撞了缓存文件会打架
            item["id"] = "%s-%d" % (base, suffix)
            suffix += 1
        used.add(item["id"])
        out.append(item)
    return out


def source_cache_paths(item: dict) -> Tuple[Path, Path]:
    """这个源的内容缓存放哪。官方源沿用老文件 —— 升级上来不用重新下载。"""
    if item.get("kind") == KIND_OFFICIAL:
        return CACHE_PATH, CACHE_SIG_PATH
    return (SOURCE_DIR / ("%s.json" % item["id"]),
            SOURCE_DIR / ("%s.json.sig" % item["id"]))


def verify_source(item: dict, payload: bytes, sig_text: str,
                  pubkey: Optional[str] = None) -> Tuple[bool, str]:
    """按来源决定怎么信任。

    * 官方源：**必须**验签，用程序内置的发布公钥（不接受别的钥匙）；
    * 用户加的源：带 `.sig` 就验（源里配了自己的公钥就用它），没签名就必须
      `ack`（添加时确认过"来源由我自己判断"）—— 否则不采用。
    """
    signed = bool((sig_text or "").strip())
    if item.get("kind") == KIND_OFFICIAL:
        if not signed:
            return False, "官方词典缺少签名文件"
        return verify(payload, sig_text, pubkey=item.get("pubkey") or pubkey)
    if signed:
        return verify(payload, sig_text, pubkey=item.get("pubkey") or None)
    if not item.get("ack"):
        return False, "这个源没有签名，来源也没确认过（在「词典源」里重新添加一次）"
    return True, ""


def _parse_terms(payload: bytes) -> Dict[str, str]:
    """把词典 JSON 里的 terms 取出来（大小写、类型都过一遍）。"""
    try:
        data = json.loads(payload.decode("utf-8"))
    except Exception as exc:                       # noqa: BLE001
        logging.getLogger("ddo").warning("词典解析失败：%s", exc)
        return {}
    raw = data.get("terms", {}) if isinstance(data, dict) else {}
    out: Dict[str, str] = {}
    if isinstance(raw, dict):
        for term, translation in raw.items():
            if (isinstance(term, str) and isinstance(translation, str)
                    and term.strip() and translation.strip()):
                out[term] = translation
    return out


def _read_source_file(item: dict) -> Tuple[bytes, str]:
    """读本地文件源：文件本身 + 同名 `.sig`（有就一起读，没有就是未签名）。"""
    path = Path(str(item.get("path") or ""))
    payload = _read_bytes(path)
    if not payload:
        return b"", ""
    sig_path = Path("%s.sig" % path)
    if sig_path.exists():
        return payload, _read_bytes(sig_path).decode("utf-8", errors="replace")
    return payload, ""


def source_terms(item: dict) -> Dict[str, str]:
    """读某个源当前的词条（不联网），带签名的顺手验一次，验不过当没有。

    本地文件源**直接读文件**（改完就生效，不用等下一次检查）；网络源读下载缓存。
    """
    if item.get("kind") == KIND_FILE:
        payload, sig_text = _read_source_file(item)
    else:
        path, sig_path = source_cache_paths(item)
        payload = _read_bytes(path)
        sig_text = _read_bytes(sig_path).decode("utf-8", errors="replace")
    if not payload:
        return {}
    ok, reason = verify_source(item, payload, sig_text)
    if not ok:
        logging.getLogger("ddo").warning("词典源「%s」的缓存不可信，忽略：%s",
                                         item.get("name"), reason)
        return {}
    return _parse_terms(payload)


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


def load_terms(config: Optional[dict] = None) -> Dict[str, str]:
    """当前可用的公共词条（只读缓存，不联网）。

    多个源按顺序叠加，**只做加法**：同一个词（归一化后）先出现的赢 ——
    官方源排第一，用户自己加的源只能补前面没有的词，谁也覆盖不了谁。
    """
    global _terms_cache
    items = [item for item in sources(config) if item.get("enabled")]
    signature = "|".join("%s:%s:%s:%s" % (item["id"], item["kind"], item["url"],
                                          item["path"]) for item in items)
    if _terms_cache is not None and _terms_cache[0] == signature:
        return _terms_cache[1]
    terms: Dict[str, str] = {}
    seen = set()
    for item in items:
        for term, translation in source_terms(item).items():
            key = term.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            terms[term] = translation
    disabled = disabled_terms()
    if disabled:
        terms = {term: zh for term, zh in terms.items()
                 if term.strip().lower() not in disabled}
    _terms_cache = (signature, terms)
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


def _state() -> dict:
    """每个源上次什么时候成功/失败（写进 state.json，界面能显示"上次错误"）。"""
    data = paths.read_json(SOURCE_STATE_PATH, {})
    return data if isinstance(data, dict) else {}


def _save_state(state: dict) -> None:
    try:
        paths.write_json(SOURCE_STATE_PATH, state)
    except Exception:                              # noqa: BLE001
        pass


def _remember(item: dict, now: float, ok: bool, error: str = "",
              digest: str = "") -> None:
    state = _state()
    info = state.get(item["id"])
    info = dict(info) if isinstance(info, dict) else {}
    info["last_try"] = now
    info["name"] = str(item.get("name") or "")
    if digest:
        info["digest"] = digest
    if ok:
        info["last_ok"] = now
        info["last_error"] = ""
    else:
        info["last_error"] = str(error or "")[:200]
    state[item["id"]] = info
    _save_state(state)


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def status(config: Optional[dict] = None) -> dict:
    """给界面用的一份状态（不联网）：总开关 + 每个源各自的条数/时间/上次错误。"""
    config = config or {}
    state = _state()
    rows = []
    for item in sources(config):
        if item.get("kind") == KIND_FILE:
            stamp_path = Path(str(item.get("path") or ""))
        else:
            stamp_path, _sig_path = source_cache_paths(item)
        info = state.get(item["id"])
        info = info if isinstance(info, dict) else {}
        terms = source_terms(item) if item.get("enabled") else {}
        rows.append({
            "id": item["id"], "name": item["name"], "kind": item["kind"],
            "enabled": bool(item.get("enabled")),
            "url": str(item.get("url") or ""), "path": str(item.get("path") or ""),
            "terms": len(terms),
            "updated_at": _mtime(stamp_path),
            "last_ok": float(info.get("last_ok") or 0),
            "last_try": float(info.get("last_try") or 0),
            "error": str(info.get("last_error") or ""),
        })
    terms = load_terms(config)
    _payload, version, _when = cached()
    stamps = [row["updated_at"] for row in rows if row["updated_at"]]
    return {
        "enabled": bool(config.get("public_dict_enabled", True)),
        "has_data": bool(terms),
        "version": version,
        "terms": len(terms),
        "updated_at": max(stamps) if stamps else 0.0,
        "url": effective_url(config),
        "interval_hours": int(config.get("public_dict_interval_hours", 6) or 6),
        "negatives": len(negatives()),        # 本机给公共词投过多少次否定票
        "disabled": len(disabled_terms()),    # 被本机停用的公共词
        "sources": rows,
    }


def effective_url(config: Optional[dict] = None) -> str:
    """实际使用的地址：配置里填了就用配置的（方便换镜像），否则用内置默认。"""
    config = config or {}
    return str(config.get("public_dict_url") or "").strip() or DEFAULT_URL


def url_candidates(config: Optional[dict] = None) -> List[str]:
    """按顺序尝试的下载地址：配置/默认 → 备用镜像（去重）。"""
    first = effective_url(config)
    out = [first]
    if MIRROR_URL not in out:
        out.append(MIRROR_URL)
    return out


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
    """有没有哪个启用中的源该检查了（刚失败过的源 30 分钟内不再试）。"""
    config = config or {}
    if not config.get("public_dict_enabled", True):
        return False
    now = time.time() if now is None else now
    if now - _last_attempt < RETRY_AFTER_FAILURE and not cached()[0]:
        return False
    hours = float(config.get("public_dict_interval_hours", 6) or 6)
    state = _state()
    for item in sources(config):
        if not item.get("enabled"):
            continue
        info = state.get(item["id"])
        info = info if isinstance(info, dict) else {}
        last_ok = float(info.get("last_ok") or 0)
        if not last_ok:
            return True                       # 这个源还没成功过 → 该试
        if now - last_ok >= hours * 3600:
            return True
    return False


def _host(url: str) -> str:
    try:
        return urllib.parse.urlsplit(url).netloc or url
    except Exception:                              # noqa: BLE001
        return url


def _fetch_source(item: dict, config: dict, fetch: Callable[[str], bytes]
                  ) -> Tuple[bytes, str, str]:
    """按源的类型拿内容，返回 (内容, 签名文本, 错误)。

    官方源带镜像候选（主地址失败自动换备用地址）；用户加的网地址就认它自己那个。
    """
    if item.get("kind") == KIND_OFFICIAL:
        primary = str(item.get("url") or "").strip()
        urls = (url_candidates(config) if not primary
                else ([primary] if MIRROR_URL == primary else [primary, MIRROR_URL]))
    else:
        urls = [str(item.get("url") or "")]
    reasons = []
    for url in urls:
        try:
            payload = fetch(url)
        except Exception as exc:                   # noqa: BLE001
            reasons.append("%s：%s" % (_host(url), exc))
            continue
        try:
            sig_text = fetch(url + ".sig").decode("utf-8", errors="replace")
        except Exception:                          # noqa: BLE001
            sig_text = ""
        if item.get("kind") == KIND_OFFICIAL and not sig_text.strip():
            reasons.append("%s：拿不到签名文件" % _host(url))
            continue
        return payload, sig_text, ""
    return b"", "", "下载失败：%s" % "；".join(reasons)


def _install_payload(item: dict, payload: bytes, sig_text: str,
                     pubkey: Optional[str] = None) -> dict:
    """验签 → 解析 → 灰度 → 写缓存。返回 {ok, updated, reason, terms...}。"""
    ok, reason = verify_source(item, payload, sig_text, pubkey=pubkey)
    if not ok:
        return {"ok": False, "updated": False, "reason": reason}
    try:
        data = json.loads(payload.decode("utf-8"))
    except Exception as exc:                       # noqa: BLE001
        return {"ok": False, "updated": False,
                "reason": "词典内容不是合法 JSON：%s" % exc}
    if not isinstance(data, dict):
        return {"ok": False, "updated": False, "reason": "词典内容格式不对"}
    adopted, why = _rollout_gate(data)
    if not adopted:
        logging.getLogger("ddo").info("词典源「%s」暂缓采用：%s", item.get("name"), why)
        return {"ok": True, "updated": False, "reason": why}
    cache_path, sig_path = source_cache_paths(item)
    if _read_bytes(cache_path) == payload:
        return {"ok": True, "updated": False, "reason": "已经是最新的",
                "terms": len(_parse_terms(payload))}
    if not _write_bytes(cache_path, payload):
        return {"ok": False, "updated": False, "reason": "写缓存失败（磁盘问题？）"}
    if sig_text.strip():
        _write_bytes(sig_path, sig_text.encode("utf-8"))
    else:
        try:
            sig_path.unlink()                      # 没签名的源不留旧签名
        except OSError:
            pass
    invalidate()
    return {"ok": True, "updated": True, "reason": "已更新",
            "version": str(data.get("version") or ""),
            "terms": len(_parse_terms(payload))}


def sync_source(item: dict, config: Optional[dict] = None,
                fetcher: Optional[Callable[[str], bytes]] = None,
                force: bool = False, pubkey: Optional[str] = None,
                now: Optional[float] = None) -> dict:
    """拉一个源。失败只影响这个源，返回状态字典，绝不抛异常。"""
    config = config or {}
    now = time.time() if now is None else now
    out = {"id": item.get("id"), "name": item.get("name") or item.get("id"),
           "kind": item.get("kind"), "ok": False, "checked": False,
           "updated": False, "reason": "", "terms": 0, "version": ""}
    if not item.get("enabled"):
        out.update({"ok": True, "reason": "已停用"})
        return out

    info = _state().get(item["id"])
    info = info if isinstance(info, dict) else {}
    if not force:
        last_try = float(info.get("last_try") or 0)
        last_ok = float(info.get("last_ok") or 0)
        if info.get("last_error") and now - last_try < RETRY_AFTER_FAILURE:
            out.update({"ok": True, "reason": "上次没成功，过一会儿再试"})
            return out
        hours = float(config.get("public_dict_interval_hours", 6) or 6)
        if last_ok and now - last_ok < hours * 3600:
            out.update({"ok": True, "reason": "还没到下次检查时间"})
            return out

    out["checked"] = True
    if item.get("kind") == KIND_FILE:
        payload, sig_text = _read_source_file(item)
        if not payload:
            out["reason"] = "读不到文件：%s" % item.get("path")
            _remember(item, now, False, out["reason"])
            return out
        ok, reason = verify_source(item, payload, sig_text)
        if not ok:
            out["reason"] = reason
            _remember(item, now, False, reason)
            return out
        digest = hashlib.sha256(payload).hexdigest()
        updated = digest != str(info.get("digest") or "")
        out.update({"ok": True, "updated": updated,
                    "terms": len(_parse_terms(payload)),
                    "reason": "已读取" if updated else "没有变化"})
        _remember(item, now, True, digest=digest)
        # 本地文件是直接读的，合并结果必须跟着失效 —— 否则用户改完文件、
        # 点「立即更新」也只会拿到上一份合并缓存（术语表看起来没变）。
        invalidate()
        return out
    else:
        fetch = fetcher or _fetch
        payload, sig_text, error = _fetch_source(item, config, fetch)
        if error:
            out["reason"] = error
            _remember(item, now, False, error)
            logging.getLogger("ddo").info("词典源「%s」%s", out["name"], error)
            return out
        result = _install_payload(item, payload, sig_text, pubkey=pubkey)

    out.update(result)
    _remember(item, now, bool(result.get("ok")),
              "" if result.get("ok") else str(result.get("reason") or ""))
    if not result.get("ok"):
        logging.getLogger("ddo").warning("词典源「%s」没更新：%s",
                                         out["name"], result.get("reason"))
    return out


def sync(config: Optional[dict] = None,
         fetcher: Optional[Callable[[str], bytes]] = None,
         force: bool = False,
         pubkey: Optional[str] = None) -> dict:
    """把所有启用中的源各拉一次，逐源更新缓存。

    任何一个源失败都不影响别的源（这是多源的意义）；返回一份状态字典，绝不抛异常。
    fetcher / pubkey 可以注入（测试用）。
    """
    global _last_attempt
    config = config or {}
    out = status(config)
    out.update({"ok": True, "updated": False, "reason": "", "sources": []})
    if not config.get("public_dict_enabled", True):
        out["ok"] = False
        out["reason"] = "设置里关掉了公共词典"
        return out
    _last_attempt = time.time()
    if not force and not needs_sync(config):
        out["reason"] = "还没到下次检查时间"
        return out
    results = [sync_source(item, config, fetcher, force, pubkey)
               for item in sources(config)]
    out["sources"] = results
    checked = [row for row in results if row.get("checked")]
    failed = [row for row in checked if not row.get("ok")]
    updated = [row for row in results if row.get("updated")]
    out["updated"] = bool(updated)
    out["ok"] = not failed
    if failed:
        out["reason"] = "；".join("%s：%s" % (row["name"], row["reason"])
                                  for row in failed)
    elif updated:
        out["reason"] = "已更新：" + "、".join(row["name"] for row in updated)
    else:
        out["reason"] = "已经是最新的" if checked else "没有需要检查的源"
    fresh = status(config)
    for key in ("version", "terms", "updated_at", "has_data"):
        out[key] = fresh[key]
    return out
