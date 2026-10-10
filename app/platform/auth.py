"""插件令牌与权限（scopes）。

每个插件一个令牌；令牌存在 `data/platform.json` 里，界面上可以查看 / 复制 / 重置。
scopes 决定它能调哪些端点：

    translate     翻译（英→中 / 中→英，这是唯一要花钱的路）
    batch         一次翻多条（能合并成一次请求，更省）
    terms:read    查本机术语表 / 词典
    terms:write   往本机词典里加词（**默认不给**，要用户在设置里单独勾）
    feedback      提交"这句更好的译法"（本机学习，立即生效）
    stats         看用量统计

设计原则：

* 令牌只是**本机权限**，不是网络凭证 —— 服务只在 127.0.0.1 上听，
  别的机器连不上。所以宁可把它记在明文 JSON 里，也别引入"密钥托管"那套复杂度。
* 插件**拿不到** DeepSeek Key：它只能通过平台转发，花费记在平台账上。
* 每天有配额（`platform_daily_quota`），防止插件写错循环把 API 费用跑爆。
  配额只统计"真的调了接口"的次数，本地命中 / 缓存不算。
"""
from __future__ import annotations

import secrets
import threading
import time
from typing import Dict, List, Optional

from .. import paths

# 一个插件默认只拿到"只读翻译"这两项 —— 写词典、学习必须用户单独授权
DEFAULT_SCOPES = ("translate", "batch")
ALL_SCOPES = ("translate", "batch", "terms:read", "terms:write", "feedback", "stats")

# 界面上给人看的中文说明
SCOPE_LABELS = {
    "translate": "翻译（英→中 / 中→英）",
    "batch": "批量翻译（多条合并成一次请求，更省）",
    "terms:read": "读取术语表 / 词典",
    "terms:write": "往本机词典加词（会改动你的词典）",
    "feedback": "提交纠错（本机学习，立即生效）",
    "stats": "查看用量统计",
}


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


