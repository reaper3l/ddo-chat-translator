"""本地翻译平台 HTTP 服务（只用标准库 http.server）。

**只监听 127.0.0.1** —— 同一台机器上的插件 / 脚本能连，别的机器连不上。
（为什么不监听 0.0.0.0：那等于把"你的 API Key 额度"开放给整个局域网。）

端点清单（完整参数见 `docs/平台接口.md`）：

    GET  /                      浏览器里打开能看到的说明页（方便自查）
    GET  /v1/health             存活 + 版本 + 引擎状态（**不需要令牌**）
    GET  /v1/capabilities       能力声明（**不需要令牌**）
    GET  /v1/stats              用量统计（需要令牌）
    GET  /v1/plugins            插件列表（需要令牌）
    GET  /v1/terms?q=           查一个词（terms:read）
    POST /v1/terms              加一个词（terms:write）
    POST /v1/translate          翻一条（translate）
    POST /v1/translate/batch    翻一批（batch，多条合并成一次请求更省）
    POST /v1/feedback           提交"这句更好的译法"（feedback，本机立即学习）

错误一律是 `{"ok": false, "error": "中文说明", "code": "..."}` + 合适的 HTTP 状态码。
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import parse_qs, urlparse

# 一次请求体上限：插件不该把整本书 POST 进来（超长文本请用批量 / 分批）
MAX_BODY_BYTES = 2_000_000


class ApiError(Exception):
    def __init__(self, status: int, message: str, code: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code or _DEFAULT_CODES.get(status, "error")


_DEFAULT_CODES = {400: "bad_request", 401: "unauthorized", 403: "forbidden",
                  404: "not_found", 405: "method_not_allowed", 413: "too_large",
                  429: "quota_exceeded", 500: "internal_error"}


def _http_server(platform):
    class _Server(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

        def __init__(self, address):
            super().__init__(address, _Handler)
            self.platform = platform

    return _Server


class _Handler(BaseHTTPRequestHandler):
    server_version = "DDOPlatform"
    protocol_version = "HTTP/1.1"

    # 默认实现会往 stderr 打日志；打包版没有控制台，打了也白打
    def log_message(self, fmt, *args) -> None:      # noqa: A003
        pass

    # ------------------------------------------------------------ 框架
    @property
    def platform(self):
        return self.server.platform                 # type: ignore[attr-defined]

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:                            # noqa: BLE001
            pass

    def _send_text(self, status: int, text: str, content_type="text/html; charset=utf-8") -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:                            # noqa: BLE001
            pass

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except Exception:
            length = 0
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise ApiError(413, "请求体太大（上限 %d 字节）" % MAX_BODY_BYTES)
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            raise ApiError(400, "请求体不是合法的 JSON（要 UTF-8 编码）")
        if not isinstance(data, dict):
            raise ApiError(400, "请求体必须是一个 JSON 对象")
        return data

    def _query(self) -> dict:
        parsed = urlparse(self.path)
        return {key: values[0] for key, values in parse_qs(parsed.query).items()}

    def _route(self) -> str:
        return urlparse(self.path).path.rstrip("/") or "/"

    def _token(self) -> str:
        header = str(self.headers.get("Authorization") or "").strip()
        if header.lower().startswith("bearer "):
            return header[7:].strip()
        if header:
            return header
        # 也允许 ?token=... （浏览器里手点 / 调试方便）
        return str(self._query().get("token") or "").strip()

    def _auth(self, scope: str = "") -> str:
        """校验令牌 + 权限，返回插件 id。"""
        tokens = self.platform.tokens
        pid = tokens.owner_of(self._token())
        if not pid:
            raise ApiError(401, "令牌无效或没带令牌。"
                                "在「设置 → 平台 / 插件」里复制该插件的令牌。")
        if not tokens.is_enabled(pid):
            raise ApiError(403, "插件 %s 已被停用（设置 → 平台 / 插件）" % pid)
        if scope and not tokens.has(pid, scope):
            from .auth import SCOPE_LABELS
            raise ApiError(403, "插件 %s 没有「%s」权限，请在设置里勾上" %
                                (pid, SCOPE_LABELS.get(scope, scope)))
        return pid

    def do_GET(self) -> None:                        # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:                       # noqa: N802
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        try:
            route = self._route()
            handler = _ROUTES.get((method, route))
            if handler is None:
                if route in ("/", ""):
                    self._send_text(200, _INDEX_HTML % {"url": self.platform.url()})
                    return
                raise ApiError(404, "没有这个端点：%s %s" % (method, route))
            handler(self)
        except ApiError as exc:
            self._send_json(exc.status, {"ok": False, "error": exc.message,
                                         "code": exc.code})
        except BrokenPipeError:
            pass
        except Exception as exc:                     # noqa: BLE001
            try:
                self._send_json(500, {"ok": False, "error": "服务内部错误：%s" % exc,
                                      "code": "internal_error"})
            except Exception:
                pass


# ---------------------------------------------------------------- 端点实现
def _health(handler: _Handler) -> None:
    platform = handler.platform
    service = platform.service
    try:
        engine_ok = bool(service.engine.available())
    except Exception:                                # noqa: BLE001
        engine_ok = False
    handler._send_json(200, {
        "ok": True,
        "service": "ddo-translator-platform",
        "version": platform.version,
        "host": platform.host,
        "port": platform.port,
        "uptime_seconds": int(platform.uptime()),
        "engine": service.engine.describe(),
        "engine_available": engine_ok,
        "plugins": len(platform.plugins.scan(use_cache=True)),
    })


def _capabilities(handler: _Handler) -> None:
    handler._send_json(200, handler.platform.service.status())


def _stats(handler: _Handler) -> None:
    pid = handler._auth("stats")
    platform = handler.platform
    own = platform.tokens.snapshot([pid]).get(pid, {})
    handler._send_json(200, {"ok": True, "plugin": pid,
                             "today": own.get("today", 0),
                             "daily_quota": platform.tokens.daily_quota,
                             "remaining": platform.tokens.remaining(pid),
                             "usage": own, "service": platform.service.stats})


def _list_plugins(handler: _Handler) -> None:
    handler._auth("stats")
    plugins = handler.platform.plugins.scan(use_cache=False)
    handler._send_json(200, {"ok": True, "plugins": [
        {"id": p.id, "name": p.name, "version": p.version,
         "description": p.description, "enabled": False if p.error else
         handler.platform.plugins.is_enabled(p.id),
         "error": p.error,
         "actions": p.actions, "scopes": handler.platform.tokens.scopes(p.id)}
        for p in plugins]})


def _lookup_term(handler: _Handler) -> None:
    handler._auth("terms:read")
    term = str(handler._query().get("q") or "").strip()
    if not term:
        raise ApiError(400, "缺少参数 q（要查的词）")
    handler._send_json(200, handler.platform.service.lookup(term))


def _add_term(handler: _Handler) -> None:
    pid = handler._auth("terms:write")
    body = handler._read_json()
    result = handler.platform.service.add_term(body.get("term"), body.get("zh"),
                                               source="plugin:%s" % pid)
    handler._send_json(200 if result.get("ok") else 400, result)


def _translate(handler: _Handler) -> None:
    pid = handler._auth("translate")
    platform = handler.platform
    _check_quota(handler, pid)
    body = handler._read_json()
    max_chars = platform.max_chars
    result = platform.service.translate(
        body.get("text"), str(body.get("direction") or "auto"),
        context=_as_list(body.get("context")), speaker=str(body.get("speaker") or ""),
        channel=str(body.get("channel") or ""), max_chars=max_chars)
    platform.tokens.record(pid, api_calls=int(result.get("api_calls") or 0),
                           error="" if result.get("ok") else str(result.get("error") or ""))
    handler._send_json(200 if result.get("ok") else 400, result)


def _translate_batch(handler: _Handler) -> None:
    pid = handler._auth("batch")
    platform = handler.platform
    _check_quota(handler, pid)
    body = handler._read_json()
    # 配额按**真的调了几次接口**算，不是按"几条文本"算：
    # 一批 3 条合并成 1 次请求，就只该扣 1 次（每条自带的 api_calls 是给界面看的）。
    before = int(platform.service.stats.get("api_calls", 0))
    result = platform.service.translate_many(
        _as_list(body.get("texts")), str(body.get("direction") or "auto"),
        context=_as_list(body.get("context")), max_chars=platform.max_chars)
    used = max(0, int(platform.service.stats.get("api_calls", 0)) - before)
    errors = [str(item.get("error")) for item in (result.get("results") or [])
              if not item.get("ok")]
    platform.tokens.record(pid, api_calls=used, error="; ".join(errors[:3]))
    handler._send_json(200 if result.get("ok") else 400, result)


def _feedback(handler: _Handler) -> None:
    pid = handler._auth("feedback")
    body = handler._read_json()
    result = handler.platform.service.feedback(
        body.get("source"), body.get("before"), body.get("after"),
        remember=bool(body.get("remember", True)))
    if not result.get("ok"):
        handler.platform.tokens.record(pid, error=str(result.get("error") or "纠错没生效"))
    else:
        handler.platform.tokens.record(pid)
    handler._send_json(200 if result.get("ok") else 400, result)


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return []


def _check_quota(handler: _Handler, plugin_id: str) -> None:
    tokens = handler.platform.tokens
    if not tokens.allow(plugin_id):
        raise ApiError(429, "插件 %s 今天的翻译配额已经用完（%d 次/天）。"
                            "可以在「设置 → 平台 / 插件」里调大 quota，"
                            "或等明天重置。" % (plugin_id, tokens.daily_quota),
                       code="quota_exceeded")


_ROUTES = {
    ("GET", "/v1/health"): _health,
    ("GET", "/v1/capabilities"): _capabilities,
    ("GET", "/v1/stats"): _stats,
    ("GET", "/v1/plugins"): _list_plugins,
    ("GET", "/v1/terms"): _lookup_term,
    ("POST", "/v1/terms"): _add_term,
    ("POST", "/v1/translate"): _translate,
    ("POST", "/v1/translate/batch"): _translate_batch,
    ("POST", "/v1/feedback"): _feedback,
}


_INDEX_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>DDO 翻译助手 · 本地平台</title>
<style>body{font-family:"Microsoft YaHei",sans-serif;background:#12141c;color:#e6e6e6;
padding:28px;line-height:1.7}code{background:#1e2130;padding:2px 6px;border-radius:4px}
h1{font-size:20px}li{margin:4px 0}</style></head><body>
<h1>DDO 翻译助手 · 本地平台</h1>
<p>服务正在 <code>%(url)s</code> 上运行。只有本机能连（127.0.0.1）。</p>
<ul>
<li><code>GET /v1/health</code> 存活、版本、引擎状态（不需要令牌）</li>
<li><code>GET /v1/capabilities</code> 能力声明（不需要令牌）</li>
<li><code>POST /v1/translate</code> 翻一条</li>
<li><code>POST /v1/translate/batch</code> 翻一批</li>
<li><code>POST /v1/feedback</code> 提交更好的译法</li>
<li><code>GET|POST /v1/terms</code> 查 / 加词</li>
</ul>
<p>令牌在「设置 → 平台 / 插件」里复制。写法：
<code>Authorization: Bearer &lt;令牌&gt;</code></p>
</body></html>"""
