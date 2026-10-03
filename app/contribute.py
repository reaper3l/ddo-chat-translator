"""贡献回传：把用户**确认过的**纠错与术语匿名汇给作者，用来改进公共词典。

默认**不上传**。只有用户主动打开"参与改进"并点"贡献"时才会发送；发送前能预览。
收集的内容只有两类，都是用户主动做过的动作：

* F10 改过的句子（原文 → 他改成的中文）；
* 他自己在词典/学习中心里加的术语。

原始聊天流**永远不收集**（那还包含别的玩家说的话）。

本机先脱敏（链接、邮箱、连续数字、疑似玩家名前缀），作者端拿到后再校验一次
—— 客户端的东西一律当不可信输入。

作者侧用 `aggregate()` 跑**自动闸门**（共识门槛 + 格式规则 + 异常报警），
不需要人工逐条过目：

* 至少 N 个**不同**的人独立给出同一条 → 才收（单人刷量再多也不算）；
* 一致率要高（大家说法不一致说明这词有歧义）；
* 单人占比过高、单批新增异常多、命中广告/联系方式特征 → 报警并整批停下等人看。
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import secrets
import time
import urllib.request
import zlib
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from . import glossary, paths

CODE_PREFIX = "DDO1:"
PROTOCOL = 1
USER_AGENT = "DDOTranslator-contribute"
SEND_TIMEOUT = 10
MAX_CODE_CHARS = 20000

INSTALL_ID_PATH = paths.DATA_DIR / "install_id.txt"
STATE_PATH = paths.DATA_DIR / "contribute_state.json"

# ---------------------------------------------------------------- 脱敏规则
URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
DIGITS_RE = re.compile(r"\d{5,}")
HANDLE_RE = re.compile(r"@[A-Za-z0-9_]{2,}")
CHAT_PREFIX_RE = re.compile(r"^\s*(?:\([^)]{0,12}\)\s*[:：]\s*)?(?:\[[^\]]{0,12}\]\s*)?"
                            r"[A-Za-z0-9_\-]{2,16}\s*[:：]\s+")

# 术语/句子 的形态门槛（客户端和作者端共用；作者端再查一遍）
TERM_SOURCE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9' \-]{1,19}$")
TERM_ZH_RE = re.compile(r"^[\u4e00-\u9fffA-Za-z0-9 ·\-]{1,12}$")
PHRASE_SOURCE_RE = re.compile(r"^[A-Za-z0-9 ,.!?'\"()\[\]/&%+\-=*<>~$^;|]+$")
AD_WORDS = ("qq群", "微信", "加群", "代练", "出售", "网址", "www.", "http",
            "contact me", "buy gold", "cheap gold", "sell gold")


def scrub(text: str) -> str:
    """去掉明显能指向具体人的东西（链接/邮箱/长数字/@账号）。"""
    value = str(text or "")
    value = URL_RE.sub("[链接]", value)
    value = EMAIL_RE.sub("[邮箱]", value)
    value = HANDLE_RE.sub("[账号]", value)
    value = DIGITS_RE.sub("[数字]", value)
    return re.sub(r"\s+", " ", value).strip()


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in str(text or ""))


def _looks_like_chat_line(text: str) -> bool:
    """带"名字: 内容"的聊天行不贡献 —— 原文里往往夹着玩家名。"""
    return bool(CHAT_PREFIX_RE.match(str(text or "")))


def clean_term(term: str, translation: str) -> Optional[Tuple[str, str]]:
    """术语过滤：不合格返回 None。"""
    term = scrub(term)
    translation = scrub(translation)
    if not TERM_SOURCE_RE.match(term):
        return None
    # 和术语表同一套门槛：普通英文词、太短的噪音即使收进来也用不上
    key = term.strip().lower()
    if " " not in key and (key in glossary.NEVER_PROTECT or len(key) < 3):
        return None
    if not TERM_ZH_RE.match(translation) or not _has_cjk(translation):
        return None
    if "[" in translation or "[" in term:            # 脱敏过的痕迹，不贡献
        return None
    if any(word in term.lower() for word in AD_WORDS):
        return None
    return term, translation


def clean_phrase(source: str, translation: str) -> Optional[Tuple[str, str]]:
    """整句纠错过滤：不合格返回 None。"""
    source = scrub(source)
    translation = scrub(translation)
    if not (2 <= len(source) <= 80) or not (1 <= len(translation) <= 60):
        return None
    if _has_cjk(source) or _looks_like_chat_line(source):
        return None
    if not PHRASE_SOURCE_RE.match(source) or ":" in source:
        return None
    if len(source.split()) < 2:                       # 单个词走术语那条路
        return None
    if not _has_cjk(translation):
        return None
    if "[" in source or "[" in translation:           # 被脱敏过 → 不贡献
        return None
    if any(word in source.lower() for word in AD_WORDS):
        return None
    return source, translation


# ---------------------------------------------------------------- 匿名标识
def install_uid() -> str:
    """本机随机安装 ID 的哈希（不含任何个人信息，也不可反推）。

    "多少个不同的人"是闸门里最关键的一条，所以需要一个稳定的本机标识；
    用随机生成 + 哈希，既不指向用户，也不会随硬件变化。
    """
    try:
        raw = INSTALL_ID_PATH.read_text(encoding="utf-8").strip()
    except Exception:
        raw = ""
    if not raw:
        raw = secrets.token_hex(16)
        try:
            INSTALL_ID_PATH.parent.mkdir(parents=True, exist_ok=True)
            INSTALL_ID_PATH.write_text(raw, encoding="utf-8")
        except Exception:
            pass
    return hashlib.sha256(("ddo-uid:" + raw).encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------- 本地台账
def _load_state() -> dict:
    data = paths.read_json(STATE_PATH, {})
    if not isinstance(data, dict):
        return {"sent": {}}
    data.setdefault("sent", {})
    return data


def _save_state(state: dict) -> None:
    paths.write_json(STATE_PATH, state)


def item_key(kind: str, source: str, translation: str) -> str:
    return hashlib.sha1(("%s\x1f%s\x1f%s" % (kind, source.strip().lower(),
                                             translation.strip())).encode("utf-8")
                        ).hexdigest()[:16]


def collect_items(memory, include_sent: bool = False) -> dict:
    """从学习库里挑出"可以贡献"的条目（已贡献过的不再重复）。"""
    state = _load_state()
    sent = state.get("sent", {})
    terms: List[dict] = []
    phrases: List[dict] = []
    skipped = {"sent": 0, "invalid": 0}

    for item in getattr(memory, "term_list", lambda: [])():
        cleaned = clean_term(item.get("text", ""), item.get("zh", ""))
        if not cleaned:
            skipped["invalid"] += 1
            continue
        term, translation = cleaned
        key = item_key("term", term, translation)
        if not include_sent and key in sent:
            skipped["sent"] += 1
            continue
        terms.append({"kind": "term", "key": key, "source": term,
                      "zh": translation, "count": int(item.get("count", 1) or 1)})

    data = getattr(memory, "data", {}) or {}
    for _fp, item in (data.get("phrases") or {}).items():
        if not isinstance(item, dict) or not item.get("stable"):
            continue          # 只贡献"用户反复确认过 / 标记为稳定"的句子
        cleaned = clean_phrase(item.get("source", ""), item.get("zh", ""))
        if not cleaned:
            skipped["invalid"] += 1
            continue
        source, translation = cleaned
        key = item_key("phrase", source, translation)
        if not include_sent and key in sent:
            skipped["sent"] += 1
            continue
        phrases.append({"kind": "phrase", "key": key, "source": source,
                        "zh": translation, "count": int(item.get("count", 1) or 1)})

    return {"terms": terms, "phrases": phrases, "skipped": skipped}


def mark_sent(items: Iterable[dict]) -> None:
    """记下已经贡献过的条目，避免每次重复发送。"""
    state = _load_state()
    sent = state.setdefault("sent", {})
    now = time.strftime("%Y-%m-%d %H:%M")
    for item in items:
        sent[item.get("key", "")] = now
    state["last_sent"] = now
    _save_state(state)


# ------------------------------------------------------------ 贡献码编解码
def build_payload(items: dict, version: str = "") -> dict:
    """组装要发出的内容（只有术语/句子，没有别的）。"""
    return {
        "v": PROTOCOL,
        "uid": install_uid(),
        "app": version,
        "time": time.strftime("%Y-%m-%d"),
        "terms": [{"t": i["source"], "z": i["zh"]} for i in items.get("terms", [])],
        "phrases": [{"t": i["source"], "z": i["zh"]} for i in items.get("phrases", [])],
    }


def encode_code(payload: dict) -> str:
    """压成可以贴进聊天/issue 的一行文本。"""
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    packed = base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode("ascii")
    return CODE_PREFIX + packed


def decode_code(code: str) -> Optional[dict]:
    """解出贡献码；格式不对返回 None。"""
    text = str(code or "").strip()
    if CODE_PREFIX not in text:
        return None
    packed = text.split(CODE_PREFIX, 1)[1].strip()
    packed = re.split(r"[\s，。、）)]", packed, 1)[0]        # 贴进聊天时后面可能跟别的字
    try:
        raw = zlib.decompress(base64.urlsafe_b64decode(packed.encode("ascii")))
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("uid"), str):
        return None
    for key in ("terms", "phrases"):
        rows = data.get(key)
        data[key] = [r for r in rows if isinstance(r, dict) and r.get("t") and r.get("z")] \
            if isinstance(rows, list) else []
    return data


def find_codes(text: str) -> List[dict]:
    """从一段文本（比如 issue 正文、聊天记录）里把所有贡献码解出来。"""
    found = []
    for match in re.finditer(re.escape(CODE_PREFIX) + r"[A-Za-z0-9_\-=]+", str(text or "")):
        payload = decode_code(match.group(0))
        if payload:
            found.append(payload)
    return found


def preview_text(payload: dict) -> str:
    """给用户看：这次到底会发出去什么。"""
    lines = ["将发送的内容（只有下面这些，没有聊天原文、没有你的 API Key）：", ""]
    lines.append("· 匿名标识：%s（随机生成，不含任何个人信息）" % payload.get("uid", ""))
    lines.append("· 程序版本：%s" % (payload.get("app") or "未知"))
    lines.append("")
    terms = payload.get("terms") or []
    phrases = payload.get("phrases") or []
    lines.append("【术语】%d 条" % len(terms))
    for row in terms[:200]:
        lines.append("  %s = %s" % (row.get("t"), row.get("z")))
    if len(terms) > 200:
        lines.append("  …（其它 %d 条）" % (len(terms) - 200))
    lines.append("")
    lines.append("【整句纠错】%d 条" % len(phrases))
    for row in phrases[:200]:
        lines.append("  %s → %s" % (row.get("t"), row.get("z")))
    if len(phrases) > 200:
        lines.append("  …（其它 %d 条）" % (len(phrases) - 200))
    return "\n".join(lines)


# ------------------------------------------------------------------ 发送
def send(payload: dict, url: str,
         poster: Optional[Callable[[str, bytes], bool]] = None) -> Tuple[bool, str]:
    """把贡献发到收件端。失败不抛异常，返回 (是否成功, 说明)。"""
    if not url:
        return False, "还没有配置接收地址（先复制贡献码，用别的方式发给作者也行）"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if poster is not None:
        try:
            return (True, "已发送") if poster(url, body) else (False, "接收端没有确认")
        except Exception as exc:                     # noqa: BLE001
            return False, "发送失败：%s" % exc
    try:
        request = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json; charset=utf-8",
                     "User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=SEND_TIMEOUT) as response:
            ok = 200 <= getattr(response, "status", 200) < 300
        return (True, "已发送") if ok else (False, "接收端返回异常")
    except Exception as exc:                          # noqa: BLE001
        logging.getLogger("ddo").info("贡献发送失败：%s", exc)
        return False, "发送失败：%s" % exc


# -------------------------------------------------------------- 作者侧闸门
DEFAULT_GATES = {
    "min_users": 4,            # 至少几个不同的人独立给出同一条
    "min_count": 8,            # 至少被提交多少次
    "min_consistency": 0.8,    # 同一术语的最主流译法占比
    "max_single_share": 0.35,  # 单个用户最多占多少（防小号刷）
    "phrase_min_users": 8,     # 整句要求更严（而且只出报告）
    "phrase_min_count": 20,
    "phrase_min_consistency": 0.9,
    "max_new_per_run": 30,     # 单批新增超过这个数 → 报警，停下来等人看
    "max_single_user_share_alert": 0.5,
}


def _validate_rows(payload: dict) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]:
    """作者端再校验一遍客户端来的内容（客户端一律不可信）。"""
    terms, phrases = [], []
    for row in payload.get("terms") or []:
        cleaned = clean_term(row.get("t", ""), row.get("z", ""))
        if cleaned:
            terms.append(cleaned)
    for row in payload.get("phrases") or []:
        cleaned = clean_phrase(row.get("t", ""), row.get("z", ""))
        if cleaned:
            phrases.append(cleaned)
    return terms, phrases


def aggregate(records: Iterable[dict], gates: Optional[dict] = None,
              known_terms: Optional[Iterable[str]] = None) -> dict:
    """把一堆贡献聚合成候选词表 + 一份人看的报告（自动闸门都在这里）。

    records 是若干份 payload（来自收件端或贡献码）。
    """
    gate = dict(DEFAULT_GATES)
    gate.update(gates or {})
    known = {str(t).strip().lower() for t in (known_terms or [])}

    # 术语 → 译法 → 每个匿名 ID 提交了几次
    term_votes: Dict[str, Dict[str, Dict[str, int]]] = {}
    phrase_votes: Dict[str, Dict[str, Dict[str, int]]] = {}
    user_totals: Dict[str, int] = {}
    rejected: List[dict] = []

    for payload in records:
        uid = str(payload.get("uid") or "?")
        terms, phrases = _validate_rows(payload)
        user_totals[uid] = user_totals.get(uid, 0) + len(terms) + len(phrases)
        for source, translation in terms:
            key = source.strip().lower()
            by_uid = term_votes.setdefault(key, {}).setdefault(translation, {})
            by_uid[uid] = by_uid.get(uid, 0) + 1
        for source, translation in phrases:
            key = source.strip().lower()
            by_uid = phrase_votes.setdefault(key, {}).setdefault(translation, {})
            by_uid[uid] = by_uid.get(uid, 0) + 1

    def pick(votes: Dict[str, Dict[str, int]], min_users: int, min_count: int,
             min_consistency: float, max_share: float) -> Tuple[Optional[str], dict]:
        """按"多少个不同的人独立同意"来判定，而不是按提交次数。"""
        ranked = sorted(votes.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        translation, by_uid = ranked[0]
        top_users = len(by_uid)
        all_users = set()
        for uids in votes.values():
            all_users |= set(uids)
        submissions = sum(sum(uids.values()) for uids in votes.values())
        top_uid_max = max(by_uid.values()) if by_uid else 0
        share = top_uid_max / float(submissions or 1)        # 单个人占这条的比例
        info = {"users": top_users, "count": submissions,
                "share": share,
                "consistency": top_users / float(len(all_users) or 1),
                "top": translation}
        fail = []
        if top_users < min_users:
            fail.append("不同用户数 %d < %d" % (top_users, min_users))
        if submissions < min_count:
            fail.append("出现次数 %d < %d" % (submissions, min_count))
        if info["consistency"] < min_consistency:
            fail.append("一致率 %.2f < %.2f" % (info["consistency"], min_consistency))
        if share > max_share:
            fail.append("单人占比 %.2f > %.2f" % (share, max_share))
        info["fail"] = fail
        return (None if fail else translation), info

    candidates: Dict[str, str] = {}
    details: List[dict] = []
    for key, votes in sorted(term_votes.items()):
        translation, info = pick(votes, gate["min_users"], gate["min_count"],
                                 gate["min_consistency"], gate["max_single_share"])
        info["term"] = key
        info["known"] = key in known
        details.append(info)
        if translation is None or key in known:
            rejected.append({"kind": "term", "source": key, "why": info["fail"] or ["内置表里已有"]})
            continue
        candidates[key] = translation

    phrase_candidates: Dict[str, str] = {}
    phrase_details: List[dict] = []
    for key, votes in sorted(phrase_votes.items()):
        translation, info = pick(votes, gate["phrase_min_users"],
                                 gate["phrase_min_count"],
                                 gate["phrase_min_consistency"],
                                 gate["max_single_share"])
        info["source"] = key
        phrase_details.append(info)
        if translation:
            phrase_candidates[key] = translation

    alerts: List[str] = []
    if len(candidates) > int(gate["max_new_per_run"]):
        alerts.append("本批新增 %d 条，超过上限 %d —— 可能有人在刷，建议先别推。"
                      % (len(candidates), int(gate["max_new_per_run"])))
    total_items = sum(user_totals.values()) or 1
    for uid, count in sorted(user_totals.items(), key=lambda kv: -kv[1])[:1]:
        if count / float(total_items) > float(gate["max_single_user_share_alert"]):
            alerts.append("单个贡献者占了 %.0f%% 的条目（%s），建议看一眼来源。"
                          % (count * 100.0 / total_items, uid))
    for info in details:
        bad = [w for w in AD_WORDS if w in info["term"]]
        if bad:
            alerts.append("词条命中广告/联系方式特征：%s（%s）" % (info["term"], bad[0]))

    return {
        "terms": dict(sorted(candidates.items())),
        "phrases": phrase_candidates,        # 只给人看，不自动进公共词典
        "details": details,
        "phrase_details": phrase_details,
        "rejected": rejected,
        "alerts": alerts,
        "contributors": len(user_totals),
        "records": len(list(records)) if not isinstance(records, list) else len(records),
    }


def report_text(result: dict, gates: Optional[dict] = None) -> str:
    """把 aggregate 的结果写成一份人扫一眼就够的报告。"""
    gate = dict(DEFAULT_GATES)
    gate.update(gates or {})
    lines = []
    lines.append("公共词典候选报告")
    lines.append("=" * 46)
    lines.append("贡献者 %d 人，候选术语 %d 条，候选整句 %d 条（仅供参考，不会进公共库）"
                 % (result.get("contributors", 0), len(result.get("terms", {})),
                    len(result.get("phrases", {}))))
    alerts = result.get("alerts") or []
    lines.append("报警：%s" % ("无" if not alerts else ""))
    for item in alerts:
        lines.append("  [!] %s" % item)
    lines.append("")
    lines.append("门槛：不同用户 ≥%d、出现 ≥%d 次、一致率 ≥%.0f%%、单人占比 ≤%.0f%%"
                 % (gate["min_users"], gate["min_count"],
                    gate["min_consistency"] * 100, gate["max_single_share"] * 100))
    lines.append("")
    lines.append("【通过】")
    for term, translation in sorted(result.get("terms", {}).items()):
        lines.append("  %s = %s" % (term, translation))
    if not result.get("terms"):
        lines.append("  （无）")
    lines.append("")
    lines.append("【没通过（前 40 条）】")
    for item in (result.get("rejected") or [])[:40]:
        lines.append("  %s：%s" % (item.get("source"), "；".join(item.get("why") or [])))
    lines.append("")
    lines.append("【整句纠错候选（只供参考）】")
    for source, translation in sorted(result.get("phrases", {}).items())[:40]:
        lines.append("  %s → %s" % (source, translation))
    return "\n".join(lines)
