"""翻译服务门面：给"界面之外"的调用方用（本地平台 API、插件、命令行）。

为什么单独一层：界面（Tk）和外部调用要走**同一套**流程 ——
术语保护 → 查缓存 → 调引擎 → 还原 → 记账/学习。否则会出现两种坏情况：
界面里翻过的句子插件再问一次还得花钱；插件翻过的句子界面学不到。

和 app/pipeline.py 的关系：**共用同一份资产**
  * 术语表：`build_glossary(config, memory)`（内置精选表 + 词典源 + 用户自己的词）
  * 记忆库：`MemoryStore`（句子记忆 / 术语 / 待学习词 / 纠错历史）
  * 翻译缓存：`data/cache.json`，**缓存键算法保持一致**（masked + 引擎 + 模式），
    所以两边互相命中：界面翻过的，插件不再花钱；插件翻过的，界面直接用。

本模块**不依赖 tkinter**，可以在 `--serve`（无界面）模式下单独跑。
"""
from __future__ import annotations

import hashlib
import threading
import time
from collections import deque
from typing import Dict, List, Optional, Sequence

from . import config as config_module
from . import paths, textutil
from .engines import create_engine
from .glossary import build_glossary
from .pipeline import Pipeline
from .prompt import (build_batch_messages, build_system_prompt,
                     build_zh2en_system_prompt,
                     build_messages, parse_batch_reply)
from .store import MemoryStore

# 缓存容量：和界面（Pipeline）保持同一量级，避免两边互删
CACHE_PERSIST_ITEMS = 3000
MAX_TEXT_CHARS = 4000          # 单条上限：防止插件把一整本书塞进来


