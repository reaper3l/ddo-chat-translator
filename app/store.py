"""学习库：把"你纠正过的翻译"变成程序下次会用的知识。

分三层，越往下越"硬"：

1. 句子记忆（phrases）：一模一样的句子（归一化后指纹相同）→ 直接给中文，不调 API。
   你每纠正一次就立刻写进去，所以改过的句子下次一定是对的。
2. 术语记忆（terms）：你在词典/学习中心里确认过的单词译法 → 进术语表参与占位符保护。
3. prompt 偏好（prompt_phrases/prompt_terms）：同一句被你改过 >=N 次（或你手动标记），
   就把"你偏好这么翻"写进 system prompt，让模型对**新句子**也照着你的习惯来。

另外还收集"没见过的词"（candidates）：聊天里反复出现、术语表却查不到的词，
按出现次数排队，由你一键收编。这就是"通过分析聊天内容自我优化"的落点。
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Dict, Iterable, List, Optional

from . import paths, textutil

MAX_CORRECTIONS = 400
MAX_CANDIDATES = 2000
SAVE_INTERVAL = 2.0

DEFAULT_STATS = {
    "frames": 0,
    "messages": 0,
    "translated": 0,
    "memory_hits": 0,
    "cache_hits": 0,
    "api_calls": 0,
    "api_errors": 0,
    "corrections": 0,
    "untranslated": 0,
}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class MemoryStore:
    def __init__(self, path=None) -> None:
        self.path = path or paths.MEMORY_PATH
        self.data: Dict[str, object] = {
            "version": 1,
            "created": _now(),
            "updated": _now(),
            "phrases": {},
            "terms": {},
            "corrections": [],
            "candidates": {},
            "ignored": [],
            "stats": dict(DEFAULT_STATS),
        }
        self._dirty = False
        self._last_save = 0.0
        self.load()

    # ---------------------------------------------------------------- 持久化
    def load(self) -> None:
        raw = paths.read_json(self.path, {})
        if not isinstance(raw, dict):
            return
        for key, default in self.data.items():
            value = raw.get(key)
            if isinstance(default, dict) and isinstance(value, dict):
                merged = dict(default)
                merged.update(value)
                self.data[key] = merged
            elif isinstance(default, list) and isinstance(value, list):
                self.data[key] = value
            elif value is not None and not isinstance(default, (dict, list)):
                self.data[key] = value

    def mark_dirty(self) -> None:
        self._dirty = True
        self.data["updated"] = _now()

    def flush(self, force: bool = False) -> bool:
        if not self._dirty and not force:
            return False
        now = time.time()
        if not force and now - self._last_save < SAVE_INTERVAL:
            return False
        ok = paths.write_json(self.path, self.data)
        if ok:
            self._dirty = False
            self._last_save = now
        return ok

    # ------------------------------------------------------------ 句子记忆
    @property
    def _phrases(self) -> Dict[str, dict]:
        return self.data["phrases"]  # type: ignore[return-value]

    def phrase(self, source: str) -> Optional[str]:
        item = self._phrases.get(textutil.fingerprint(source))
        return item.get("zh") if item else None

    def set_phrase(self, source: str, zh: str, count: int = 1, stable: bool = True) -> None:
        fp = textutil.fingerprint(source)
        if not fp or not zh:
            return
        self._phrases[fp] = {
            "source": source,
            "zh": zh,
            "count": count,
            "stable": stable,
            "last": _now(),
        }
        self.mark_dirty()

    def learn_correction(self, source: str, before: str, after: str,
                         min_count: int = 2, fixed_source: str = "") -> dict:
        """记录一次人工纠正，返回这次学习的结果说明。

        `fixed_source`：用户在纠错窗口里把**英文原文**改对了（OCR 读错时用）。
        注意记忆的 key 仍然是**原来读到的那句**（`source`）——
        下次 OCR 读成同样的样子才命中；改过的英文只作为历史记录留着参考。
        """
        fp = textutil.fingerprint(source)
        previous = self._phrases.get(fp, {})
        count = int(previous.get("count", 0)) + 1
        same_as_before = previous.get("zh") == after
        if same_as_before:
            count = max(count, int(previous.get("count", 0)))

        self._phrases[fp] = {
            "source": source,
            "zh": after,
            "count": count,
            "stable": count >= min_count or bool(previous.get("stable")),
            "last": _now(),
        }

        corrections = self.data["corrections"]
        corrections.append({
            "time": _now(),
            "source": source,
            "before": before,
            "after": after,
            "fixed_source": str(fixed_source or "").strip(),
        })
        if len(corrections) > MAX_CORRECTIONS:
            del corrections[:-MAX_CORRECTIONS]

        self.bump("corrections")
        self.mark_dirty()
        return {
            "count": count,
            "stable": self._phrases[fp]["stable"],
            "exact_hit": True,
        }

    def phrase_rules(self, limit: int = 200) -> List[dict]:
        items = sorted(self._phrases.values(), key=lambda i: i.get("last", ""), reverse=True)
        return items[:limit]

    def delete_phrase(self, source: str) -> bool:
        fp = textutil.fingerprint(source)
        if fp in self._phrases:
            del self._phrases[fp]
            self.mark_dirty()
            return True
        return False

    # ------------------------------------------------------------ 术语记忆
    @property
    def _terms(self) -> Dict[str, dict]:
        return self.data["terms"]  # type: ignore[return-value]

    def set_term(self, term: str, zh: str, source: str = "user") -> None:
        term = (term or "").strip()
        zh = (zh or "").strip()
        if not term or not zh:
            return
        # 本机自保护：如果这是"公共词典"里的词、而用户给了不一样的译法，
        # 记一张否定票（改到几次就在本机停用它；下次贡献时把票带给作者）。
        try:
            from . import public_dict

            public_dict.record_negative(term, zh)
        except Exception:
            pass
        key = term.lower()
        previous = self._terms.get(key, {})
        self._terms[key] = {
            "text": term,
            "zh": zh,
            "count": int(previous.get("count", 0)) + 1,
            "source": source,
            "last": _now(),
        }
        self.mark_dirty()

    def delete_term(self, term: str) -> bool:
        key = (term or "").strip().lower()
        if key in self._terms:
            del self._terms[key]
            self.mark_dirty()
            return True
        return False

    def term_list(self) -> List[dict]:
        return sorted(self._terms.values(), key=lambda i: i.get("last", ""), reverse=True)

    # -------------------------------------------------------------- 候选词
    @property
    def _candidates(self) -> Dict[str, dict]:
        return self.data["candidates"]  # type: ignore[return-value]

    @property
    def _ignored(self) -> List[str]:
        return self.data["ignored"]  # type: ignore[return-value]

    def observe(self, tokens: Iterable[str], sample: str, problem: bool = False) -> None:
        """记录"没见过的词"。problem=True 表示这条消息当时没翻好（出错/原样返回），
        这种词在学习列表里会被优先展示。"""
        for token in tokens:
            token = (token or "").strip().lower()
            if len(token) < 3 or token in self._ignored:
                continue
            item = self._candidates.get(token)
            if item is None:
                item = {"count": 0, "problem": 0, "samples": [], "last": _now()}
                self._candidates[token] = item
            item["count"] = int(item.get("count", 0)) + 1
            if problem:
                item["problem"] = int(item.get("problem", 0)) + 1
            item["last"] = _now()
            samples = item.setdefault("samples", [])
            if sample and len(samples) < 2 and sample not in samples:
                samples.append(textutil.truncate(sample, 80))

        if len(self._candidates) > MAX_CANDIDATES:
            ordered = sorted(self._candidates.items(),
                             key=lambda kv: int(kv[1].get("count", 0)), reverse=True)
            self.data["candidates"] = dict(ordered[: MAX_CANDIDATES // 2])
        self._dirty = True

    def candidates(self, min_count: int = 3, limit: int = 200) -> List[dict]:
        items = [
            {"token": token, "count": int(item.get("count", 0)),
             "problem": int(item.get("problem", 0)),
             "samples": item.get("samples", []), "last": item.get("last", "")}
            for token, item in self._candidates.items()
            if int(item.get("count", 0)) >= min_count
        ]
        # 先看"翻不出来的次数"，再看总出现次数
        items.sort(key=lambda i: (-i["problem"], -i["count"], i["token"]))
        return items[:limit]

    def ignore_candidate(self, token: str) -> None:
        token = (token or "").strip().lower()
        if token and token not in self._ignored:
            self._ignored.append(token)
        self._candidates.pop(token, None)
        self.mark_dirty()

    def clear_candidates(self) -> None:
        self.data["candidates"] = {}
        self.mark_dirty()

    # ---------------------------------------------------------- prompt 素材
    def prompt_phrases(self, limit: int = 12) -> List[dict]:
        items = [i for i in self._phrases.values() if i.get("stable")]
        items.sort(key=lambda i: int(i.get("count", 0)), reverse=True)
        return items[:limit]

    def prompt_terms(self, limit: int = 25) -> List[dict]:
        items = list(self._terms.values())
        items.sort(key=lambda i: int(i.get("count", 0)), reverse=True)
        return items[:limit]

    # ---------------------------------------------------------------- 统计
    @property
    def stats(self) -> Dict[str, int]:
        return self.data["stats"]  # type: ignore[return-value]

    def bump(self, key: str, amount: int = 1) -> None:
        stats = self.stats
        stats[key] = int(stats.get(key, 0)) + amount

    # ------------------------------------------------------------ 导入导出
    def export_to(self, path) -> bool:
        return paths.write_json(paths.ensure_path(path), self.data)

    def import_from(self, path) -> dict:
        raw = paths.read_json(paths.ensure_path(path), {})
        if not isinstance(raw, dict):
            return {"phrases": 0, "terms": 0}
        added_phrases = added_terms = 0
        for fp, item in (raw.get("phrases") or {}).items():
            if fp not in self._phrases and isinstance(item, dict):
                self._phrases[fp] = item
                added_phrases += 1
        for key, item in (raw.get("terms") or {}).items():
            if isinstance(item, dict) and item.get("text") and item.get("zh"):
                self._terms[key] = item
                added_terms += 1
        for token, item in (raw.get("candidates") or {}).items():
            current = self._candidates.get(token)
            if current is None:
                self._candidates[token] = item
            else:
                current["count"] = int(current.get("count", 0)) + int(item.get("count", 0))
        self.mark_dirty()
        return {"phrases": added_phrases, "terms": added_terms}

    def summary(self) -> Dict[str, int]:
        return {
            "句子记忆": len(self._phrases),
            "术语记忆": len(self._terms),
            "待学习词": len(self._candidates),
            "已忽略词": len(self._ignored),
        }
