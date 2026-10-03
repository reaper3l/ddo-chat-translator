"""最小的贡献收件端（本地测试 / 自己的小服务器都能用）。

用法：
    python tools\\contribute_server.py --port 8765 --token <一串随机串>
    # 程序里 data/config.json 的 contribute_url 填 http://127.0.0.1:8765/
    # 取数据：curl.exe -s "http://127.0.0.1:8765/?token=<token>" > 贡献.json

它只做三件事：接 POST、原样存进 JSONL、带 token 的 GET 导出全部。
真正的过滤/闸门在 `tools\\collect_contributions.py` 里做 —— 收到的东西一律不可信。

放在公网服务器上时：请自己加 HTTPS（反向代理）、限制访问频率，并换掉默认端口。
"""
from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAX_BODY = 200 * 1024
_lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    server_version = "DDOContrib/1"
    store: Path = Path("contributions.jsonl")
    token: str = ""

    def _send(self, status: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:                       # noqa: N802 (http.server 约定)
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            self._send(413, {"ok": False, "error": "too large"})
            return
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send(400, {"ok": False, "error": "bad json"})
            return
        if not isinstance(data, dict) or not isinstance(data.get("uid"), str):
            self._send(400, {"ok": False, "error": "missing uid"})
            return
        with _lock:
            self.store.parent.mkdir(parents=True, exist_ok=True)
            with open(self.store, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(data, ensure_ascii=False) + "\n")
        self._send(200, {"ok": True})

    def do_GET(self) -> None:                        # noqa: N802
        from urllib.parse import parse_qs, urlsplit

        token = (parse_qs(urlsplit(self.path).query).get("token") or [""])[0]
        if not self.token or token != self.token:
            self._send(403, {"ok": False, "error": "forbidden"})
            return
        rows = []
        try:
            for line in self.store.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        except FileNotFoundError:
            pass
        except Exception as exc:                     # noqa: BLE001
            self._send(500, {"ok": False, "error": str(exc)})
            return
        self._send(200, rows)

    def log_message(self, *_args) -> None:
        """默认的访问日志太吵，这里静音。"""


def main() -> int:
    parser = argparse.ArgumentParser(description="DDO 翻译助手 · 贡献收件端")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--token", required=True, help="导出数据用的口令（自己编一串）")
    parser.add_argument("--store", default="contributions.jsonl")
    args = parser.parse_args()

    Handler.store = Path(args.store)
    Handler.token = args.token
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print("收件端已启动：http://%s:%d/" % (args.host, args.port))
    print("数据写到：%s" % Handler.store)
    print("导出：curl.exe -s \"http://%s:%d/?token=%s\" > 贡献.json"
          % (args.host, args.port, args.token))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
