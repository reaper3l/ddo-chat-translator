"""本地翻译平台 + 插件（app/platform/）的测试。

不打真实接口、不碰用户数据：引擎用 EchoEngine，插件用临时目录里的假插件。
HTTP 部分是真的起一个本地端口，用 urllib 打过去（比 mock 更能抓到路由/鉴权的错）。
"""
import contextlib
import io
import json
import socket
import sys
import tempfile
import textwrap
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from app.config import DEFAULT_CONFIG
from app.engines import BaseEngine, TranslationResult
from app.glossary import Glossary
from app.service import TranslatorService
from app.store import MemoryStore
from app.platform import Platform
from app.platform.auth import TokenStore
from app.platform.plugins import PluginRegistry


class EchoEngine(BaseEngine):
    name = "echo"
    supports_chat = True

    def __init__(self):
        self.calls = 0

    def available(self):
        return True

    def describe(self):
        return "echo"

    def translate(self, text, messages=None, timeout=20.0):
        import re

        self.calls += 1
        # 批量请求长这样："[1] alpha\n[2] beta" —— 要按 "1. 译文" 回话，
        # 否则会被 parse_batch_reply 当成"缺行"而逐条重试（那样就多花钱了）。
        lines = []
        for raw in (text or "").splitlines():
            match = re.match(r"^\s*\[(\d+)\]\s*(.*)$", raw)
            lines.append("%s. 中:%s" % (match.group(1), match.group(2))
                         if match else raw)
        return TranslationResult("\n".join(lines), True, self.name)


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Rig:
    """一个隔离好的平台：临时目录 + 假引擎。"""

    def __init__(self, daily_quota=500):
        self.folder = Path(tempfile.mkdtemp(prefix="ddo_platform_"))
        config = dict(DEFAULT_CONFIG)
        config["engine"] = "offline"
        config["dict_sources"] = []
        config["public_dict_enabled"] = False
        config["batch_translate"] = True
        config["batch_max"] = 3
        config["platform_port"] = free_port()
        config["platform_max_chars"] = 4000
        self.config = config
        self.engine = EchoEngine()
        self.memory = MemoryStore(self.folder / "memory.json")
        self.service = TranslatorService(
            config, memory=self.memory, engine=self.engine,
            glossary=Glossary({"shroud": "幽影堡"}),
            cache_path=self.folder / "cache.json")
        self.tokens = TokenStore(path=self.folder / "platform.json",
                                 daily_quota=daily_quota)
        self.registry = PluginRegistry(self.tokens, root=self.folder / "plugins")
        self.platform = Platform(config, service=self.service, tokens=self.tokens,
                                 registry=self.registry)
        self.started = False

    # ---------------------------------------------------------------
    def start(self):
        result = self.platform.start(self.config["platform_port"])
        assert result.get("ok"), result
        self.started = True
        return self.platform.url()

    def stop(self):
        if self.started:
            self.platform.stop()
            self.started = False

    def plugin(self, plugin_id="demo", scopes=("translate", "batch"), enabled=True):
        if enabled:
            self.tokens._entry(plugin_id)
            self.tokens.set_enabled(plugin_id, True)
        return self.tokens.token_for(plugin_id)

    def add_plugin(self, plugin_id="demo", body=None, actions=None):
        """在插件目录里造一个假插件（.py，只回一段 JSON）。"""
        directory = self.registry.root / plugin_id
        directory.mkdir(parents=True, exist_ok=True)
        script = body or textwrap.dedent(
            """
            import json, sys
            request = json.loads(sys.stdin.read() or "{}")
            print(json.dumps({"ok": True,
                              "message": "收到动作 " + request.get("action", ""),
                              "output": {"base_url": (request.get("platform") or {}).get("base_url", ""),
                                         "token_len": len((request.get("platform") or {}).get("token", ""))}},
                             ensure_ascii=False))
            """)
        (directory / "plugin.py").write_text(script, encoding="utf-8")
        (directory / "manifest.json").write_text(json.dumps({
            "id": plugin_id, "name": "假插件 " + plugin_id, "entry": "plugin.py",
            "actions": actions or [{"id": "run", "name": "跑一下"}]},
            ensure_ascii=False), encoding="utf-8")
        self.registry.refresh()
        return directory

    # ---------------------------------------------------------------
    def request(self, method, path, body=None, token=None, base=None, timeout=15):
        url = (base or self.platform.url()) + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        if body is not None:
            request.add_header("Content-Type", "application/json")
        if token:
            request.add_header("Authorization", "Bearer " + token)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))


