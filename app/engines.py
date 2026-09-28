"""翻译引擎：DeepSeek（主力）、MyMemory（免费无 Key）、离线（纯术语表）。

所有引擎统一返回 TranslationResult，永远不抛异常——出错也把原文带回去，
由界面负责显示错误信息，绝不让后台线程死掉。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

import requests


@dataclass
class TranslationResult:
    text: str
    ok: bool
    engine: str
    error: str = ""
    elapsed: float = 0.0


class BaseEngine:
    name = "base"
    # 能不能"跟模型对话"（推荐回复、按上下文润色需要它；纯翻译接口做不到）
    supports_chat = False

    def available(self) -> bool:
        return False

    def describe(self) -> str:
        return self.name

    def translate(self, text: str, messages: Optional[Sequence[Dict[str, str]]] = None,
                  timeout: float = 20.0) -> TranslationResult:
        raise NotImplementedError


class OfflineEngine(BaseEngine):
    """不联网：术语表已经把词换成中文了，这里直接把结果交回去。"""

    name = "offline"

    def available(self) -> bool:
        return True

    def describe(self) -> str:
        return "离线术语表"

    def translate(self, text, messages=None, timeout: float = 20.0) -> TranslationResult:
        return TranslationResult(text=text, ok=True, engine=self.name)


class DeepSeekEngine(BaseEngine):
    name = "deepseek"
    supports_chat = True
    URL = "https://api.deepseek.com/v1/chat/completions"

    def __init__(self, api_key: str = "", model: str = "deepseek-chat") -> None:
        self.api_key = (api_key or "").strip()
        self.model = (model or "deepseek-chat").strip() or "deepseek-chat"

    def available(self) -> bool:
        return bool(self.api_key)

    def describe(self) -> str:
        return "DeepSeek(%s)" % self.model

    def translate(self, text: str, messages: Optional[Sequence[Dict[str, str]]] = None,
                  timeout: float = 20.0) -> TranslationResult:
        if not self.api_key:
            return TranslationResult(text, False, self.name, "未填写 DeepSeek API Key")
        if not messages:
            messages = [{"role": "user", "content": text}]

        payload = {
            "model": self.model,
            "messages": list(messages),
            "temperature": 0.2,
            "max_tokens": 400,
            "stream": False,
        }
        headers = {
            "Authorization": "Bearer %s" % self.api_key,
            "Content-Type": "application/json",
        }

        last_error = ""
        for attempt in range(2):
            started = time.time()
            try:
                response = requests.post(self.URL, headers=headers, json=payload,
                                         timeout=timeout)
                elapsed = time.time() - started
                if response.status_code == 200:
                    data = response.json()
                    content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
                    content = (content or "").strip()
                    if not content:
                        last_error = "模型返回空内容"
                        continue
                    return TranslationResult(content, True, self.name, elapsed=elapsed)

                body = (response.text or "")[:200]
                if response.status_code in (401, 403):
                    return TranslationResult(text, False, self.name,
                                             "API Key 无效或无权限（HTTP %d）" % response.status_code)
                if response.status_code == 402:
                    return TranslationResult(text, False, self.name, "API 余额不足（HTTP 402）")
                if response.status_code == 429:
                    last_error = "请求过于频繁（HTTP 429）"
                else:
                    last_error = "HTTP %d：%s" % (response.status_code, body)
            except requests.Timeout:
                last_error = "请求超时（%.0f 秒）" % timeout
            except Exception as exc:                      # 网络/解析异常一律兜住
                last_error = "请求失败：%s" % exc

            if attempt == 0:
                time.sleep(1.2)

        return TranslationResult(text, False, self.name, last_error)


class MyMemoryEngine(BaseEngine):
    """免费、无需 Key。质量一般，胜在没有门槛，适合试玩/兜底。"""

    name = "mymemory"
    URL = "https://api.mymemory.translated.net/get"

    def available(self) -> bool:
        return True

    def describe(self) -> str:
        return "MyMemory 免费在线翻译"

    def translate(self, text: str, messages: Optional[Sequence[Dict[str, str]]] = None,
                  timeout: float = 20.0) -> TranslationResult:
        started = time.time()
        try:
            response = requests.get(
                self.URL,
                params={"q": text, "langpair": "en|zh-CN"},
                headers={"User-Agent": "DDO-Translator/3.0"},
                timeout=timeout,
            )
            elapsed = time.time() - started
            if response.status_code != 200:
                return TranslationResult(text, False, self.name,
                                         "HTTP %d" % response.status_code, elapsed)
            data = response.json()
            translated = ((data.get("responseData") or {}).get("translatedText") or "").strip()
            if not translated:
                return TranslationResult(text, False, self.name, "免费接口没有返回内容", elapsed)
            upper = translated.upper()
            if "MYMEMORY WARNING" in upper or "QUERY LENGTH LIMIT" in upper:
                return TranslationResult(text, False, self.name,
                                         "免费额度已用完（明天恢复）", elapsed)
            return TranslationResult(translated, True, self.name, elapsed=elapsed)
        except requests.Timeout:
            return TranslationResult(text, False, self.name, "请求超时",
                                     time.time() - started)
        except Exception as exc:
            return TranslationResult(text, False, self.name, "请求失败：%s" % exc,
                                     time.time() - started)


def create_engine(config: dict) -> tuple:
    """按配置创建引擎，返回 (引擎, 提示信息)。提示信息用于界面告知当前状态。"""
    name = (config.get("engine") or "deepseek").lower()

    if name == "deepseek":
        engine = DeepSeekEngine(config.get("deepseek_key", ""),
                                config.get("deepseek_model", "deepseek-chat"))
        if engine.available():
            return engine, ""
        return OfflineEngine(), "没有填 DeepSeek API Key，先用离线术语表顶着"

    if name == "mymemory":
        return MyMemoryEngine(), ""

    if name == "offline":
        return OfflineEngine(), ""

    return OfflineEngine(), "未知引擎 %s，已回退到离线术语表" % name