class TokenStore:
    """`data/platform.json` 的读写 + 配额记账。多线程安全（HTTP 服务是多线程的）。"""

    def __init__(self, path=None, daily_quota: int = 500) -> None:
        self.path = path if path is not None else (paths.DATA_DIR / "platform.json")
        self.daily_quota = max(0, int(daily_quota or 0))
        self._lock = threading.RLock()
        self._data: Dict[str, dict] = {"version": 1, "plugins": {}}
        self.load()

    # ------------------------------------------------------------ 读写
    def load(self) -> None:
        raw = paths.read_json(self.path, {})
        plugins = {}
        if isinstance(raw, dict) and isinstance(raw.get("plugins"), dict):
            for pid, entry in raw["plugins"].items():
                if isinstance(entry, dict):
                    plugins[str(pid)] = self._normalize(str(pid), entry)
        self._data = {"version": 1, "plugins": plugins}

    def save(self) -> bool:
        with self._lock:
            return paths.write_json(self.path, self._data)

    @staticmethod
    def _normalize(pid: str, entry: dict) -> dict:
        scopes = [s for s in (entry.get("scopes") or DEFAULT_SCOPES)
                  if s in ALL_SCOPES]
        return {
            "token": str(entry.get("token") or "") or secrets.token_hex(16),
            "scopes": scopes or list(DEFAULT_SCOPES),
            "enabled": bool(entry.get("enabled", False)),
            "created_at": entry.get("created_at") or time.time(),
            "calls": int(entry.get("calls") or 0),
            "api_calls": int(entry.get("api_calls") or 0),
            "denied": int(entry.get("denied") or 0),
            "last_used": float(entry.get("last_used") or 0),
            "last_error": str(entry.get("last_error") or ""),
            "note": str(entry.get("note") or ""),
            "usage": {k: int(v) for k, v in (entry.get("usage") or {}).items()
                      if isinstance(v, (int, float)) or str(v).isdigit()},
        }

    # ------------------------------------------------------------ 条目
    def _entry(self, plugin_id: str, create: bool = True) -> Optional[dict]:
        pid = str(plugin_id or "").strip()
        if not pid:
            return None
        with self._lock:
            entry = self._data["plugins"].get(pid)
            if entry is None and create:
                entry = self._normalize(pid, {})
                self._data["plugins"][pid] = entry
            return entry

    def token_for(self, plugin_id: str) -> str:
        entry = self._entry(plugin_id)
        return str(entry["token"]) if entry else ""

    def reset_token(self, plugin_id: str) -> str:
        """换一把新令牌（旧的立刻作废）。用户怀疑插件乱来时就点它。"""
        entry = self._entry(plugin_id)
        if entry is None:
            return ""
        entry["token"] = secrets.token_hex(16)
        self.save()
        return entry["token"]

    def scopes(self, plugin_id: str) -> List[str]:
        entry = self._entry(plugin_id, create=False)
        return list(entry["scopes"]) if entry else []

    def set_scopes(self, plugin_id: str, scopes) -> List[str]:
        entry = self._entry(plugin_id)
        if entry is None:
            return []
        entry["scopes"] = [s for s in scopes if s in ALL_SCOPES]
        self.save()
        return list(entry["scopes"])

    def has(self, plugin_id: str, scope: str) -> bool:
        entry = self._entry(plugin_id, create=False)
        return bool(entry and scope in entry["scopes"])

    def is_enabled(self, plugin_id: str) -> bool:
        entry = self._entry(plugin_id, create=False)
        return bool(entry and entry["enabled"])

    def set_enabled(self, plugin_id: str, enabled: bool) -> bool:
        entry = self._entry(plugin_id)
        if entry is None:
            return False
        entry["enabled"] = bool(enabled)
        self.save()
        return entry["enabled"]

    def forget(self, plugin_id: str) -> bool:
        with self._lock:
            removed = self._data["plugins"].pop(str(plugin_id or ""), None) is not None
        if removed:
            self.save()
        return removed

    def owner_of(self, token: str) -> str:
        """反查令牌是哪个插件的（HTTP 请求头上带的就是它）。"""
        token = str(token or "")
        if not token:
            return ""
        with self._lock:
            for pid, entry in self._data["plugins"].items():
                if secrets.compare_digest(str(entry.get("token") or ""), token):
                    return pid
        return ""

    # ------------------------------------------------------------ 配额 / 记账
    def today_usage(self, plugin_id: str) -> int:
        entry = self._entry(plugin_id, create=False)
        if entry is None:
            return 0
        return int(entry["usage"].get(_today(), 0))

    def remaining(self, plugin_id: str) -> int:
        if self.daily_quota <= 0:
            return -1                       # -1 = 不限
        return max(0, self.daily_quota - self.today_usage(plugin_id))

    def allow(self, plugin_id: str) -> bool:
        return self.remaining(plugin_id) != 0

    def record(self, plugin_id: str, api_calls: int = 0, denied: bool = False,
               error: str = "") -> None:
        entry = self._entry(plugin_id)
        if entry is None:
            return
        with self._lock:
            entry["calls"] = int(entry.get("calls", 0)) + 1
            entry["last_used"] = time.time()
            if api_calls:
                entry["api_calls"] = int(entry.get("api_calls", 0)) + int(api_calls)
                today = _today()
                entry["usage"][today] = int(entry["usage"].get(today, 0)) + int(api_calls)
                # 只留最近 14 天，别让这个文件无限长
                for key in sorted(entry["usage"])[:-14]:
                    entry["usage"].pop(key, None)
            if denied:
                entry["denied"] = int(entry.get("denied", 0)) + 1
            if error:
                entry["last_error"] = str(error)[:300]

    # ------------------------------------------------------------ 界面用
    def snapshot(self, plugin_ids=None) -> Dict[str, dict]:
        """给设置界面 / /v1/stats 用的一份只读快照（含令牌，界面要显示/复制）。"""
        with self._lock:
            items = {}
            for pid, entry in self._data["plugins"].items():
                if plugin_ids is not None and pid not in plugin_ids:
                    continue
                items[pid] = {
                    "token": entry["token"],
                    "scopes": list(entry["scopes"]),
                    "enabled": entry["enabled"],
                    "calls": entry["calls"],
                    "api_calls": entry["api_calls"],
                    "denied": entry["denied"],
                    "last_used": entry["last_used"],
                    "last_error": entry["last_error"],
                    "today": int(entry["usage"].get(_today(), 0)),
                    "usage": dict(entry["usage"]),
                }
            return items