def make_rig(daily_quota=500):
    rig = Rig(daily_quota=daily_quota)
    rig.start()
    return rig


# ==================================================================== HTTP
def test_health_and_capabilities_need_no_token():
    rig = make_rig()
    try:
        status, data = rig.request("GET", "/v1/health")
        assert status == 200 and data["ok"] is True
        assert data["host"] == "127.0.0.1"
        status, data = rig.request("GET", "/v1/capabilities")
        assert status == 200 and data["ok"] is True
        assert "directions" in data["capabilities"]
        assert data["capabilities"]["batch"] is True
    finally:
        rig.stop()


def test_only_listens_on_loopback():
    rig = make_rig()
    try:
        assert rig.platform.host == "127.0.0.1"
        # 用一个外部可达的地址去连应该连不上（这里只验证绑定地址本身）
        assert rig.platform.url().startswith("http://127.0.0.1:")
    finally:
        rig.stop()


def test_missing_token_is_401():
    rig = make_rig()
    try:
        status, data = rig.request("POST", "/v1/translate", {"text": "omw"})
        assert status == 401 and data["ok"] is False
    finally:
        rig.stop()


def test_unknown_token_is_401():
    rig = make_rig()
    try:
        status, data = rig.request("POST", "/v1/translate", {"text": "omw"},
                                   token="deadbeef")
        assert status == 401
    finally:
        rig.stop()


def test_disabled_plugin_is_403():
    rig = make_rig()
    try:
        token = rig.plugin("demo", enabled=False)
        status, data = rig.request("POST", "/v1/translate", {"text": "omw"}, token=token)
        assert status == 403 and "停用" in data["error"]
    finally:
        rig.stop()


def test_default_plugin_cannot_write_terms():
    rig = make_rig()
    try:
        token = rig.plugin("demo")
        status, data = rig.request("POST", "/v1/terms",
                                   {"term": "x", "zh": "y"}, token=token)
        assert status == 403 and "权限" in data["error"]
        # 但翻译是允许的
        status, data = rig.request("POST", "/v1/translate", {"text": "omw"}, token=token)
        assert status == 200 and data["ok"] is True
    finally:
        rig.stop()


def test_translate_hits_glossary_and_caches():
    rig = make_rig()
    try:
        token = rig.plugin("demo")
        status, first = rig.request("POST", "/v1/translate",
                                    {"text": "need heals for shroud"}, token=token)
        assert status == 200 and first["ok"] is True
        assert "幽影堡" in first["zh"]
        assert rig.engine.calls == 1
        status, second = rig.request("POST", "/v1/translate",
                                     {"text": "need heals for shroud"}, token=token)
        assert second["ok"] is True
        assert rig.engine.calls == 1, "同样的句子第二次不该再调接口"
        assert second["cached"] is True or second["note"] == "缓存"
        snapshot = rig.tokens.snapshot(["demo"])["demo"]
        assert snapshot["api_calls"] == 1, snapshot
        assert snapshot["calls"] >= 2
    finally:
        rig.stop()


def test_fully_covered_text_never_calls_engine():
    rig = make_rig()
    try:
        token = rig.plugin("demo")
        status, data = rig.request("POST", "/v1/translate", {"text": "shroud"}, token=token)
        assert data["zh"] == "幽影堡"
        assert rig.engine.calls == 0
        assert data["api_calls"] == 0
    finally:
        rig.stop()


def test_batch_counts_one_api_call_for_one_request():
    rig = make_rig()
    try:
        token = rig.plugin("demo")
        status, data = rig.request("POST", "/v1/translate/batch",
                                   {"texts": ["alpha", "beta"]}, token=token)
        assert status == 200 and len(data["results"]) == 2
        assert rig.engine.calls == 1, "两条合并成一次请求"
        assert rig.tokens.snapshot(["demo"])["demo"]["api_calls"] == 1
    finally:
        rig.stop()


