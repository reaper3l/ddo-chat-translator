"""高频短语挖掘：从本机已有的"英文 → 中文"记录里，找出值得收进术语表的词组。

为什么做这个：DDO 聊天里大量是固定说法（on my way / need heals / pop side…）。
这些词组如果进了术语表：
  * **整句正好就是这个词组**时（"omw"、"need heals" 这种短句非常多），
    程序能直接拼出中文、**完全不用调用接口**；
  * 其它句子里也能保证"同一个说法永远同一个译法"，读起来更稳、返工更少。

数据从哪来：本机已有的记录，不需要新采集 ——
  * `pipeline` 的翻译缓存（原文 + 译文，机器自己翻译过的）；
  * 用户自己 F10 改过的句子（`memory.phrases`）。

三步走，中间只有第二步花一次接口钱：
  1. `mine()`：本地统计，找出反复出现的词组（免费、瞬间完成）；
  2. `build_messages()` + `parse_reply()`：把这一批候选**一次**发给模型配中文
     （一次请求 ≈ 一条消息的钱，几十个词组一起问）；
  3. 用户扫一眼采纳（或者在设置里打开"自动采纳高置信度"，见 main_window 的定时任务）。

为什么默认不让它完全自动入库：术语是**影响所有句子**的东西，宁可让用户扫一眼。
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Sequence, Tuple

from . import textutil

MIN_WORDS = 2
MAX_WORDS = 3           # 再长的词组在聊天里很少成句，收益低、误收风险高
DEFAULT_MIN_COUNT = 3   # 至少出现在这么多句**不同的**英文里
SAMPLE_LIMIT = 2

_TOKEN_RE = re.compile(r"[a-z][a-z0-9'\-]{1,20}")


def _tokens(text: str) -> List[str]:
    """英文切成小写词（只留字母开头的词，OCR 噪音和数字不参与）。"""
    return _TOKEN_RE.findall((text or "").lower())


def _all_stopwords(words: Sequence[str]) -> bool:
    """整组都是虚词/短词（"of the"、"i am"、"to the"）→ 没有收录价值。

    注意这里**只**用真正的虚词表（`textutil.STOPWORDS`），不能用客户端的
    `glossary.NEVER_PROTECT`：那张表是"单个词不做保护"的名单，里面连
    `way` / `need` 这类词都有 —— 拿它来卡词组，会把 `on my way` 这种
    正想要的东西一起毙掉（写这个模块时踩过）。
    客户端的过滤本身也只针对**单个**英文词（多词词组照收）。
    """
    return not any(len(word) >= 3 and word not in textutil.STOPWORDS
                   for word in words)


def pairs_from_cache(cache: Dict[str, dict]) -> List[Tuple[str, str]]:
    """从翻译缓存里取 (原文, 译文)；顺手丢掉太短/空的。"""
    out: List[Tuple[str, str]] = []
    for value in (cache or {}).values():
        if not isinstance(value, dict):
            continue
        source = str(value.get("src") or "").strip()
        zh = str(value.get("zh") or "").strip()
        if source and zh and textutil.has_cjk(zh):
            out.append((source, zh))
    return out


def pairs_from_memory(memory) -> List[Tuple[str, str]]:
    """用户自己 F10 改过的句子（这些是"他自己确认过"的，质量最高）。"""
    out: List[Tuple[str, str]] = []
    try:
        data = getattr(memory, "data", {}) or {}
        for item in (data.get("phrases") or {}).values():
            source = str(item.get("source") or "").strip()
            zh = str(item.get("zh") or "").strip()
            if source and zh:
                out.append((source, zh))
    except Exception:                              # noqa: BLE001
        return []
    return out


def mine(pairs: Iterable[Tuple[str, str]], known: Iterable[str] = (),
         min_count: int = DEFAULT_MIN_COUNT, max_words: int = MAX_WORDS,
         limit: int = 60) -> List[dict]:
    """找出反复出现的英文词组 → [{phrase, count, samples, words}]。

    规则（都是为了"别把垃圾塞进术语表"）：
    * 只看 2~max_words 个**连续**词；
    * 整组不能全是停用词；
    * 同一个词组要出现在 ≥min_count 句**不同的**英文里（同一句重复不算）；
    * 已经在术语表/内置表里的跳过；
    * 同一句里，如果**更长的**词组已经在术语表里，它的子词组也不收
      （否则 "on my way" 已收录时，会冒出没用的 "my way"）；
    * 长词组优先：长词组的出现次数不低于它的子词组时，只留长的
      （留 "on my way"，丢掉 "my way"、"way"）。
    """
    known_keys = {re.sub(r"\s+", " ", str(item or "").strip().lower())
                  for item in (known or [])}
    stats: Dict[str, dict] = {}
    for index, pair in enumerate(pairs or []):
        source = pair[0] if isinstance(pair, (tuple, list)) and pair else ""
        words = _tokens(source)
        if len(words) < MIN_WORDS:
            continue
        # 这一句里"已经是术语"的更长词组：它们的子词组不用再挖
        known_spans = []
        for size in range(MIN_WORDS, max_words + 1):
            for start in range(0, len(words) - size + 1):
                if " ".join(words[start:start + size]) in known_keys:
                    known_spans.append((start, start + size))
        for size in range(MIN_WORDS, max_words + 1):
            for start in range(0, len(words) - size + 1):
                chunk = words[start:start + size]
                phrase = " ".join(chunk)
                if phrase in known_keys or _all_stopwords(chunk):
                    continue
                if any(lo <= start and start + size <= hi
                       for lo, hi in known_spans if hi - lo > size):
                    continue
                entry = stats.setdefault(phrase, {"count": 0, "seen": set(),
                                                  "samples": []})
                if index in entry["seen"]:          # 同一句里出现多次只算一次
                    continue
                entry["seen"].add(index)
                entry["count"] += 1
                if len(entry["samples"]) < SAMPLE_LIMIT:
                    entry["samples"].append(str(source).strip())

    candidates = [{"phrase": phrase, "count": data["count"],
                   "samples": data["samples"], "words": len(phrase.split())}
                  for phrase, data in stats.items()
                  if data["count"] >= min_count]
    # 长词组优先 + 去掉被更长的候选完全包住的（出现次数不高于它的）子词组
    candidates.sort(key=lambda item: (-item["words"], -item["count"], item["phrase"]))
    kept: List[dict] = []
    for item in candidates:
        phrase = item["phrase"]
        swallowed = False
        for big in kept:
            if big["count"] >= item["count"] and re.search(
                    r"(?:^|\s)%s(?:\s|$)" % re.escape(phrase), big["phrase"]):
                swallowed = True
                break
        if not swallowed:
            kept.append(item)
    kept.sort(key=lambda item: (-item["count"], -item["words"], item["phrase"]))
    return kept[:max(1, int(limit))]


GLOSSARY_SYSTEM = (
    "你是《龙与地下城 Online》(DDO) 的中英术语整理助手。"
    "用户会给你一批从游戏聊天记录里挑出来的英文词组，请为每个词组给出"
    "简洁、玩家常用中文说法。\n"
    "要求：\n"
    "1. 每个词组一行，格式严格是「序号. 中文」；\n"
    "2. 只写中文本身：不要解释、不要标点、不要重复英文、不要拼音；\n"
    "3. 中文尽量短（不超过 12 个字），用游戏里常见的说法，例如 "
    "omw=马上到、rez=复活、shroud=幽影堡；\n"
    "4. 拿不准就写最直白的意思，不要编造；\n"
    "5. 行数、序号必须与输入一一对应，不要多也不要少。"
)


def build_messages(phrases: Sequence[str]) -> List[dict]:
    """把候选词组拼成"一次问完"的请求（几十个词组只花一次调用）。"""
    lines = ["%d. %s" % (index, str(phrase).strip())
             for index, phrase in enumerate(phrases or [], 1)]
    return [{"role": "system", "content": GLOSSARY_SYSTEM},
            {"role": "user", "content": "\n".join(lines)}]


def parse_reply(text: str, phrases: Sequence[str]) -> Dict[str, str]:
    """把「序号. 中文」解析回 {词组: 中文}；格式不对的行**丢掉**（宁可少，不要错）。"""
    out: Dict[str, str] = {}
    items = list(phrases or [])
    for raw in (text or "").splitlines():
        line = raw.strip().lstrip("*-•　 ").strip()
        match = re.match(r"^(\d{1,3})\s*[.、:：)）]\s*(.+)$", line)
        if not match:
            continue
        index = int(match.group(1)) - 1
        if not (0 <= index < len(items)):
            continue
        zh = match.group(2).strip().strip("。.，,;；!！?？")
        if not zh or len(zh) > 20:
            continue
        if not textutil.has_cjk(zh) or textutil.has_latin(zh):
            continue                                # 没翻出来、或者夹着英文 → 不用
        out[str(items[index]).strip()] = zh
    return out
