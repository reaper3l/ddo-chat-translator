"""插件「DDO 论坛构卡汇编」（plugins/ddo-forum-build）的测试。

不打真实网络、不花接口钱：

* 论坛页面用**本地 HTML 文件**或**本机 127.0.0.1 的假论坛服务器**喂给它；
* 翻译用 tests/test_platform.py 里的 EchoEngine 假引擎；
* 每个用例都在临时目录里跑插件的**真进程**（照 test_bundled_example_plugin_runs_end_to_end
  的写法），所以协议、stdin/stdout、权限这些都是一起验的。
"""
from __future__ import annotations

import http.server
import json
import re
import shutil
import sys
import threading
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for path in (str(ROOT), str(HERE)):
    if path not in sys.path:
        sys.path.insert(0, path)

from test_platform import Rig, make_rig               # noqa: E402

PLUGIN_ID = "ddo-forum-build"
PLUGIN_DIR = ROOT / "plugins" / PLUGIN_ID


def _load_module():
    """直接加载插件模块，用来做单元级的解析测试。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location("ddo_forum_build",
                                                  PLUGIN_DIR / "plugin.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _thread_html(title, author, body_html):
    return (
        "<html><head><title>%s | DDO Forums</title></head><body>"
        '<div class="p-body-header"><div class="p-title">'
        '<h1 class="p-title-value">%s</h1></div></div>'
        '<article class="message message--post js-post" data-author="%s">'
        '<div class="message-cell message-cell--user">'
        '<h4 class="message-name"><a class="username">%s</a></h4></div>'
        '<div class="message-cell message-cell--main">'
        '<article class="message-body js-selectToQuote"><div class="bbWrapper">%s</div></article>'
        '<footer class="message-footer">'
        '<div class="message-signature">sent from my signature, ignore me</div>'
        "</footer></div></article></body></html>"
        % (title, title, author, author, body_html))


BUILD_BODY = (
    '<div class="bbCodeBlock bbCodeBlock--quote"><div class="bbCodeBlock-content">'
    "Originally posted by Someone: this quoted text should be dropped</div></div>"
    "The goal: U81 cold alchemist nuker that can run R6 while keeping the "
    "conjuration DC high enough to land on reapers."
    "<br /><br />"
    "Enhancements: 41 points in the elemental tree, then Epic Destiny points into "
    "Draconic Incarnation for the spell power and the extra spell critical chance."
    "<br /><br />"
    "The Shroud is a raid, bring gear for it."
)

SECTION_HTML = (
    "<html><body><div class=\"p-title\"><h1 class=\"p-title-value\">"
    "Character Builds and Classes</h1></div><div class=\"structItem-title\">"
    '<a href="/threads/build-a.123/">U81 First Life Cold Alchemist Nuker</a>'
    '<a href="/threads/build-b.456/post-999">Reaper endgame gear checklist</a>'
    "</div></body></html>"
)


class _ForumHandler(http.server.BaseHTTPRequestHandler):
    """假论坛：只认几张固定的页面，别的都 404。"""

    pages = {}

    def do_GET(self):                                 # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        html = self.pages.get(path)
        if html is None:
            body = b"not found"
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _start_forum(pages):
    handler = type("_Handler", (_ForumHandler,), {"pages": pages})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, "http://127.0.0.1:%d" % server.server_address[1]


def _install_plugin(rig):
    """把仓库里的插件复制进临时插件目录（不在仓库里写 cache / output）。"""
    target = rig.registry.root / PLUGIN_ID
    shutil.copytree(PLUGIN_DIR, target)
    rig.registry.refresh()
    rig.plugin(PLUGIN_ID)
    return target


def _write_sources(rig, *items) -> Path:
    """写一份 sources.txt（地址一行一个，可带 "# 备注"），返回它的路径。"""
    folder = rig.folder / "pages"
    folder.mkdir(exist_ok=True)
    path = folder / "sources.txt"
    path.write_text("".join("%s\n" % item for item in items), encoding="utf-8")
    return path


def _write_thread(rig, title="U81 First Life Cold Alchemist Nuker",
                  author="Archangeluriel", body=BUILD_BODY) -> Path:
    folder = rig.folder / "pages"
    folder.mkdir(exist_ok=True)
    name = re.sub(r"[^0-9A-Za-z]+", "_", title)[:40] + ".html"
    path = folder / name
    path.write_text(_thread_html(title, author, body), encoding="utf-8")
    return path


# ==================================================================== 单元
def test_manifest_and_entry_are_valid():
    manifest = json.loads((PLUGIN_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["id"] == PLUGIN_ID
    assert (PLUGIN_DIR / manifest["entry"]).is_file()
    actions = [item["id"] for item in manifest["actions"]]
    assert actions == ["collect", "collect-clipboard", "collect-file", "collect-cached"]
    for item in manifest["actions"]:
        assert item["name"]
    assert "translate" in manifest["scopes"]


def test_parse_page_extracts_title_posts_and_drops_quotes():
    module = _load_module()
    page = module._parse_page(_thread_html("U81 Test Build", "Tester", BUILD_BODY))
    assert page["structured"] is True
    assert page["title"] == "U81 Test Build"
    assert page["posts"][0]["author"] == "Tester"
    text = page["posts"][0]["text"]
    assert "quoted text should be dropped" not in text      # 引用块要丢掉
    assert "signature" not in text                          # 签名要丢掉
    assert "conjuration DC" in text
    doc = module._build_doc("http://x/thread", "来源", "", page, False, 12)
    assert doc["versions"] == ["U81"]
    assert doc["paragraphs"], doc


def test_detect_version_takes_the_highest():
    module = _load_module()
    assert module._format_version(module._detect_version("U81 first life")) == "U81"
    assert module._format_version(module._detect_version("patch notes U73 and U78")) == "U78"
    assert module._format_version(module._detect_version("Update 66: changes")) == "U66"
    assert module._detect_version("no version here") is None


def test_parse_entries_accepts_json_lines_and_comments():
    module = _load_module()
    entries = module._parse_entries("https://a/1  # 备注\n\n# 整行注释\nhttps://a/2", "t")
    assert [item["url"] for item in entries] == ["https://a/1", "https://a/2"]
    assert entries[0]["note"] == "备注"
    entries = module._parse_entries({"sources": [{"url": "x.html", "note": "本地"}]}, "t")
    assert entries == [{"url": "x.html", "note": "本地"}]
    entries = module._parse_entries(["https://a/1", "https://a/1"], "t")
    assert len(entries) == 1                                # 去重


# ==================================================================== 端到端
def test_collect_from_local_html_end_to_end():
    rig = make_rig()
    try:
        _install_plugin(rig)
        thread = _write_thread(rig)
        listing = _write_sources(rig, "%s  # 构卡帖" % thread)
        out_dir = rig.folder / "out"

        result = rig.registry.run(PLUGIN_ID, "collect-file",
                                  payload={"path": str(listing),
                                           "output_dir": str(out_dir)},
                                  timeout=120, base_url=rig.platform.url())
        assert result["ok"] is True, result
        assert len(result["files"]) == 2
        markdown = Path(result["files"][0]).read_text(encoding="utf-8")
        assert "U81" in markdown
        assert "中:" in markdown, markdown[:400]              # 译文真的从平台取回来了
        assert "幽影堡" in markdown, markdown[:600]            # 平台术语表生效了
        assert "The Shroud is a raid" in markdown             # 英文原文也留着（中英对照）
        assert "机制要点" in markdown
        assert "quoted text should be dropped" not in markdown
        assert result["output"]["version"] == "U81"
        assert result["output"]["api_calls"] >= 1
        data = json.loads(Path(result["files"][1]).read_text(encoding="utf-8"))
        assert data["version"] == "U81" and data["paragraphs"]
        assert rig.engine.calls >= 1
    finally:
        rig.stop()


def test_section_page_expands_threads_then_offline_rerun_uses_cache():
    server, base = _start_forum({
        "/forums/character-builds.24/": SECTION_HTML,
        "/threads/build-a.123/": _thread_html(
            "U81 First Life Cold Alchemist Nuker", "Archangeluriel", BUILD_BODY),
        "/threads/build-b.456/": _thread_html(
            "Reaper endgame gear checklist", "Tester",
            "Bring the gear listed below, the DC breakpoints matter for R6 runs."),
    })
    rig = make_rig()
    try:
        _install_plugin(rig)
        out_dir = rig.folder / "out"
        payload = {"sources": [{"url": base + "/forums/character-builds.24/",
                                "note": "假板块"}],
                   "output_dir": str(out_dir), "threads_per_section": 2}
        result = rig.registry.run(PLUGIN_ID, "collect", payload=payload,
                                  timeout=120, base_url=rig.platform.url())
        assert result["ok"] is True, result
        markdown = Path(result["files"][0]).read_text(encoding="utf-8")
        # 板块页展开成了 2 个主题帖：两个标题都要在文档里
        assert "Cold Alchemist Nuker" in markdown
        assert "Reaper endgame gear checklist" in markdown
        assert result["output"]["sources"] == 3                # 板块页 + 2 个帖子
        assert rig.engine.calls >= 1

        server.shutdown()
        server.server_close()
        calls_before = rig.engine.calls
        offline = rig.registry.run(PLUGIN_ID, "collect-cached", payload=payload,
                                   timeout=120, base_url=rig.platform.url())
        assert offline["ok"] is True, offline
        assert rig.engine.calls == calls_before, "第二次应该命中译文缓存，不再调接口"
        assert "Cold Alchemist Nuker" in Path(offline["files"][0]).read_text(
            encoding="utf-8")
    finally:
        rig.stop()
        server.shutdown()
        server.server_close()


def test_missing_translate_scope_is_a_friendly_message():
    rig = make_rig()
    try:
        _install_plugin(rig)
        rig.tokens.set_scopes(PLUGIN_ID, ["stats"])          # 故意不给翻译权限
        thread = _write_thread(rig)
        listing = _write_sources(rig, thread)
        result = rig.registry.run(PLUGIN_ID, "collect-file",
                                  payload={"path": str(listing),
                                           "output_dir": str(rig.folder / "out")},
                                  timeout=120, base_url=rig.platform.url())
        assert result["ok"] is False
        assert "权限" in result["message"] and "translate" in result["message"]
        assert "Traceback" not in (result.get("stderr") or "")
    finally:
        rig.stop()


def test_only_translate_scope_still_works_one_by_one():
    """只勾了「翻译」没勾「批量」也能用：批量被拒 → 自动退化成一条一条翻。"""
    rig = make_rig()
    try:
        _install_plugin(rig)
        rig.tokens.set_scopes(PLUGIN_ID, ["translate"])
        thread = _write_thread(rig)
        listing = _write_sources(rig, thread)
        result = rig.registry.run(PLUGIN_ID, "collect-file",
                                  payload={"path": str(listing),
                                           "output_dir": str(rig.folder / "out")},
                                  timeout=120, base_url=rig.platform.url())
        assert result["ok"] is True, result
        markdown = Path(result["files"][0]).read_text(encoding="utf-8")
        # 单条接口一次翻一段（假引擎对单条请求是"原样返回"），
        # 所以这里用平台术语表的替换结果来证明译文真的回来了
        assert "幽影堡" in markdown, markdown[:600]
        assert result["output"]["api_calls"] == 3, result["output"]
        assert rig.engine.calls == 3
    finally:
        rig.stop()


def test_no_source_reachable_reports_cleanly():
    rig = make_rig()
    try:
        _install_plugin(rig)
        result = rig.registry.run(PLUGIN_ID, "collect-file",
                                  payload={"path": str(rig.folder / "nope.txt"),
                                           "output_dir": str(rig.folder / "out")},
                                  timeout=60, base_url=rig.platform.url())
        assert result["ok"] is False
        assert "找不到" in result["message"]
        assert "Traceback" not in (result.get("stderr") or "")
    finally:
        rig.stop()


def test_platform_off_is_handled_without_network_calls():
    """宿主没给平台地址（平台没开）时，插件只整理英文原文，不崩。"""
    rig = make_rig()
    try:
        _install_plugin(rig)
        thread = _write_thread(rig, title="U81 Test")
        listing = _write_sources(rig, thread)
        result = rig.registry.run(PLUGIN_ID, "collect-file",
                                  payload={"path": str(listing),
                                           "output_dir": str(rig.folder / "out")},
                                  timeout=60, base_url="")
        assert result["ok"] is True, result
        markdown = Path(result["files"][0]).read_text(encoding="utf-8")
        assert "The Shroud is a raid" in markdown
        assert "没有译文" in markdown
    finally:
        rig.stop()