def test_terms_and_feedback_flow():
    rig = make_rig()
    try:
        token = rig.plugin("demo")
        rig.tokens.set_scopes("demo", ["translate", "batch", "terms:read",
                                       "terms:write", "feedback", "stats"])
        status, data = rig.request("POST", "/v1/terms",
                                   {"term": "FoD", "zh": "冲突基地"}, token=token)
        assert status == 200 and data["ok"] is True
        status, data = rig.request("GET", "/v1/terms?q=fod", token=token)
        assert data["found"] is True and data["zh"] == "冲突基地"
        status, data = rig.request("POST", "/v1/feedback",
                                   {"source": "pop side", "before": "流行乐那边",
                                    "after": "位面监狱那边"}, token=token)
        assert status == 200 and data["ok"] is True
        status, data = rig.request("POST", "/v1/translate", {"text": "pop side"},
                                   token=token)
        assert data["zh"] == "位面监狱那边" and data["note"] == "记忆命中"
    finally:
        rig.stop()


def test_quota_exceeded_returns_429():
    rig = make_rig(daily_quota=1)
    try:
        token = rig.plugin("demo")
        status, _data = rig.request("POST", "/v1/translate", {"text": "first one"},
                                    token=token)
        assert status == 200
        status, data = rig.request("POST", "/v1/translate", {"text": "second one"},
                                   token=token)
        assert status == 429 and data["code"] == "quota_exceeded"
    finally:
        rig.stop()


def test_stats_and_plugins_endpoints():
    rig = make_rig()
    try:
        rig.add_plugin("demo")
        token = rig.plugin("demo")
        rig.tokens.set_scopes("demo", ["translate", "batch", "stats"])
        status, data = rig.request("GET", "/v1/plugins", token=token)
        assert status == 200
        assert [item["id"] for item in data["plugins"]] == ["demo"]
        assert data["plugins"][0]["enabled"] is True
        status, data = rig.request("GET", "/v1/stats", token=token)
        assert status == 200 and data["daily_quota"] == 500
        assert data["remaining"] == 500
    finally:
        rig.stop()


def test_unknown_route_is_404_and_bad_json_is_400():
    rig = make_rig()
    try:
        status, data = rig.request("GET", "/v1/nope")
        assert status == 404 and data["code"] == "not_found"
        token = rig.plugin("demo")
        url = rig.platform.url() + "/v1/translate"
        request = urllib.request.Request(url, data=b"{not json", method="POST")
        request.add_header("Authorization", "Bearer " + token)
        request.add_header("Content-Type", "application/json")
        try:
            urllib.request.urlopen(request, timeout=10)
            raise AssertionError("应该 400")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
            assert json.loads(exc.read().decode("utf-8"))["code"] == "bad_request"
    finally:
        rig.stop()


def test_root_page_is_html():
    rig = make_rig()
    try:
        with urllib.request.urlopen(rig.platform.url() + "/", timeout=10) as response:
            body = response.read().decode("utf-8")
        assert "本地平台" in body and "<html" in body.lower()
    finally:
        rig.stop()


# ==================================================================== 插件
def test_registry_discovers_and_runs_plugin():
    rig = make_rig()
    try:
        rig.add_plugin("demo")
        plugins = rig.registry.scan(use_cache=False)
        assert [p.id for p in plugins] == ["demo"]
        assert plugins[0].actions[0]["id"] == "run"
        token = rig.plugin("demo")
        result = rig.registry.run("demo", "run", payload={}, timeout=60,
                                  base_url=rig.platform.url())
        assert result["ok"] is True, result
        assert "收到动作 run" in result["message"]
        assert result["output"]["base_url"] == rig.platform.url()
        assert result["output"]["token_len"] == len(token)
    finally:
        rig.stop()


def test_plugin_must_be_enabled_before_running():
    rig = make_rig()
    try:
        rig.add_plugin("demo")
        rig.tokens._entry("demo")
        result = rig.registry.run("demo", "run")
        assert result["ok"] is False and "启用" in result["error"]
    finally:
        rig.stop()


def test_unknown_action_is_rejected():
    rig = make_rig()
    try:
        rig.add_plugin("demo")
        rig.plugin("demo")
        result = rig.registry.run("demo", "no-such-action")
        assert result["ok"] is False and "动作" in result["error"]
    finally:
        rig.stop()


def test_plugin_timeout_is_killed():
    rig = make_rig()
    try:
        rig.add_plugin("slow", body="import time\ntime.sleep(60)\n")
        rig.plugin("slow")
        started = time.time()
        result = rig.registry.run("slow", "run", timeout=1.5)
        elapsed = time.time() - started
        assert result["ok"] is False
        assert "超时" in result["error"]
        assert elapsed < 30, "超时必须真的把进程杀掉"
    finally:
        rig.stop()


