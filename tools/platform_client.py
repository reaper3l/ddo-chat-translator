"""本地翻译平台的示例客户端（也是"怎么写插件"的参考）。

用法：

    python tools\\platform_client.py health
    python tools\\platform_client.py capabilities
    python tools\\platform_client.py translate "omw, need heals"
    python tools\\platform_client.py translate "马上到" --direction zh2en
    python tools\\platform_client.py batch "omw" "ty" "need heals"
    python tools\\platform_client.py terms --q shroud
    python tools\\platform_client.py terms --add FoD "冲突基地"
    python tools\\platform_client.py feedback "pop side" "流行音乐那边" "位面监狱那边"
    python tools\\platform_client.py plugins
    python tools\\platform_client.py stats

令牌不用手填：默认去 `data/platform.json` 里挑第一个**已启用**的插件令牌。
也可以 `--token <令牌>` 直接给，或者 `--plugin <插件id>` 指定用哪个。

完整的接口说明见 `docs/平台接口.md`。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def find_token(plugin_id: str = "") -> str:
    """从 data/platform.json 里挑一个已启用插件的令牌（找不到就返回空）。"""
    try:
        from app import paths
    except Exception:                             # noqa: BLE001
        paths = None  # type: ignore[assignment]
    candidates = []
    if paths is not None:
        candidates.append(Path(paths.DATA_DIR) / "platform.json")
    candidates.append(ROOT / "data" / "platform.json")
    for path in candidates:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:                         # noqa: BLE001
            continue
        plugins = data.get("plugins") if isinstance(data, dict) else None
        if not isinstance(plugins, dict):
            continue
        for pid, entry in plugins.items():
            if not isinstance(entry, dict):
                continue
            if plugin_id and pid != plugin_id:
                continue
            if str(entry.get("enabled")) in ("True", "true") or entry.get("enabled") is True:
                token = str(entry.get("token") or "")
                if token:
                    return token
        if plugin_id:
            entry = plugins.get(plugin_id) or {}
            if isinstance(entry, dict) and entry.get("token"):
                return str(entry["token"])
    return ""


class Client:
    def __init__(self, base: str, token: str = "") -> None:
        self.base = base.rstrip("/")
        self.token = token

    def _request(self, method: str, path: str, body=None, params=None):
        url = self.base + path
        if params:
            from urllib.parse import urlencode
            url += "?" + urlencode(params)
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                return exc.code, json.loads(raw)
            except Exception:                     # noqa: BLE001
                return exc.code, {"ok": False, "error": raw}
        except urllib.error.URLError as exc:
            return 0, {"ok": False,
                       "error": "连不上 %s（平台没启动？端口对不对？）：%s"
                                % (self.base, exc.reason)}

    def get(self, path, **params):
        return self._request("GET", path, params=params or None)

    def post(self, path, body):
        return self._request("POST", path, body=body)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="DDO 翻译助手 · 本地平台客户端",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    parser.add_argument("--url", default="", help="平台地址（默认 http://127.0.0.1:8765）")
    parser.add_argument("--port", type=int, default=0, help="只给端口（等价于 --url http://127.0.0.1:端口）")
    parser.add_argument("--token", default="", help="插件令牌（默认自动从 data/platform.json 找）")
    parser.add_argument("--plugin", default="", help="用哪个插件的令牌")
    parser.add_argument("--json", action="store_true", help="输出原始 JSON")
    parser.add_argument("command", help="health / capabilities / translate / batch / "
                                        "terms / feedback / plugins / stats")
    parser.add_argument("args", nargs="*", help="命令参数")
    parser.add_argument("--q", default="", help="terms：要查的词")
    parser.add_argument("--add", action="store_true", help="terms：加词（args = 词 译文）")
    parser.add_argument("--direction", default="auto", help="translate：auto/en2zh/zh2en")
    options = parser.parse_args(argv)

    base = options.url
    if not base:
        base = "http://127.0.0.1:%d" % (options.port or 8765)
    client = Client(base, options.token or find_token(options.plugin))
    if not client.token and options.command not in ("health", "capabilities"):
        print("没有可用的令牌：先到「设置 → 平台 / 插件」里启用一个插件，"
              "或者用 --token 直接指定。", file=sys.stderr)

    command = options.command.lower()
    if command == "health":
        status, data = client.get("/v1/health")
    elif command == "capabilities":
        status, data = client.get("/v1/capabilities")
    elif command == "plugins":
        status, data = client.get("/v1/plugins")
    elif command == "stats":
        status, data = client.get("/v1/stats")
    elif command == "translate":
        if not options.args:
            print("要翻什么？例如：platform_client.py translate \"omw\"", file=sys.stderr)
            return 2
        status, data = client.post("/v1/translate",
                                   {"text": " ".join(options.args),
                                    "direction": options.direction})
    elif command == "batch":
        if not options.args:
            print("至少要给一条文本", file=sys.stderr)
            return 2
        status, data = client.post("/v1/translate/batch",
                                   {"texts": list(options.args),
                                    "direction": options.direction})
    elif command == "terms":
        if options.add:
            if len(options.args) < 2:
                print("用法：terms --add 词 译文", file=sys.stderr)
                return 2
            status, data = client.post("/v1/terms",
                                       {"term": options.args[0], "zh": options.args[1]})
        else:
            term = options.q or (" ".join(options.args) if options.args else "")
            if not term:
                print("用法：terms --q 词", file=sys.stderr)
                return 2
            status, data = client.get("/v1/terms", q=term)
    elif command == "feedback":
        if len(options.args) < 3:
            print("用法：feedback 原文 之前的译文 更好的译文", file=sys.stderr)
            return 2
        status, data = client.post("/v1/feedback",
                                   {"source": options.args[0], "before": options.args[1],
                                    "after": options.args[2]})
    else:
        print("不认识的命令：%s" % options.command, file=sys.stderr)
        return 2

    if options.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(_pretty(command, data))
    return 0 if data.get("ok") or status == 200 else 1


def _pretty(command: str, data: dict) -> str:
    if not data.get("ok"):
        return "失败：%s" % data.get("error")
    if command == "health":
        return ("平台 %s　版本 %s　引擎 %s（%s）　插件 %d 个　已运行 %d 秒"
                % (data.get("port"), data.get("version"), data.get("engine"),
                   "可用" if data.get("engine_available") else "不可用",
                   data.get("plugins", 0), data.get("uptime_seconds", 0)))
    if command == "translate":
        note = "　[%s]" % data["note"] if data.get("note") else ""
        money = "（没花钱）" if not data.get("api_calls") else "（调用 1 次）"
        return "%s%s %s" % (data.get("zh", ""), note, money)
    if command == "batch":
        lines = []
        for index, item in enumerate(data.get("results") or [], 1):
            lines.append("%d. %s" % (index, item.get("zh", "")))
        return "\n".join(lines)
    if command == "terms":
        return "%s = %s%s" % (data.get("term"), data.get("zh") or "(没有)",
                              "（自己的词）" if data.get("mine") else "")
    if command == "feedback":
        return "%s" % data.get("applies", "已记录")
    if command == "plugins":
        lines = []
        for item in data.get("plugins") or []:
            state = "启用" if item.get("enabled") else "停用"
            err = "　⚠%s" % item["error"] if item.get("error") else ""
            lines.append("%-16s %-4s 权限 %s%s" % (item.get("name"), state,
                                                 ",".join(item.get("scopes") or []), err))
        return "\n".join(lines) or "（没有插件）"
    if command == "stats":
        return ("今天 %s/%s 次　累计调用 %s　本地命中 %s（省下的钱）"
                % (data.get("today"), data.get("daily_quota"),
                   (data.get("service") or {}).get("api_calls"),
                   sum((data.get("service") or {}).get(k, 0)
                       for k in ("cache_hits", "memory_hits", "dict_hits"))))
    return json.dumps(data, ensure_ascii=False)


if __name__ == "__main__":
    raise SystemExit(main())
