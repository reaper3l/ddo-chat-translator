"""本地翻译平台：把翻译能力开放给插件 / 脚本 / 别的程序。

一句话：**界面能做的翻译，命令行和插件也能做，而且用的是同一套术语表、
记忆库和缓存** —— 所以不会出现"界面翻过一遍，插件再翻一遍还得花钱"。

* 对外：`app/platform/server.py` 起一个只监听 127.0.0.1 的 HTTP 服务，
  端点见 `docs/平台接口.md`。
* 对内：`app/platform/plugins.py` 按 `plugins/<id>/manifest.json` 拉起插件进程，
  传 JSON 进去、收 JSON 出来，带超时强杀。
* 权限：`app/platform/auth.py`，每个插件一个令牌 + scopes + 每日配额。

平台默认**关**：用户要在「设置 → 平台 / 插件」里明确打开才会监听端口。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Optional

from .. import __version__, config as config_module
from ..service import TranslatorService
from .auth import ALL_SCOPES, DEFAULT_SCOPES, SCOPE_LABELS, TokenStore
from .plugins import (PluginError, PluginRegistry, ensure_plugins_dir,
                     plugins_dir)
from .server import _http_server

__all__ = ["Platform", "TokenStore", "PluginRegistry", "SCOPE_LABELS",
           "DEFAULT_SCOPES", "ALL_SCOPES", "plugins_dir", "PluginError",
           "ensure_plugins_dir"]

# 端口被占用时往后再试这么多个（8765 → 8766 → …）
_PORT_TRIES = 12


class Platform:
    """把 service / tokens / plugins / HTTP 服务 串在一起的那一层。"""

    def __init__(self, config: Optional[dict] = None, service: Optional[TranslatorService] = None,
                 tokens: Optional[TokenStore] = None, registry: Optional[PluginRegistry] = None,
                 token_path=None, plugins_root=None) -> None:
        self.config = dict(config) if config is not None else config_module.load_config()
        self.service = service if service is not None else TranslatorService(self.config)
        self.tokens = tokens if tokens is not None else TokenStore(
            path=token_path, daily_quota=int(self.config.get("platform_daily_quota", 500) or 0))
        self.plugins = registry if registry is not None else PluginRegistry(
            self.tokens, root=Path(plugins_root) if plugins_root is not None else None)
        self.version = __version__
        self.host = "127.0.0.1"
        self.port = int(self.config.get("platform_port", 8765) or 8765)
        self.max_chars = int(self.config.get("platform_max_chars", 4000) or 4000)
        self.last_error = ""
        self._httpd = None
        self._thread = None
        self._lock = threading.RLock()
        self._started_at = 0.0

    # ------------------------------------------------------------ 状态
    @property
    def running(self) -> bool:
        with self._lock:
            return self._httpd is not None

    def uptime(self) -> float:
        return (time.time() - self._started_at) if self.running else 0.0

    def url(self) -> str:
        return "http://%s:%d" % (self.host, self.port)

    def status(self) -> dict:
        plugins = self.plugins.scan(use_cache=True)
        return {
            "ok": True,
            "running": self.running,
            "url": self.url(),
            "port": self.port,
            "last_error": self.last_error,
            "plugins": len(plugins),
            "plugins_enabled": sum(1 for p in plugins if self.plugins.is_enabled(p.id)),
            "engine": self.service.engine.describe(),
            "engine_available": bool(self.service.engine.available()),
            "daily_quota": self.tokens.daily_quota,
            "plugin_dir": str(self.plugins.root),
        }

    # ------------------------------------------------------------ 开关
    def start(self, port: Optional[int] = None) -> dict:
        with self._lock:
            if self._httpd is not None:
                return {"ok": True, "url": self.url(), "port": self.port,
                        "note": "已经在跑了"}
            wanted = int(port or self.port or 8765)
            server_cls = _http_server(self)
            error = ""
            for candidate in [wanted] + [wanted + n for n in range(1, _PORT_TRIES)]:
                if not (1 <= candidate <= 65535):
                    continue
                try:
                    httpd = server_cls((self.host, candidate))
                except OSError as exc:
                    error = str(exc)
                    continue
                except Exception as exc:                 # noqa: BLE001
                    error = str(exc)
                    break
                self._httpd = httpd
                self.port = candidate
                self._thread = threading.Thread(
                    target=httpd.serve_forever, kwargs={"poll_interval": 0.2},
                    daemon=True, name="ddo-platform")
                self._thread.start()
                self._started_at = time.time()
                self.last_error = ""
                note = "" if candidate == wanted else ("端口 %d 被占用，改用 %d"
                                                       % (wanted, candidate))
                return {"ok": True, "url": self.url(), "port": candidate, "note": note}
            self.last_error = error or "端口被占用"
            return {"ok": False, "error": "本地平台起不来：%s" % self.last_error}

    def stop(self) -> dict:
        with self._lock:
            httpd, self._httpd = self._httpd, None
            thread, self._thread = self._thread, None
            self._started_at = 0.0
        if httpd is not None:
            try:
                httpd.shutdown()
                httpd.server_close()
            except Exception:                            # noqa: BLE001
                pass
        if thread is not None:
            try:
                thread.join(timeout=3)
            except Exception:                            # noqa: BLE001
                pass
        return {"ok": True, "running": False}

    def apply_config(self, config: Optional[dict] = None) -> dict:
        """界面点「保存」后调用：按 `platform_enabled` 把服务开到 / 关到该有的状态。"""
        if config is not None:
            self.config = dict(config)
        self.port = int(self.config.get("platform_port", 8765) or 8765)
        self.tokens.daily_quota = max(0, int(self.config.get("platform_daily_quota", 500) or 0))
        self.max_chars = int(self.config.get("platform_max_chars", 4000) or 4000)
        wanted = self.port
        if self.config.get("platform_enabled"):
            if self.running and self.port == wanted:
                return {"ok": True, "running": True, "url": self.url()}
            if self.running:
                self.stop()
            result = self.start(wanted)
            # 统一返回形状：调用方永远可以问 result["running"]
            result["running"] = bool(result.get("ok") and self.running)
            return result
        if self.running:
            self.stop()
        return {"ok": True, "running": False}

    def attach(self, config: dict, service=None) -> None:
        """界面改了配置 / 重建了术语表之后同步过来（和服务共用同一份资产）。"""
        self.config = dict(config)
        self.port = int(self.config.get("platform_port", 8765) or 8765)
        self.tokens.daily_quota = max(0, int(self.config.get("platform_daily_quota", 500) or 0))
        self.max_chars = int(self.config.get("platform_max_chars", 4000) or 4000)
        if service is not None:
            self.service = service