def test_plugin_crash_reports_error():
    rig = make_rig()
    try:
        rig.add_plugin("boom", body="import sys\nsys.stderr.write('bad stuff\\n')\nsys.exit(3)\n")
        rig.plugin("boom")
        result = rig.registry.run("boom", "run", timeout=30)
        assert result["ok"] is False
        assert result["error"]
    finally:
        rig.stop()


def test_plugin_output_can_have_log_lines_before_json():
    rig = make_rig()
    try:
        rig.add_plugin("chatty", body=(
            "import json\n"
            "print('正在处理…')\n"
            "print(json.dumps({'ok': True, 'message': '好了'}))\n"
            "print('收工')\n"))
        rig.plugin("chatty")
        result = rig.registry.run("chatty", "run", timeout=30)
        assert result["ok"] is True and result["message"] == "好了"
    finally:
        rig.stop()


def test_manifest_error_is_reported_not_fatal():
    rig = make_rig()
    try:
        directory = rig.registry.root / "broken"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "manifest.json").write_text("{ this is not json", encoding="utf-8")
        plugins = rig.registry.refresh()
        broken = [p for p in plugins if p.id == "broken"]
        assert broken and broken[0].error
        result = rig.registry.set_enabled("broken", True)
        assert result["ok"] is False
    finally:
        rig.stop()


def test_install_from_zip_and_uninstall():
    rig = make_rig()
    try:
        archive_path = rig.folder / "demo.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("demo/manifest.json", json.dumps(
                {"id": "demo", "name": "压缩包插件", "entry": "plugin.py",
                 "actions": [{"id": "run", "name": "跑"}]}, ensure_ascii=False))
            archive.writestr("demo/plugin.py",
                             "import json\nprint(json.dumps({'ok': True, 'message': 'hi'}))\n")
        result = rig.registry.install_zip(archive_path)
        assert result["ok"] is True, result
        assert (rig.registry.root / "demo" / "manifest.json").is_file()
        assert [p.id for p in rig.registry.refresh()] == ["demo"]
        removed = rig.registry.uninstall("demo")
        assert removed["ok"] is True
        assert not (rig.registry.root / "demo").exists()
    finally:
        rig.stop()


def test_zip_slip_is_rejected():
    rig = make_rig()
    try:
        archive_path = rig.folder / "evil.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("../outside.txt", "pwned")
            archive.writestr("manifest.json", "{}")
        result = rig.registry.install_zip(archive_path)
        assert result["ok"] is False and "不安全" in result["error"]
        assert not (rig.folder.parent / "outside.txt").exists()
    finally:
        rig.stop()


def test_plugin_gets_token_and_url_in_env():
    rig = make_rig()
    try:
        rig.add_plugin("env", body=(
            "import json, os\n"
            "print(json.dumps({'ok': True, 'output': {\n"
            "  'url': os.environ.get('DDO_PLATFORM_URL', ''),\n"
            "  'token': os.environ.get('DDO_PLATFORM_TOKEN', ''),\n"
            "  'flag': os.environ.get('DDO_PLUGIN', '')}}))\n"))
        token = rig.plugin("env")
        result = rig.registry.run("env", "run", timeout=30, base_url=rig.platform.url())
        assert result["ok"] is True
        assert result["output"]["url"] == rig.platform.url()
        assert result["output"]["token"] == token
        assert result["output"]["flag"] == "1"
    finally:
        rig.stop()