class TranslatorService:
    """翻译能力门面。线程安全（HTTP 服务是多线程的）。"""

    def __init__(self, config: Optional[dict] = None, memory=None, engine=None,
                 glossary=None, cache_path=None) -> None:
        self.config = dict(config) if config is not None else config_module.load_config()
        self.memory = memory if memory is not None else MemoryStore()
        self.glossary = (glossary if glossary is not None
                         else build_glossary(self.config, self.memory))
        # 缓存文件：默认和界面共用 data/cache.json；测试/特殊场景可以换一个
        self.cache_path = paths.CACHE_PATH if cache_path is None else cache_path
        if engine is not None:
            self.engine = engine
            self.engine_note = "注入的引擎"
        else:
            self.engine, self.engine_note = create_engine(self.config)
        self._lock = threading.RLock()
        self._cache: Dict[str, dict] = {}
        self._cache_dirty = False
        self._history: deque = deque(maxlen=12)
        self.stats = {"api_calls": 0, "api_errors": 0, "cache_hits": 0, "memory_hits": 0,
                      "dict_hits": 0, "translated": 0, "batched": 0, "chars_in": 0,
                      "requests": 0}
        self._load_cache()

    # ------------------------------------------------------------------ 缓存
    def _load_cache(self) -> None:
        raw = paths.read_json(self.cache_path, {})
        items = {}
        if isinstance(raw, dict) and isinstance(raw.get("items"), dict):
            for key, value in raw["items"].items():
                if isinstance(value, dict) and isinstance(value.get("zh"), str):
                    items[key] = {"src": str(value.get("src") or ""), "zh": value["zh"]}
                elif isinstance(value, str):      # 老格式
                    items[key] = {"src": "", "zh": value}
        self._cache = items

    def flush_cache(self) -> None:
        """写回缓存。写之前**先合并磁盘上的新内容**，免得和同时开着的界面互相覆盖。"""
        if not self._cache_dirty:
            return
        # 先读一遍磁盘上的（可能是界面进程刚写的），再把自己的合并上去
        disk = paths.read_json(self.cache_path, {})
        merged = {}
        if isinstance(disk, dict) and isinstance(disk.get("items"), dict):
            merged.update(disk["items"])
        merged.update(self._cache)
        if len(merged) > CACHE_PERSIST_ITEMS:
            merged = dict(list(merged.items())[-CACHE_PERSIST_ITEMS:])
        if paths.write_json(self.cache_path, {"version": 1, "items": merged}):
            self._cache_dirty = False

    def _cache_key(self, masked: str, mode: str) -> str:
        """和 Pipeline._cache_key 一致（masked + 引擎 + 模式）——两边共用同一份缓存。"""
        parts = [masked, self.engine.describe(), str(mode)]
        return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------ 术语
    def reload_glossary(self) -> None:
        with self._lock:
            self.glossary = build_glossary(self.config, self.memory)

    def lookup(self, term: str) -> dict:
        term = str(term or "").strip()
        if not term:
            return {"ok": False, "error": "term 不能为空"}
        terms = self.glossary.terms()
        hit = terms.get(term)
        if hit is None:                        # 大小写/空格不敏感地再找一遍
            wanted = term.lower()
            for key, value in terms.items():
                if str(key).strip().lower() == wanted:
                    hit, term = value, str(key)
                    break
        mine = self.memory.term_list()
        return {"ok": True, "term": term, "zh": hit or "",
                "found": bool(hit),
                "mine": any(str(item.get("term", "")).strip().lower() == term.lower()
                            for item in mine)}

    def add_term(self, term: str, zh: str, source: str = "plugin") -> dict:
        term, zh = str(term or "").strip(), str(zh or "").strip()
        if not term or not zh:
            return {"ok": False, "error": "term / zh 都不能为空"}
        with self._lock:
            self.memory.set_term(term, zh, source=source)
            self.memory.flush(force=True)
            self.reload_glossary()
        return {"ok": True, "term": term, "zh": zh, "applies": "本机立即生效"}

    # ------------------------------------------------------------------ 翻译
    @staticmethod
    def _char_limit(max_chars: Optional[int]) -> int:
        """单条文本的上限。平台可以给更小的值（插件不该一次塞一大段），但不会更大。"""
        if max_chars is None:
            return MAX_TEXT_CHARS
        try:
            wanted = int(max_chars)
        except Exception:
            return MAX_TEXT_CHARS
        return max(1, min(MAX_TEXT_CHARS, wanted))

    def direction_for(self, text: str, direction: str = "auto") -> str:
        mode = str(direction or "auto").lower()
        if mode in ("en2zh", "zh2en"):
            return mode
        return textutil.resolve_direction(text, "auto")

    def attach(self, config: dict, glossary=None, memory=None) -> None:
        """界面改了设置 / 重建了术语表之后，把新的一份接过来。

        界面和平台必须共用**同一份**资产（术语表、记忆库、缓存），否则会出现
        "界面里学到的纠错，插件问同样的句子还得再花钱"这类割裂。
        """
        with self._lock:
            self.config = dict(config)
            if memory is not None:
                self.memory = memory
            if glossary is not None:
                self.glossary = glossary
            else:
                self.glossary = build_glossary(self.config, self.memory)
            self.engine, self.engine_note = create_engine(self.config)

    def translate(self, text: str, direction: str = "auto", context: Sequence[str] = (),
                  speaker: str = "", channel: str = "", timeout: Optional[float] = None,
                  learn: bool = True, max_chars: Optional[int] = None) -> dict:
        """翻一条。返回带诊断信息的 dict（zh / 命中来源 / 用了哪些术语 / 是否花钱）。"""
        source = str(text or "").strip()
        if not source:
            return {"ok": False, "error": "text 不能为空"}
        limit = self._char_limit(max_chars)
        truncated = False
        if len(source) > limit:
            source = source[:limit]
            truncated = True
        started = time.perf_counter()
        with self._lock:
            self.stats["requests"] += 1
            self.stats["chars_in"] += len(source)
            resolved = self.direction_for(source, direction)
            if resolved == "zh2en":
                result = self._translate_zh2en(source, timeout)
            else:
                result = self._translate_en2zh(source, context, speaker, channel,
                                              timeout, learn)
            result["direction"] = resolved
            result["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
            if truncated:
                result["truncated"] = True
            return result

    def translate_many(self, texts: Sequence[str], direction: str = "auto",
                       context: Sequence[str] = (), timeout: Optional[float] = None,
                       max_chars: Optional[int] = None) -> dict:
        """翻一批（能合并成一次请求就合并，省钱）。返回 {"results": [...]}。"""
        limit = self._char_limit(max_chars)
        items = [str(t or "").strip()[:limit] for t in texts]
        if not items:
            return {"ok": False, "error": "texts 不能为空"}
        with self._lock:
            resolved = self.direction_for(items[0], direction)
        if resolved == "zh2en" or len(items) == 1:
            out = [self.translate(item, resolved, context, timeout=timeout) for item in items]
            return {"ok": True, "results": out, "engine": self.engine.describe()}

        prepared = []
        out: List[dict] = [{} for _ in items]
        for index, item in enumerate(items):
            local, pending = self._prepare_en2zh(item, context, "")
            if local is not None:
                out[index] = local
                prepared.append(None)
            else:
                prepared.append(pending)

        todo = [(index, pending) for index, pending in enumerate(prepared) if pending]
        batch_limit = max(1, int(self.config.get("batch_max", 3) or 3))
        use_batch = bool(self.config.get("batch_translate", True)) and len(todo) > 1
        if use_batch:
            for start in range(0, len(todo), batch_limit):
                group = todo[start:start + batch_limit]
                translated = self._engine_batch([pending for _i, pending in group], timeout)
                for (index, pending), (text, ok, error) in zip(group, translated):
                    if ok:
                        out[index] = self._finish_en2zh(pending, text, True, "")
                    else:
                        out[index] = self._engine_one(pending, timeout, error)
        else:
            for index, pending in todo:
                out[index] = self._engine_one(pending, timeout)
        for index, item in enumerate(out):
            if not item:
                out[index] = {"ok": False, "zh": items[index], "error": "没有结果"}
            out[index]["direction"] = "en2zh"
        return {"ok": True, "results": out, "engine": self.engine.describe()}

    # ---------------------------------------------------- 英→中（带术语/缓存）
    def _prepare_en2zh(self, source: str, context: Sequence[str], speaker: str):
        """本地能出结果就直接返回；否则返回待办 dict（照抄 Pipeline._prepare 的顺序）。"""
        if not textutil.has_latin(source):
            return {"ok": True, "zh": source, "source": source, "note": "原文非英文",
                    "api_calls": 0}, None
        remembered = self.memory.phrase(source)
        if remembered:
            self.stats["memory_hits"] += 1
            return {"ok": True, "zh": remembered, "source": source, "note": "记忆命中",
                    "api_calls": 0}, None
        protected, urls = textutil.protect_urls(source)
        masked, mapping, unknown = self.glossary.protect(protected)
        if not textutil.has_latin_outside_marks(masked):
            self.stats["dict_hits"] += 1
            return None, {"source": source, "masked": masked, "mapping": mapping,
                          "urls": urls, "unknown": unknown, "cache_key": "",
                          "raw": masked, "note": "词典直译", "context": tuple(context),
                          "speaker": speaker}
        mode = str(self.config.get("translate_mode", "quality"))
        cache_key = self._cache_key(masked, mode)
        cached = self._cache.get(cache_key)
        if isinstance(cached, dict) and cached.get("zh"):
            self.stats["cache_hits"] += 1
            return None, {"source": source, "masked": masked, "mapping": mapping,
                          "urls": urls, "unknown": unknown, "cache_key": cache_key,
                          "raw": str(cached["zh"]), "note": "缓存",
                          "context": tuple(context), "speaker": speaker}
        return None, {"source": source, "masked": masked, "mapping": mapping,
                      "urls": urls, "unknown": unknown, "cache_key": cache_key,
                      "context": tuple(context), "speaker": speaker}

    def _translate_en2zh(self, source: str, context: Sequence[str], speaker: str,
                         channel: str, timeout: Optional[float], learn: bool) -> dict:
        local, pending = self._prepare_en2zh(source, context, speaker)
        if local is not None:
            return local
        assert pending is not None
        return self._engine_one(pending, timeout)

    def _messages_for(self, pending: dict) -> List[dict]:
        mode = str(self.config.get("translate_mode", "quality"))
        turns = max(0, int(self.config.get("context_turns", 5)))
        history = list(self._history)
        history = history[-turns * 2:] if turns else []
        # 插件给的上下文是"前面几句原文"，成对结构里译文还不知道 → 用空串占位
        extra = tuple((str(item), "") for item in (pending.get("context") or ()))
        context = tuple(history) + extra
        return build_messages(build_system_prompt(mode, self.memory), context, (), 
                              pending["masked"])

    def _engine_one(self, pending: dict, timeout: Optional[float],
                    previous_error: str = "") -> dict:
        # 本地已经出结果（词典直译 / 缓存命中 / 记忆命中）→ 不再调接口
        if pending.get("raw") is not None:
            return self._finish_en2zh(pending, str(pending["raw"]), True, "", api_calls=0)
        call_timeout = float(timeout or self.config.get("timeout_seconds", 20))
        try:
            result = self.engine.translate(pending["masked"], self._messages_for(pending),
                                           timeout=call_timeout)
        except Exception as exc:                      # noqa: BLE001
            self.stats["api_errors"] += 1
            return {"ok": False, "zh": pending["source"], "source": pending["source"],
                    "error": str(exc) or "调用引擎失败", "api_calls": 0}
        self.stats["api_calls"] += 1
        if not getattr(result, "ok", False):
            self.stats["api_errors"] += 1
            reason = getattr(result, "error", "") or previous_error or "翻译失败"
            return {"ok": False, "zh": pending["source"], "source": pending["source"],
                    "error": reason, "api_calls": 1}
        return self._finish_en2zh(pending, str(getattr(result, "text", "") or ""),
                                  True, "", api_calls=1)

    def _engine_batch(self, pendings: List[dict], timeout: Optional[float]) -> List[tuple]:
        """一次请求翻多条（复用界面那套 build_batch_messages/parse_batch_reply）。"""
        mode = str(self.config.get("translate_mode", "quality"))
        turns = max(0, int(self.config.get("context_turns", 5)))
        history = list(self._history)
        context = tuple(history[-turns * 2:]) if turns else ()
        texts = [pending["masked"] for pending in pendings]
        messages = build_batch_messages(build_system_prompt(mode, self.memory), context,
                                        (), texts)
        call_timeout = float(timeout or self.config.get("timeout_seconds", 20))
        try:
            result = self.engine.translate(
                "\n".join("[%d] %s" % (index, text) for index, text in enumerate(texts, 1)),
                messages, timeout=call_timeout)
        except Exception as exc:                      # noqa: BLE001
            self.stats["api_errors"] += 1
            return [(text, False, str(exc) or "调用引擎失败") for text in texts]
        self.stats["api_calls"] += 1
        if not getattr(result, "ok", False):
            self.stats["api_errors"] += 1
            reason = getattr(result, "error", "") or "翻译失败"
            return [(text, False, reason) for text in texts]
        parts = parse_batch_reply(str(getattr(result, "text", "") or ""), len(texts))
        out = []
        for text, piece in zip(texts, parts):
            out.append((piece, True, "") if piece else (text, False, "批量结果里缺这一行"))
        self.stats["batched"] += sum(1 for _t, ok, _e in out if ok)
        return out

    def _finish_en2zh(self, pending: dict, translated: str, ok: bool, error: str,
                      api_calls: int = 1) -> dict:
        """还原术语/链接、记账、写缓存、顺手学习（和 Pipeline._finish 同一套行为）。"""
        source = pending["source"]
        raw = translated or ""
        note = str(pending.get("note") or "")
        text = textutil.restore_urls(raw, pending["urls"])
        text = self.glossary.restore(text, pending["mapping"])
        text = Pipeline._post_process(text)          # 同一个后处理（去引号/“译文:”等）
        if not text:
            text = source
        if ok and textutil.is_refusal(text) and textutil.has_latin(source):
            text, note = source, "模型没看懂（显示原文）"
        if ok:
            if pending.get("cache_key"):
                self._cache[pending["cache_key"]] = {"src": source, "zh": raw}
                self._cache_dirty = True
            self._history.append((source, text))
            self.stats["translated"] += 1
        if pending.get("unknown"):
            problem = bool(error) or (not textutil.has_cjk(text)
                                      and textutil.has_latin(text))
            try:
                self.memory.observe(pending["unknown"], source, problem=problem)
            except Exception:                        # noqa: BLE001
                pass
        terms = [[str(term), str(zh)] for term, zh in (pending.get("mapping") or {}).items()]
        return {"ok": bool(ok), "zh": text, "source": source, "note": note,
                "error": error, "api_calls": int(api_calls) if ok else 0,
                "cached": note == "缓存", "terms": terms}

    # ---------------------------------------------------- 中→英（给玩家交流用）
    def _translate_zh2en(self, source: str, timeout: Optional[float]) -> dict:
        mode = str(self.config.get("translate_mode", "quality"))
        cache_key = self._cache_key(source, "zh2en|" + mode)
        cached = self._cache.get(cache_key)
        if isinstance(cached, dict) and cached.get("zh"):
            self.stats["cache_hits"] += 1
            return {"ok": True, "zh": str(cached["zh"]), "source": source,
                    "note": "缓存", "api_calls": 0, "cached": True}
        messages = [{"role": "system", "content": build_zh2en_system_prompt(self.memory)},
                    {"role": "user", "content": source}]
        call_timeout = float(timeout or self.config.get("timeout_seconds", 20))
        try:
            result = self.engine.translate(source, messages, timeout=call_timeout)
        except Exception as exc:                      # noqa: BLE001
            self.stats["api_errors"] += 1
            return {"ok": False, "zh": source, "source": source,
                    "error": str(exc) or "调用引擎失败", "api_calls": 0}
        self.stats["api_calls"] += 1
        if not getattr(result, "ok", False):
            self.stats["api_errors"] += 1
            return {"ok": False, "zh": source, "source": source,
                    "error": getattr(result, "error", "") or "翻译失败", "api_calls": 1}
        text = Pipeline._post_process(str(getattr(result, "text", "") or ""))
        if text:
            self._cache[cache_key] = {"src": source, "zh": text}
            self._cache_dirty = True
            self.stats["translated"] += 1
        return {"ok": True, "zh": text or source, "source": source, "note": "",
                "api_calls": 1}

    # ------------------------------------------------------------------ 学习
    def feedback(self, source: str, before: str, after: str,
                 remember: bool = True) -> dict:
        """报告"这句更好的译法"。= 界面里 F10 纠错那套，本机立即生效。"""
        source, after = str(source or "").strip(), str(after or "").strip()
        if not source or not after:
            return {"ok": False, "error": "source / after 都不能为空"}
        result = self.memory.learn_correction(source, str(before or ""), after,
                                             remember=bool(remember))
        self.memory.flush(force=True)
        return {"ok": True, "learned": "phrase", "remember": bool(remember),
                "applies": "本机立即生效",
                "detail": result if isinstance(result, dict) else {}}

    # ------------------------------------------------------------------ 状态
    def status(self) -> dict:
        from . import __version__

        return {
            "ok": True,
            "version": __version__,
            "engine": self.engine.describe(),
            "engine_note": self.engine_note,
            "engine_available": bool(self.engine.available()),
            "terms": len(self.glossary),
            "terms_mine": len(self.memory.term_list()),
            "cache": len(self._cache),
            "memory_phrases": len(self.memory.phrase_rules()),
            "stats": dict(self.stats),
            "capabilities": {
                "directions": ["en2zh", "zh2en", "auto"],
                "batch": True,
                "feedback": True,
                "terms": True,
                "chat": bool(getattr(self.engine, "supports_chat", False)),
                "max_text_chars": MAX_TEXT_CHARS,
            },
        }
