"""术语保护。

思路：把聊天里出现的游戏术语/俚语先替换成占位符（{{TERM_3}}），让模型只翻译
剩下的自然语言，翻完再把占位符换回中文术语。这样 "reaper / shrine / favor"
这类词不会被模型乱翻。

和旧版最大的区别：
* 占位符映射是**按条消息返回**的（不再挂在全局单例上），并发/乱序也不会串位。
* 匹配用"词表 + 1~4 元组查表"，不是给每个词编译一个正则再逐条 sub
  （旧版每条消息要跑 1900 次正则替换，纯浪费 CPU）。
* 顺带把"没见过的词"收集出来，喂给学习库。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from . import paths, textutil

# 绝不做术语保护的"普通英文词"。
# 旧版大词典里有 will→意志、die→死亡、normal→普通… 这类条目，一旦被保护，
# 模型只能逐词填空，会出现 "意志 只是在 死亡 驯服中" 这种碎句。
NEVER_PROTECT = set(textutil.STOPWORDS) | {
    "die", "dies", "died", "dead", "death", "dying", "kill", "kills", "killed",
    "will", "would", "shall", "should", "could", "might", "must", "may",
    "normal", "hard", "easy", "casual", "slow", "fast", "quick", "soft",
    "nice", "cool", "fun", "funny", "bad", "best", "worst", "worse", "great",
    "big", "small", "long", "short", "high", "low", "new", "old", "young",
    "left", "right", "front", "top", "bottom", "start", "stop", "end",
    "help", "work", "play", "talk", "tell", "ask", "look", "feel", "try",
    "use", "see", "hear", "run", "wait", "stay", "come", "go", "get",
    "time", "day", "night", "today", "tomorrow", "week", "month", "year",
    "man", "woman", "boy", "girl", "people", "person", "friend", "friends",
    "yes", "sure", "maybe", "well", "true", "false", "wrong", "same",
    "thing", "things", "stuff", "way", "place", "part", "side", "kind",
}

TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'\-]*|[^A-Za-z0-9]+")
WORD_START_RE = re.compile(r"[A-Za-z0-9]")
MARK_RE = re.compile(r"\{\{\s*TERM\s*_?\s*(\d+)\s*\}\}", re.IGNORECASE)
MARK_SPAN_RE = re.compile(r"\{\{\s*(?:TERM|URL)\s*_?\s*\d+\s*\}\}", re.IGNORECASE)
MAX_NGRAM = 4
MAX_SPAN_CHARS = 48


@dataclass
class TermMatch:
    start: int
    end: int
    term: str
    translation: str


class Glossary:
    """术语表：查表 + 占位符保护 + 还原。"""

    def __init__(self, terms: Optional[Dict[str, str]] = None,
                 patterns: Optional[Iterable[dict]] = None) -> None:
        self._lookup: Dict[str, Tuple[str, str]] = {}
        self._regexes: List[Tuple["re.Pattern[str]", str]] = []
        self.patterns = list(patterns or [])

        for term, translation in (terms or {}).items():
            self.add(term, translation)

        signature_src = "|".join(
            "%s=%s" % (key, value[1]) for key, value in sorted(self._lookup.items())
        )
        signature_src += "|".join(pattern.get("pattern", "") for pattern in self.patterns)
        self.signature = hashlib.sha1(signature_src.encode("utf-8")).hexdigest()[:12]

    # ------------------------------------------------------------------ 构建
    def add(self, term: str, translation: str) -> None:
        if not term or not translation:
            return
        key = self._norm_key(term)
        if not key:
            return
        if re.fullmatch(r"[a-z0-9'\- ]+", key):
            self._lookup[key] = (term, translation)
        else:
            # 含点号/斜杠等特殊字符（域名、R1-R10 之外的符号）→ 用正则匹配
            escaped = re.escape(term).replace(r"\ ", r"\s+")
            try:
                self._regexes.append(
                    (re.compile(escaped, re.IGNORECASE), translation)
                )
            except re.error:
                pass

    @staticmethod
    def _norm_key(term: str) -> str:
        return re.sub(r"\s+", " ", (term or "").strip().lower())

    def __len__(self) -> int:
        return len(self._lookup) + len(self._regexes)

    # ------------------------------------------------------------ 保护/还原
    def protect(self, text: str) -> Tuple[str, Dict[str, str], List[str]]:
        """返回 (打完占位符的文本, 占位符映射, 没见过的英文词)。

        映射只属于这一条消息，调用方自己拿着，翻完交给 restore。
        """
        mapping: Dict[str, str] = {}
        counter = [0]

        def alloc(translation: str) -> str:
            mark = "{{TERM_%d}}" % counter[0]
            counter[0] += 1
            mapping[mark] = translation
            return mark

        # 1) 配置里的正则规则（例如整条 steam 链接）
        masked = text
        for rule in self.patterns:
            pattern = rule.get("pattern")
            target = rule.get("replacement")
            if not pattern:
                continue
            try:
                masked = re.sub(pattern, target if target is not None else "", masked)
            except re.error:
                continue

        # 2) 特殊字符词条（含 . / 等）
        for regex, translation in self._regexes:
            masked = regex.sub(lambda _m, t=translation: alloc(t), masked)

        # 3) 普通词条：按 1~4 元组查表
        masked, unknown = self._protect_words(masked, alloc)
        return masked, mapping, unknown

    def _protect_words(self, text: str, alloc) -> Tuple[str, List[str]]:
        tokens = [(m.group(0), m.start(), m.end()) for m in TOKEN_RE.finditer(text)]
        mark_spans = [(m.start(), m.end()) for m in MARK_SPAN_RE.finditer(text)]

        def inside_mark(position: int) -> bool:
            # 上一步插入的 {{TERM_0}} 里的 TERM 不能被当成生词收走
            return any(start <= position < end for start, end in mark_spans)

        def crosses_mark(start: int, end: int) -> bool:
            return any(start < mark_end and end > mark_start
                       for mark_start, mark_end in mark_spans)

        # 已经被占位符（{{URL_0}} / {{TERM_0}}）占住的词不参与匹配，也不会被当生词
        word_positions = [i for i, tok in enumerate(tokens)
                          if WORD_START_RE.match(tok[0]) and not inside_mark(tok[1])]

        matches: List[TermMatch] = []
        unknown: List[str] = []

        index = 0
        while index < len(word_positions):
            hit: Optional[TermMatch] = None
            max_n = min(MAX_NGRAM, len(word_positions) - index)
            for size in range(max_n, 0, -1):
                chosen = word_positions[index:index + size]
                start = tokens[chosen[0]][1]
                end = tokens[chosen[-1]][2]
                if end - start > MAX_SPAN_CHARS:
                    continue
                if crosses_mark(start, end):
                    continue
                key = self._norm_key(" ".join(tokens[i][0] for i in chosen))
                found = self._lookup.get(key)
                if found is not None:
                    hit = TermMatch(start, end, found[0], found[1])
                    index += size
                    break
            if hit is not None:
                matches.append(hit)
                continue

            token = tokens[word_positions[index]][0]
            position = tokens[word_positions[index]][1]
            if (len(token) >= 3 and token.isalpha()
                    and token.lower() not in textutil.STOPWORDS
                    and not inside_mark(position)):
                unknown.append(token.lower())
            index += 1

        if not matches:
            return text, unknown

        pieces: List[str] = []
        cursor = 0
        for match in matches:
            pieces.append(text[cursor:match.start])
            pieces.append(alloc(match.translation))
            cursor = match.end
        pieces.append(text[cursor:])
        return "".join(pieces), unknown

    @staticmethod
    def restore(text: str, mapping: Dict[str, str]) -> str:
        if not text:
            return ""
        if not mapping:
            return textutil.strip_leftover_marks(text)

        def repl(match: "re.Match[str]") -> str:
            key = "{{TERM_%s}}" % match.group(1)
            return mapping.get(key, "")

        return textutil.strip_leftover_marks(MARK_RE.sub(repl, text)).strip()

    # ------------------------------------------------------------------ 工具
    def terms(self) -> Dict[str, str]:
        """返回 术语 -> 中文 的字典（给设置/词典界面用）。"""
        result: Dict[str, str] = {}
        for term, translation in self._lookup.values():
            result[term] = translation
        for regex, translation in self._regexes:
            result[regex.pattern] = translation
        return result


def build_glossary(config: dict, memory=None) -> Glossary:
    """按配置组装术语表。

    优先级（后者覆盖前者）：
        扩展表（旧版大词典） < 内置精选表 < 公共词典 < 用户学习到的。
    旧版那 1900 条里有不少和精选表冲突的（例如 tr=缠根 vs tr=真轮回、
    elite=精英 vs elite=精英难度），所以内置精选必须压过它。

    公共词典（`app/public_dict.py`，从网上下载的签名词表）**只做加法**：
    同名的一律跳过，绝不改掉内置精选表和用户自己的译法。这样联网下发的内容
    最坏只是"多了一条新词"，不会动到既有行为。
    """
    if not config.get("use_glossary", True):
        return Glossary({}, [])

    terms: Dict[str, str] = {}

    base = paths.read_json(paths.GLOSSARY_PATH, {})
    base_terms = (base.get("terms", {}) or {}) if isinstance(base, dict) else {}
    patterns = list(base.get("patterns", []) or []) if isinstance(base, dict) else []

    if config.get("use_extra_glossary", True):
        extra = paths.read_json(paths.GLOSSARY_EXTRA_PATH, {})
        if isinstance(extra, dict):
            for term, translation in extra.items():
                # 译文里没有中文的条目（旧表里有一些）只会把英文原样留在句子里，跳过
                if not isinstance(translation, str) or not textutil.has_cjk(translation):
                    continue
                key = Glossary._norm_key(term)
                if " " not in key:
                    # 单个普通英文词不做保护（否则句子会被拆成逐词直译）
                    if key in NEVER_PROTECT:
                        continue
                    # 1~2 个字母的老条目多半是 OCR 噪音（aa/fs/zs…），精选表里已覆盖常用缩写
                    if len(key) < 3:
                        continue
                terms[term] = translation

    terms.update(base_terms)

    if config.get("public_dict_enabled", True):
        from . import public_dict

        existing = {Glossary._norm_key(term) for term in terms}
        for term, translation in public_dict.load_terms(config).items():
            key = Glossary._norm_key(term)
            if not key or key in existing:
                continue                     # 已有同名（内置/大词典）→ 不覆盖
            if not isinstance(translation, str) or not textutil.has_cjk(translation):
                continue                     # 和内置表同样的门槛：译文必须有中文
            if " " not in key and (key in NEVER_PROTECT or len(key) < 3):
                continue                     # 单个普通英文词/缩写噪音不做保护
            terms[term] = translation
            existing.add(key)

    if memory is not None:
        for item in getattr(memory, "data", {}).get("terms", {}).values():
            term = item.get("text")
            translation = item.get("zh")
            if term and translation:
                terms[term] = translation        # 用户学习到的优先级最高

    return Glossary(terms, patterns)