def test_bundled_example_plugin_runs_end_to_end():
    """真跑一遍随程序带的示例插件（plugins/guide-hanhua），别让它悄悄坏掉。"""
    source_dir = Path(__file__).resolve().parent.parent / "plugins" / "guide-hanhua"
    if not (source_dir / "manifest.json").is_file():
        return                                   # 打包/裁剪过的环境里没有它就跳过
    rig = make_rig()
    try:
        # 把示例插件复制到临时插件目录里（不在仓库里写 output\）
        target = rig.registry.root / "guide-hanhua"
        import shutil
        shutil.copytree(source_dir, target)
        rig.registry.refresh()
        assert [p.id for p in rig.registry.scan(use_cache=False)] == ["guide-hanhua"]
        rig.plugin("guide-hanhua")

        source = rig.folder / "guide.txt"
        source.write_text("The Shroud is a raid.\n\nBring poison resist, please.\n",
                          encoding="utf-8")
        out_dir = rig.folder / "out"
        result = rig.registry.run("guide-hanhua", "translate-file",
                                  payload={"path": str(source),
                                           "output_dir": str(out_dir)},
                                  timeout=120, base_url=rig.platform.url())
        assert result["ok"] is True, result
        assert result["files"], result
        markdown = Path(result["files"][0]).read_text(encoding="utf-8")
        assert "The Shroud is a raid." in markdown      # 原文也在（中英对照）
        assert "中:" in markdown, markdown[:400]         # 译文真的从平台取回来了
        # 术语表是平台的：Shroud 在插件里被换成了中文，说明整条链路是通的
        assert "幽影堡" in markdown, markdown[:400]
    finally:
        rig.stop()


# ==================================================================== 命令行
def test_cli_translate_uses_offline_engine_without_network():
    """`--translate` 要和界面共用同一套资产，而且不联网也能跑通。"""
    import main as main_module
    from app import paths as paths_module

    folder = Path(tempfile.mkdtemp(prefix="ddo_cli_"))
    original_config = paths_module.CONFIG_PATH
    original_cache = paths_module.CACHE_PATH
    original_memory = paths_module.MEMORY_PATH
    paths_module.CONFIG_PATH = folder / "config.json"
    paths_module.CACHE_PATH = folder / "cache.json"
    paths_module.MEMORY_PATH = folder / "memory.json"
    paths_module.CONFIG_PATH.write_text(json.dumps({
        "engine": "offline", "dict_sources": [], "public_dict_enabled": False,
        "use_extra_glossary": False}), encoding="utf-8")
    try:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main_module._run_translate(["--translate", "hello there", "--json"])
        assert code == 0
        payload = json.loads(buffer.getvalue())
        assert payload["ok"] is True
        assert payload["zh"]
        assert payload["direction"] == "en2zh"
    finally:
        paths_module.CONFIG_PATH = original_config
        paths_module.CACHE_PATH = original_cache
        paths_module.MEMORY_PATH = original_memory


def test_cli_translate_rejects_empty_text():
    import main as main_module

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = main_module._run_translate(["--translate", ""])
    assert code == 2


def test_cli_serve_arg_parsing():
    import main as main_module

    assert main_module._arg_value(["--port", "9000"], "--port") == "9000"
    assert main_module._arg_value(["--port=9001"], "--port") == "9001"
    assert main_module._arg_value([], "--port", "8765") == "8765"


def test_port_falls_back_when_busy():
    """端口被占用时自动往后找，而不是直接失败。"""
    rig = Rig()
    blocker = socket.socket()
    port = free_port()
    blocker.bind(("127.0.0.1", port))
    blocker.listen(1)
    try:
        result = rig.platform.start(port)
        assert result["ok"] is True, result
        assert result["port"] != port
        assert "占用" in (result.get("note") or "")
    finally:
        blocker.close()
        rig.stop()


def test_apply_config_starts_and_stops():
    rig = Rig()
    try:
        rig.config["platform_enabled"] = False
        assert rig.platform.apply_config(rig.config)["running"] is False
        rig.config["platform_enabled"] = True
        started = rig.platform.apply_config(rig.config)
        assert started.get("ok") is True, started
        assert rig.platform.running is True
        rig.config["platform_enabled"] = False
        assert rig.platform.apply_config(rig.config)["running"] is False
        assert rig.platform.running is False
    finally:
        rig.stop()


def test_service_and_platform_share_cache_file():
    """平台翻过的句子，界面（另一个 service 实例）应该直接命中缓存。"""
    rig = make_rig()
    try:
        token = rig.plugin("demo")
        rig.request("POST", "/v1/translate", {"text": "need heals for shroud"},
                    token=token)
        rig.service.flush_cache()
        other = TranslatorService(rig.config, memory=MemoryStore(rig.folder / "m2.json"),
                                  engine=EchoEngine(),
                                  glossary=Glossary({"shroud": "幽影堡"}),
                                  cache_path=rig.folder / "cache.json")
        result = other.translate("need heals for shroud")
        assert result["ok"] is True
        assert result["api_calls"] == 0, "同一份缓存文件必须能互相命中"
    finally:
        rig.stop()
