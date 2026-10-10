"""DDO 官方论坛构卡资料汇编 —— 插件（只用标准库）。

它做什么
--------
1. **抓**：把 DDO 官方论坛（forums.ddo.com）里跟"构卡 / 游戏机制"有关的板块页、
   主题帖页抓下来；也可以直接喂本地存好的 HTML 文件（测试和离线走这条）；
2. **拆**：取出标题、作者、正文段落，丢掉引用块、签名、评分栏这些噪音；
3. **认版本**：扫正文里的 `U81` / `Update 81` / `Module 12` 线索，取最大的那个
   当"当前版本"，写在文档标题上；
4. **挑重点**：统计构卡关键词（Enhancement / Epic Destiny / Feat / DC / PRR …）
   的命中次数，按分数挑出最值得看的段落；
5. **翻译**：把挑出来的英文段落**一批一批**发给本机翻译平台（本地命中的不花钱）；
6. **成文**：写一份中文 Markdown（版本 + 来源清单 + 机制要点表 + 中英对照正文
   + 术语附录），另存一份同名 `.json` 给别的程序用。

权限
----
* 平台要在「设置 → 平台 / 插件」里开着；
* 要给本插件勾上 **translate**（翻译）——默认就有；顺手勾上 **batch**（批量）更省钱；
* 勾上 **terms:read** 的话，附录里会带上平台术语表的官方译名（可选，不加也能跑）；
* 权限不够时**正常退出 + 说人话**，不会崩：403 / 401 / 429 都有对应提示。

规矩
----
* 只抓公开页面，抓完就退出（一次动作、一次输出，不常驻）；
* 抓到的页面缓存在插件目录 `cache/` 下（默认 7 天），同一份内容不反复打扰论坛；
* 只通过平台翻译，**不存任何 API Key**。
"""
from __future__ import annotations

import hashlib
import html as html_module
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUTPUT_DIR = HERE / "output"
CACHE_DIR = HERE / "cache"

# ---------------------------------------------------------------- 默认参数
PAGE_TTL = 7 * 24 * 3600          # 抓下来的网页缓存几天
MAX_BYTES = 3_000_000             # 单页最多读多少字节
FETCH_TIMEOUT = 30.0
FETCH_GAP = 0.8                   # 两次抓取之间歇一下，别把论坛当压测目标
BATCH_SIZE = 20                   # 一次给平台多少段
MAX_SOURCES = 12                  # 来源清单最多认几条
MAX_PAGES = 24                    # 最多抓几页（板块页展开出的主题帖也算）
MAX_THREADS = 16                  # 板块页里最多展开几个主题帖
THREADS_PER_SECTION = 4
MAX_POSTS_PER_SOURCE = 8          # 一个主题帖最多看几层楼
KEEP_PER_THREAD = 12              # 一个主题帖最多留几段
MAX_PARAGRAPHS = 160              # 整篇文档最多翻几段
MIN_PARAGRAPH_CHARS = 30
MAX_PARAGRAPH_CHARS = 1200
TIME_BUDGET = 480.0               # 翻译阶段的时间预算（宿主上限 10 分钟）

_UA = "Mozilla/5.0 (DDO-Translator-Plugin/ddo-forum-build)"

# 官方论坛里跟"构卡 / 机制"最相关的板块（真实地址，抄自论坛首页）。
# 真正的清单在 sources.json 里，这里只是那份文件丢了之后的兜底。
DEFAULT_SOURCES = [
    {"url": "https://forums.ddo.com/index.php?forums/character-builds-and-classes.24/",
     "note": "角色构筑与职业（构卡主板块）"},
    {"url": "https://forums.ddo.com/index.php?forums/gameplay-and-game-systems.55/",
     "note": "玩法与游戏系统（机制）"},
    {"url": "https://forums.ddo.com/index.php?forums/player-guides-strategies-walkthoughs-and-faq.64/",
     "note": "玩家攻略 / 常见问题"},
    {"url": "https://forums.ddo.com/index.php?forums/release-notes.7/",
     "note": "更新公告（版本号线索）"},
    {"url": "https://forums.ddo.com/index.php?forums/developer-diaries-and-official-discussions.9/",
     "note": "开发者日记 / 官方讨论"},
]

# 构卡关键词：英文（原文里找）+ 中文（文档里给人看）
KEYWORDS = [
    ("Epic Destiny", "传奇命运"),
    ("Destiny Point", "命运点"),
    ("Enhancement", "增强（天赋树）"),
    ("Feat", "专长"),
    ("AP", "增强点(AP)"),
    ("PRR", "物理抗性(PRR)"),
    ("MRR", "魔法抗性(MRR)"),
    ("DC", "法术难度(DC)"),
    ("DPS", "输出(DPS)"),
    ("Spell Power", "法术强度"),
    ("Melee Power", "近战强度"),
    ("Ranged Power", "远程强度"),
    ("Caster Level", "施法者等级"),
    ("Spell Critical", "法术暴击"),
    ("Armor Class", "防御等级(AC)"),
    ("Dodge", "闪避"),
    ("Reaper", "收割者(Reaper)"),
    ("RXP", "收割经验(RXP)"),
    ("Past Life", "前世"),
    ("Iconic", "标志性英雄"),
    ("Multiclass", "多职业"),
    ("Splash", "兼职"),
    ("Set Bonus", "套装效果"),
    ("Filigree", "金丝"),
    ("Sentient", "灵性武器"),
    ("Crafting", "制作"),
    ("Gear", "装备"),
    ("Rotation", "输出循环"),
    ("Tier 5", "五层增强"),
    ("Unyielding Sentinel", "不屈哨卫"),
    ("Fatesinger", "命运歌者"),
    ("Draconic Incarnation", "龙裔化身"),
    ("Shadowdancer", "暗影舞者"),
    ("Exalted Angel", "崇高天使"),
    ("Fury of the Wild", "荒野之怒"),
    ("Legendary Dreadnought", "传奇无畏舰"),
    ("Grandmaster of Flowers", "花之宗师"),
    ("Magus of the Eclipse", "月蚀法师"),
    ("Primal Avatar", "原始化身"),
    ("Machrotechnic", "机械术师"),
]


# ------------------------------------------------------------------ 入口
def main(argv) -> int:
    action = argv[1] if len(argv) > 1 else ""
    request = _read_request()
    payload = request.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    platform = request.get("platform") or {}
    base_url = str(platform.get("base_url") or os.environ.get("DDO_PLATFORM_URL") or "").strip()
    token = str(platform.get("token") or os.environ.get("DDO_PLATFORM_TOKEN") or "").strip()
    try:
        return _run(action, payload, base_url, token)
    except ValueError as exc:
        return _reply(False, str(exc))
    except Exception as exc:                          # noqa: BLE001
        return _reply(False, "插件内部出错：%s: %s" % (type(exc).__name__, exc))


def _run(action: str, payload: dict, base_url: str, token: str) -> int:
    started = time.time()
    try:
        time_budget = float(payload.get("time_budget") or TIME_BUDGET)
    except Exception:                                 # noqa: BLE001
        time_budget = TIME_BUDGET
    deadline = started + max(30.0, time_budget)

    entries = _resolve_sources(action, payload)
    options = {
        "offline": bool(payload.get("offline")) or action == "collect-cached",
        "refresh": bool(payload.get("refresh")),
        "deadline": deadline,                         # 抓取阶段也要看时间，别整体超时被杀
        "max_pages": _as_int(payload.get("max_pages"), MAX_PAGES),
        "max_threads": _as_int(payload.get("max_threads"), MAX_THREADS),
        "threads_per_section": _as_int(payload.get("threads_per_section"),
                                       THREADS_PER_SECTION),
        "keep_per_thread": _as_int(payload.get("keep_per_thread"), KEEP_PER_THREAD),
        "max_paragraphs": _as_int(payload.get("max_paragraphs"), MAX_PARAGRAPHS),
    }
    if action == "collect-cached":
        options["offline"] = True

    docs, warnings = _collect(entries, options)

    # 把每个来源挑出来的段落摊平，做一次全局排序 + 截断
    items = []
    for doc_index, doc in enumerate(docs):
        for para in doc["paragraphs"]:
            items.append({"doc": doc_index, "order": len(items), "text": para["text"],
                          "score": para["score"], "author": para["author"],
                          "post": para["post"], "zh": ""})
    kept = _rank_and_cut(items, options["max_paragraphs"])
    if not kept:
        detail = "；".join(warnings[:3]) if warnings else "清单里的地址都没抓到正文"
        return _reply(False, "这次没汇总到可用的正文段落：%s" % detail)

    # 翻译（平台地址为空 = 只整理英文；权限 / 配额问题都在这里变成人话）
    api_calls = 0
    if base_url:
        cache = _load_translation_cache()
        outcome = _translate_all(base_url, token, [item["text"] for item in kept],
                                 cache, deadline)
        if outcome["fatal"]:
            return _reply(False, outcome["fatal"])
        if outcome["note"]:
            warnings.append(outcome["note"])
        if outcome["errors"]:
            warnings.append("%d 段没翻成（平台回的原话：%s）"
                            % (len(outcome["errors"]), outcome["errors"][0]))
        api_calls = outcome["api_calls"]
        for item, zh in zip(kept, outcome["zh"]):
            item["zh"] = zh
        _save_translation_cache(cache)
    else:
        warnings.append("宿主没有把平台地址传进来，这次只整理了英文原文，没有译文。")

    version_label, version_notes = _pick_version(docs, payload)
    keyword_rows = _keyword_rows(docs)
    glossary_rows = _glossary_rows(keyword_rows, base_url, token)
    generated_at = time.strftime("%Y-%m-%d %H:%M")

    markdown = _compose_markdown(docs, kept, version_label, version_notes,
                                 keyword_rows, glossary_rows, warnings,
                                 api_calls, generated_at)
    data = {
        "version": version_label,
        "generated_at": generated_at,
        "action": action,
        "warnings": warnings,
        "api_calls": api_calls,
        "sources": [{"url": doc["url"], "title": doc["title"],
                     "section": doc["section"], "author": doc["author"],
                     "posts": doc["posts"], "paragraphs": len(doc["paragraphs"]),
                     "versions": doc["versions"], "cached": doc["cached"],
                     "error": doc["error"], "kind": doc["kind"]} for doc in docs],
        "keywords": keyword_rows,
        "paragraphs": [{"title": docs[item["doc"]]["title"],
                        "author": item["author"], "text": item["text"],
                        "zh": item["zh"], "score": round(item["score"], 2)}
                       for item in kept],
    }

    try:
        out_dir = Path(str(payload.get("output_dir") or OUTPUT_DIR))
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        slug = re.sub(r"[^0-9A-Za-z]+", "", version_label) or "unknown"
        md_path = out_dir / ("构卡汇编_%s_%s.md" % (slug, stamp))
        json_path = out_dir / ("构卡汇编_%s_%s.json" % (slug, stamp))
        md_path.write_text(markdown, encoding="utf-8")
        json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    except Exception as exc:                          # noqa: BLE001
        return _reply(False, "汇总好了，但文件写不出去：%s" % exc)

    message = ("已汇总 %d 个来源、%d 段，版本 %s；真实接口调用 %d 次。生成：%s"
               % (len(docs), len(kept), version_label, api_calls, md_path))
    if warnings:
        message += "（%d 条提示，见文档开头）" % len(warnings)
    return _reply(True, message, text=markdown,
                  output={"version": version_label, "sources": len(docs),
                          "paragraphs": len(kept), "api_calls": api_calls,
                          "warnings": warnings[:5],
                          "markdown": str(md_path), "json": str(json_path)},
                  files=[str(md_path), str(json_path)])


# ------------------------------------------------------------------ 取来源
def _resolve_sources(action: str, payload: dict) -> list:
    if payload.get("sources"):
        entries = _parse_entries(payload["sources"], "payload.sources")
    elif action == "collect-clipboard":
        text = _clipboard()
        if not text.strip():
            raise ValueError("剪贴板是空的。先 Ctrl+C 复制论坛地址（一行一个），再点这个动作。")
        entries = _parse_entries(text, "剪贴板")
    elif action == "collect-file":
        path = Path(str(payload.get("path") or (HERE / "sources.txt")))
        if not path.is_file():
            raise ValueError("找不到 %s（把地址一行一个写进这个文件，"
                             "或直接在插件目录下放个 sources.txt）" % path)
        entries = _parse_entries(path.read_text(encoding="utf-8", errors="replace"),
                                 str(path))
    elif action in ("collect", "collect-cached", ""):
        entries = _parse_entries(_default_sources_text(), "sources.json")
    else:
        raise ValueError("不认识的动作：%s" % action)
    if not entries:
        raise ValueError("来源清单是空的。可在插件目录的 sources.json 里加板块 / 主题帖地址。")
    return entries[:MAX_SOURCES]


def _default_sources_text() -> str:
    path = HERE / "sources.json"
    if path.is_file():
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except Exception:                             # noqa: BLE001
            pass
    return json.dumps({"sources": DEFAULT_SOURCES}, ensure_ascii=False)


def _parse_entries(value, origin: str) -> list:
    """把 sources.json / 剪贴板 / sources.txt 都认成 [{'url':..., 'note':...}]。"""
    raw_items = []
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("{") or text.startswith("["):
            try:
                value = json.loads(text)
            except Exception:                         # noqa: BLE001
                value = text
        if isinstance(value, str):
            raw_items = value.splitlines()
    if not raw_items:
        if isinstance(value, dict):
            raw_items = value.get("sources") or []
        elif isinstance(value, list):
            raw_items = value
    entries, seen = [], set()
    for item in raw_items:
        if isinstance(item, dict):
            url = str(item.get("url") or item.get("path") or "").strip()
            note = str(item.get("note") or item.get("title") or "").strip()
        else:
            line = str(item or "").strip()
            # 支持 "地址 # 备注"（只认“空格+#”，免得误伤网址里的 #）
            parts = re.split(r"\s+#\s*", line, maxsplit=1)
            url, note = parts[0].strip(), (parts[1].strip() if len(parts) > 1 else "")
        if not url or url.startswith("#") or url in seen:
            continue
        seen.add(url)
        entries.append({"url": url, "note": note})
    return entries


def _clipboard() -> str:
    """读 Windows 剪贴板（失败就返回空，不抛异常）。"""
    try:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        try:
            return str(root.clipboard_get())
        finally:
            root.destroy()
    except Exception:                                 # noqa: BLE001
        return ""


# ------------------------------------------------------------------ 抓页面
def _collect(entries: list, options: dict) -> tuple:
    docs, warnings = [], []
    pages = 0
    threads_left = options["max_threads"]
    for entry in entries:
        if _out_of_time(options):
            warnings.append("时间预算用完了，剩下的来源这轮没抓（下次会命中缓存）。")
            break
        if pages >= options["max_pages"]:
            warnings.append("抓取页数到上限（%d 页），后面的来源这轮没抓。"
                            % options["max_pages"])
            break
        target = entry["url"]
        label = entry.get("note") or target
        fetched = _load_target(target, options)
        pages += 1
        if fetched.get("note"):
            warnings.append(fetched["note"])
        if not fetched["text"]:
            warnings.append("抓不到「%s」：%s" % (label, fetched["error"]))
            docs.append(_blank_doc(target, label, fetched["error"]))
            continue
        page = _parse_page(fetched["text"])
        if page["structured"] or not _looks_like_html(fetched["text"]):
            docs.append(_build_doc(target, label, "", page, fetched["cached"],
                                   options["keep_per_thread"]))
            continue
        links = _extract_thread_links(fetched["text"], target,
                                      min(options["threads_per_section"], threads_left))
        if not links:
            warnings.append("「%s」的页面结构没认出来（不像主题帖、也没找到帖子链接），"
                            "按整页纯文本处理。" % label)
            docs.append(_build_doc(target, label, "", _plain_page(fetched["text"]),
                                   fetched["cached"], options["keep_per_thread"]))
            continue
        docs.append(_section_doc(target, label, page["title"], len(links),
                                 fetched["text"]))
        for link in links:
            if _out_of_time(options):
                warnings.append("时间预算用完了，「%s」里还有没抓的主题帖。" % label)
                break
            if pages >= options["max_pages"] or threads_left <= 0:
                warnings.append("主题帖数量到上限（%d 个），「%s」里还有没抓的。"
                                % (options["max_threads"], label))
                break
            child = _load_target(link["url"], options)
            pages += 1
            threads_left -= 1
            child_note = link.get("note") or link["url"]
            if child.get("note"):
                warnings.append(child["note"])
            if not child["text"]:
                warnings.append("主题帖抓不到「%s」：%s" % (child_note, child["error"]))
                docs.append(_blank_doc(link["url"], child_note, child["error"], label))
                continue
            child_page = _parse_page(child["text"])
            docs.append(_build_doc(link["url"], child_note, label, child_page,
                                   child["cached"], options["keep_per_thread"]))
    return docs, warnings


def _out_of_time(options: dict) -> bool:
    return time.time() > float(options.get("deadline") or 0)


def _load_target(target: str, options: dict) -> dict:
    """本地 HTML 文件直接读；网址走带缓存的抓取。返回 {text,cached,error,note}。"""
    if _is_url(target):
        return _fetch_page(target, options)
    path = Path(target)
    if not path.is_absolute():
        path = HERE / target
    try:
        if not path.is_file():
            return {"text": "", "cached": False, "error": "找不到文件 %s" % path,
                    "note": ""}
        text = path.read_text(encoding="utf-8", errors="replace")
        return {"text": text[:MAX_BYTES], "cached": False, "error": "", "note": ""}
    except Exception as exc:                          # noqa: BLE001
        return {"text": "", "cached": False, "error": "读不了文件：%s" % exc, "note": ""}


def _fetch_page(url: str, options: dict) -> dict:
    key = hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]
    html_path = CACHE_DIR / "pages" / (key + ".html")
    meta_path = CACHE_DIR / "pages" / (key + ".json")
    cached_text, cached_at = _read_page_cache(html_path, meta_path)

    if cached_text is not None and not options["refresh"] and cached_at:
        if time.time() - cached_at <= PAGE_TTL:
            return {"text": cached_text, "cached": True, "error": "", "note": ""}
    if options["offline"]:
        if cached_text is not None:
            return {"text": cached_text, "cached": True, "error": "",
                    "note": "%s 用的是旧缓存（离线模式）。" % url}
        return {"text": "", "cached": False, "error": "离线模式，而这一页还没抓过",
                "note": ""}

    remaining = float(options.get("deadline") or 0) - time.time()
    if 0 < remaining < 3:
        return {"text": "", "cached": False, "error": "时间不够了，跳过这一页", "note": ""}
    timeout = FETCH_TIMEOUT if remaining <= 0 else min(FETCH_TIMEOUT, remaining)
    time.sleep(FETCH_GAP)
    request = urllib.request.Request(url, headers={"User-Agent": _UA,
                                                   "Accept": "text/html,*/*"})
    charset = "utf-8"
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_BYTES + 1)
            charset = response.headers.get_content_charset() or charset
    except urllib.error.HTTPError as exc:
        reason = "HTTP %s %s" % (exc.code, exc.reason)
    except urllib.error.URLError as exc:
        reason = "连不上（%s）" % getattr(exc, "reason", exc)
    except Exception as exc:                          # noqa: BLE001
        reason = str(exc)
    else:
        text = raw[:MAX_BYTES].decode(charset, errors="replace")
        _write_page_cache(html_path, meta_path, text, url)
        return {"text": text, "cached": False, "error": "", "note": ""}

    if cached_text is not None:
        return {"text": cached_text, "cached": True, "error": "",
                "note": "%s 这次没抓到（%s），先用上次的缓存。" % (url, reason)}
    return {"text": "", "cached": False, "error": reason, "note": ""}


def _read_page_cache(html_path: Path, meta_path: Path):
    try:
        if not html_path.is_file():
            return None, 0.0
        info = {}
        if meta_path.is_file():
            info = json.loads(meta_path.read_text(encoding="utf-8"))
        return html_path.read_text(encoding="utf-8", errors="replace"), float(
            info.get("fetched_at") or 0.0)
    except Exception:                                 # noqa: BLE001
        return None, 0.0


def _write_page_cache(html_path: Path, meta_path: Path, text: str, url: str) -> None:
    try:
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(text, encoding="utf-8")
        meta_path.write_text(json.dumps({"url": url, "fetched_at": time.time()},
                                        ensure_ascii=False), encoding="utf-8")
    except Exception:                                 # noqa: BLE001
        pass


def _is_url(target: str) -> bool:
    return str(target).lower().startswith(("http://", "https://"))


def _looks_like_html(text: str) -> bool:
    return "<" in text[:2000] and ">" in text[:2000]


# ------------------------------------------------------------------ 拆页面
_SKIP_TAGS = {"script", "style", "noscript", "svg", "iframe", "canvas", "template",
              "object", "embed", "form", "nav", "select", "textarea", "button",
              "audio", "video", "map"}
_SKIP_CLASS_RE = re.compile(
    r"(?i)(?:^|\s)(?:signature|message-signature|bbcodequote|bbcodeblock--quote|"
    r"attachments|message-attribution|message-footer|message-user|message-avatar|"
    r"reactionsbar|bookmarklink|sharebuttons|pagination|js-postmenu|messageshare|"
    r"guestwarning|sidebarbox|pagenav|threadlist|pollcontainer)(?:\s|$)")
_SKIP_ID_RE = re.compile(r"(?i)^(?:footer|nav|search|pagenav|sidebar|comments)")
_BLOCK_TAGS = {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
               "blockquote", "article", "section", "header", "footer", "ul", "ol",
               "table", "pre", "dl", "dt", "dd"}
_POST_BODY_CLASSES = ("message-body", "bbwrapper", "postbody", "postcontent")
_POST_BODY_IDS = ("post_message_", "postmessage_")

# 页面上的固定噪音（登录提示、评分栏、跳转按钮…），整行丢掉
_NOISE_RE = re.compile(
    r"(?i)^(?:click (?:to expand|to shrink)|originally posted by\b.*|"
    r"last edited(?:\s*:|\s+by\b).*|join date\s*:.*|location\s*:.*|"
    r"posts\s*:\s*\d+|reaction score.*|trophy points.*|#\d+|quote(?:\s*\+)?|"
    r"like|reply|share|bookmark|permalink|view attachment.*|attachments?|"
    r"report|edit|delete|signature|you must log (?:in|on) or register.*|log in|"
    r"register|trending|what'?s new|members|latest activity|search forums|"
    r"menu|ddo forums?|forums\.ddo\.com|ddo\.com|standing stone games|"
    r"(?:next|previous)(?: page)?|page \d+ of \d+)$")


class _PageParser(HTMLParser):
    """把 XenForo / vBulletin 的帖子页拆成"标题 + 若干层楼的正文"。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.structured = False
        self.posts = []
        self._stack = []
        self._skip = 0
        self._capture = 0
        self._buffer = []
        self._author = ""
        self._pending_author = ""
        self._title_parts = []
        self._title_kind = ""

    def handle_starttag(self, tag, attrs):
        values = {}
        for key, value in attrs:
            values[str(key).lower()] = value or ""
        cls = values.get("class", "").lower()
        ident = values.get("id", "").lower()

        if self._skip:
            self._stack.append("skip")
            self._skip += 1
            return
        if tag in _SKIP_TAGS or _SKIP_CLASS_RE.search(cls) or _SKIP_ID_RE.match(ident):
            self._stack.append("skip")
            self._skip = 1
            return
        if values.get("data-author"):
            self._pending_author = values["data-author"].strip()

        if tag == "h1" and "p-title-value" in cls:
            self._stack.append("title")
            self._title_kind = "h1"
            self._title_parts = []
            return
        if tag == "title":
            self._stack.append("title")
            if not self._title_kind:
                self._title_kind = "doc"
            self._title_parts = []
            return

        if not self._capture and _is_post_body(tag, cls, ident):
            self._capture = 1
            self._stack.append("capture")
            self._buffer = []
            self._author = self._pending_author
            return
        if self._capture:
            self._capture += 1
            self._stack.append("capture")
            if tag == "br":
                self._buffer.append("\n")
            elif tag == "li":
                self._buffer.append("\n- ")
            elif tag in _BLOCK_TAGS:
                self._buffer.append("\n")

    def handle_endtag(self, tag):
        if not self._stack:
            return
        state = self._stack.pop()
        if state == "skip":
            if self._skip:
                self._skip -= 1
            return
        if state == "title":
            chunk = _squash("".join(self._title_parts))
            self._title_parts = []
            if chunk and (not self.title or self._title_kind == "h1"):
                self.title = chunk
            self._title_kind = ""
            return
        if state == "capture":
            if self._capture:
                self._capture -= 1
            if self._capture == 0:
                text = _clean_text("".join(self._buffer))
                if text:
                    self.structured = True
                    self.posts.append({"author": self._author, "text": text})
                self._buffer = []
            elif tag in _BLOCK_TAGS:
                self._buffer.append("\n")

    def handle_data(self, data):
        if self._title_kind and not self._skip:
            self._title_parts.append(data)
        if self._capture and not self._skip:
            self._buffer.append(data)


def _is_post_body(tag: str, cls: str, ident: str) -> bool:
    if tag not in ("article", "div", "td", "section"):
        return False
    if any(name in cls for name in _POST_BODY_CLASSES):
        return True
    return any(ident.startswith(prefix) for prefix in _POST_BODY_IDS)


def _parse_page(text: str) -> dict:
    title, posts, structured = "", [], False
    if _looks_like_html(text):
        parser = _PageParser()
        try:
            parser.feed(text)
            parser.close()
        except Exception:                             # noqa: BLE001
            pass
        title, posts, structured = parser.title, parser.posts, parser.structured
    return {"title": _clean_title(title), "posts": posts, "structured": structured}


def _plain_page(text: str) -> dict:
    body = _clean_text(_strip_html(text)) if _looks_like_html(text) else text
    return {"title": "", "posts": [{"author": "", "text": body}], "structured": False}


_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|tr|h[1-6]|article|section|blockquote)>", "\n", text)
    text = _TAG_RE.sub("", text)
    return html_module.unescape(text)


def _clean_title(title: str) -> str:
    title = _squash(html_module.unescape(title or ""))
    if "|" in title:
        title = title.split("|")[0].strip()
    return title[:200]


_THREAD_HREF_RE = re.compile(
    r'(?is)<a\s[^>]*href="([^"]*?/?(?:index\.php\?)?threads/[^"#]*?)"[^>]*>(.*?)</a>')


def _extract_thread_links(text: str, base: str, limit: int) -> list:
    """板块页 → 主题帖链接（挑不出就返回空，调用方会退化成整页纯文本）。"""
    found, seen = [], set()
    for match in _THREAD_HREF_RE.finditer(text or ""):
        href = match.group(1)
        label = _clean_title(_strip_html(match.group(2)))
        if len(label) < 8:
            continue
        url = urllib.parse.urljoin(base, href)
        key = re.sub(r"/post-\d+/?$", "", url.split("#")[0])
        if key in seen:
            continue
        seen.add(key)
        found.append({"url": key, "note": label})
        if len(found) >= limit:
            break
    return found


# ------------------------------------------------------------------ 清洗 / 挑段
def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def _clean_text(raw: str) -> str:
    text = (raw or "").replace("\xa0", " ").replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for line in text.split("\n"):
        line = re.sub(r"[ \t\f\v]+", " ", line).strip()
        # 论坛用 CSS 排版时，序号和正文会粘在一起（"22Epic Destiny Feat…"）
        line = re.sub(r"(\d)([A-Z][a-z])", r"\1 \2", line)
        if line and _NOISE_RE.match(line):
            continue
        if not line and (not lines or not lines[-1]):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _split_paragraphs(text: str) -> list:
    blocks = []
    for chunk in re.split(r"\n\s*\n", text or ""):
        lines = [line.strip() for line in chunk.splitlines() if line.strip()]
        if not lines:
            continue
        # 表格 / 列表在纯文本里是一行一格，逐行成段更好读、也好翻；
        # 但"上一行没句末标点、下一行小写开头"说明是一句话被折行，拼回去。
        merged = []
        for line in lines:
            if (merged and line[:1].islower()
                    and not re.search(r"[.!?:;)\]\"']$", merged[-1])):
                merged[-1] = merged[-1] + " " + line
            else:
                merged.append(line)
        for block in merged:
            block = _squash(block)
            if not block:
                continue
            if len(block) <= MAX_PARAGRAPH_CHARS:
                blocks.append(block)
            else:
                blocks.extend(_split_long(block))
    return [block for block in blocks if _keep_paragraph(block)]


def _split_long(block: str) -> list:
    pieces, current = [], ""
    for sentence in re.split(r"(?<=[.!?;])\s+", block):
        if len(current) + len(sentence) > 900 and current:
            pieces.append(current.strip())
            current = sentence
        else:
            current = (current + " " + sentence).strip()
    if current.strip():
        pieces.append(current.strip())
    return pieces


def _keep_paragraph(text: str) -> bool:
    if len(text) < MIN_PARAGRAPH_CHARS:
        # 短行也可能有用：像 "Enhancements:" / "Tier 5:" 这种小标题
        return bool(re.search(r"[A-Za-z]{3}", text)) and (
            text.endswith(":") or len(text.split()) <= 6)
    if text.count("http") >= 2 and len(re.findall(r"[A-Za-z]{3,}", text)) < 12:
        return False
    if re.match(r"(?i)^(?:>|quote\s*:)", text):
        return False
    return True


_ACRONYM_RE = re.compile(r"^[A-Z][A-Z0-9]{1,4}$")


def _hits(text: str, term: str) -> int:
    if _ACRONYM_RE.match(term):
        pattern = r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])"
        return len(re.findall(pattern, text or ""))
    return len(re.findall(re.escape(term), text or "", re.IGNORECASE))


def _score(text: str) -> float:
    hits = sum(_hits(text, term) for term, _zh in KEYWORDS)
    score = hits * 3.0 + min(len(text), 800) / 400.0
    if re.search(r"(?i)\b(?:builds?|gears?|guides?|rotations?|items?|breakdown|"
                 r"stats?|feats?|enhancements?|destin(?:y|ies)|screenshots?)\b", text):
        score += 1.5
    if re.search(r"(?i)\b(?:U|Update)\s?\d{1,3}\b", text):
        score += 1.5
    return score


def _select_paragraphs(paragraphs: list, limit: int) -> list:
    if len(paragraphs) <= limit:
        return paragraphs
    ranked = sorted(range(len(paragraphs)),
                    key=lambda i: paragraphs[i]["score"], reverse=True)[:limit]
    return [paragraphs[i] for i in sorted(ranked)]


def _rank_and_cut(items: list, limit: int) -> list:
    if len(items) <= limit:
        return items
    ranked = sorted(range(len(items)), key=lambda i: items[i]["score"], reverse=True)
    return [items[i] for i in sorted(ranked[:limit])]


_VERSION_RES = (
    ("U", re.compile(r"(?<![A-Za-z])U\s?(\d{1,3})(?![0-9])")),
    ("Update", re.compile(r"(?i)(?<![A-Za-z])Update\s*#?\s*(\d{1,3})(?![0-9])")),
    ("Module", re.compile(r"(?i)(?<![A-Za-z])Module\s*(\d{1,2})(?![0-9])")),
)


def _detect_version(text: str):
    """返回 (类型, 数字) —— 例如 ("U", 81)；没线索就 None。"""
    best = None
    for kind, pattern in _VERSION_RES:
        for match in pattern.finditer(text or ""):
            number = int(match.group(1))
            if number <= 0 or (kind == "U" and number > 200):
                continue
            if best is None or number > best[1]:
                best = (kind, number)
    return best


def _format_version(found) -> str:
    if not found:
        return ""
    kind, number = found
    return "Module %d" % number if kind == "Module" else "U%d" % number


# ------------------------------------------------------------------ 组装来源
def _build_doc(url: str, label: str, section: str, page: dict, cached: bool,
               keep: int) -> dict:
    paragraphs = []
    for index, post in enumerate((page.get("posts") or [])[:MAX_POSTS_PER_SOURCE]):
        for chunk in _split_paragraphs(post.get("text") or ""):
            paragraphs.append({"text": chunk, "score": _score(chunk),
                               "author": post.get("author") or "",
                               "post": index + 1})
    selected = _select_paragraphs(paragraphs, keep)
    scanned = [page.get("title") or ""] + [post.get("text") or ""
                                           for post in (page.get("posts") or [])]
    versions = []
    for chunk in scanned:
        found = _detect_version(chunk)
        if found:
            versions.append(_format_version(found))
    return {"url": url, "label": label, "section": section,
            "title": page.get("title") or label or url,
            "author": (page.get("posts") or [{}])[0].get("author") or "",
            "posts": len(page.get("posts") or []), "paragraphs": selected,
            "versions": _dedupe(versions)[:6], "cached": bool(cached),
            "error": "", "kind": "thread"}


def _blank_doc(url: str, label: str, error: str, section: str = "") -> dict:
    return {"url": url, "label": label, "section": section, "title": label or url,
            "author": "", "posts": 0, "paragraphs": [], "versions": [],
            "cached": False, "error": error, "kind": "error"}


def _section_doc(url: str, label: str, title: str, links: int, text: str = "") -> dict:
    # 板块页上那一堆帖子标题也是版本线索（"U81 …"），顺手一起认；
    # 认不出来也不影响正文，只是文档标题写"未知版本"。
    found = _detect_version(_strip_html(text[:200_000]) if _looks_like_html(text) else text)
    return {"url": url, "label": label, "section": "", "title": title or label,
            "author": "", "posts": links, "paragraphs": [],
            "versions": [_format_version(found)] if found else [],
            "cached": False, "error": "", "kind": "section"}


def _dedupe(items: list) -> list:
    seen, out = set(), []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _pick_version(docs: list, payload: dict) -> tuple:
    """当前版本 = 来源里出现过的最大 U 编号；payload.game_version 优先。"""
    forced = str(payload.get("game_version") or "").strip()
    if forced:
        return forced, ["版本号由调用方指定"]
    best, notes = None, []
    for doc in docs:
        for label in doc["versions"]:
            found = _detect_version(label)
            if found and (best is None or found[1] > best[1]):
                best = found
    if best is None:
        return "未知版本", ["收集到的内容里没写 Update / U 编号"]
    for doc in docs:
        hits = [label for label in doc["versions"] if _detect_version(label) == best]
        if hits:
            notes.append("%s（%s）" % (hits[0], doc["title"][:40]))
        if len(notes) >= 3:
            break
    return _format_version(best), notes


def _keyword_rows(docs: list) -> list:
    rows = []
    for term, zh in KEYWORDS:
        total, where = 0, []
        for doc in docs:
            count = sum(_hits(para["text"], term) for para in doc["paragraphs"])
            if count:
                total += count
                where.append(doc["title"][:28])
        if total:
            rows.append({"term": term, "zh": zh, "count": total,
                         "sources": _dedupe(where)[:3]})
    rows.sort(key=lambda row: row["count"], reverse=True)
    return rows


def _glossary_rows(keyword_rows: list, base_url: str, token: str) -> list:
    """附录里的术语：能读平台术语表就用它的官方译名，读不到就用内置对照。"""
    if not keyword_rows:
        return []
    rows = [dict(row) for row in keyword_rows]
    terms = _lookup_terms(base_url, token, [row["term"] for row in rows[:30]])
    for row in rows:
        row["official"] = terms.get(row["term"].lower(), "")
    return rows


def _lookup_terms(base_url: str, token: str, words: list) -> dict:
    """查平台术语表；没权限 / 没开平台就安安静静返回空（不打扰用户）。"""
    found = {}
    if not base_url or not words:
        return found
    for word in words:
        result = _get(base_url, token, "/v1/terms?q=" + urllib.parse.quote(word))
        if not result.get("ok"):
            if str(result.get("code") or "") in ("forbidden", "unauthorized"):
                return found
            continue
        if result.get("found") and result.get("zh"):
            found[word.lower()] = str(result["zh"])
    return found


# ------------------------------------------------------------------ 翻译
def _translate_all(base_url: str, token: str, texts: list, cache: dict,
                   deadline: float) -> dict:
    zh = [cache.get(_cache_key(text)) or "" for text in texts]
    pending = [index for index, value in enumerate(zh) if not value]
    api_calls, errors, note, fatal = 0, [], "", ""
    batch_ok = True
    position = 0
    while position < len(pending):
        if time.time() > deadline:
            note = ("时间预算用完了，还有 %d 段没翻；已经翻好的照样在文档里"
                    "（下次再跑会命中缓存，不会白花）。" % (len(pending) - position))
            break
        group = pending[position:position + BATCH_SIZE]
        if batch_ok:
            body = {"texts": [texts[index] for index in group], "direction": "en2zh"}
            result = _post(base_url, token, "/v1/translate/batch", body)
            if result.get("ok"):
                items = result.get("results") or []
                for offset, index in enumerate(group):
                    item = items[offset] if offset < len(items) else {}
                    value = str(item.get("zh") or "")
                    api_calls += _as_int(item.get("api_calls"), 0)
                    if value:
                        zh[index] = value
                        cache[_cache_key(texts[index])] = value
                    elif item.get("error"):
                        errors.append(str(item["error"]))
                position += len(group)
                continue
            if _missing_scope(result, "batch"):
                batch_ok = False                       # 只有 translate 权限也能跑
            else:
                kind, message = _classify(result)
                if kind == "fatal":
                    fatal = message
                else:
                    note = message
                break
        for index in group:
            if time.time() > deadline:
                note = "时间预算用完了，剩下几段没翻（下次会命中缓存）。"
                break
            body = {"text": texts[index], "direction": "en2zh"}
            result = _post(base_url, token, "/v1/translate", body)
            if not result.get("ok"):
                if _missing_scope(result, "translate"):
                    fatal = _friendly_permission(result)
                    break
                kind, message = _classify(result)
                if kind == "fatal":
                    fatal = message
                    break
                note = message
                errors.append(str(result.get("error") or ""))
                continue
            value = str(result.get("zh") or "")
            api_calls += _as_int(result.get("api_calls"), 0)
            if value:
                zh[index] = value
                cache[_cache_key(texts[index])] = value
        if fatal or note:
            break
        position += len(group)
    return {"zh": zh, "api_calls": api_calls, "errors": errors, "note": note,
            "fatal": fatal}


def _missing_scope(result: dict, scope: str) -> bool:
    """平台的 403 正文用的是中文标签（「批量翻译（…）」「翻译（英→中 / 中→英）」），
    所以两种写法都要认。顺序也有讲究：「批量翻译」里含"翻译"两个字，
    先判 batch，免得把"没有 batch 权限"误报成"没有 translate 权限"。"""
    text = str(result.get("error") or "")
    if "权限" not in text:
        return False
    if scope == "batch":
        return "batch" in text or "批量" in text
    if scope == "translate":
        return "批量" not in text and ("translate" in text or "翻译" in text)
    return scope in text


def _friendly_permission(result: dict) -> str:
    text = str(result.get("error") or "")
    return ("这个插件没有翻译权限（平台回的：%s）。请到 设置 → 平台 / 插件，"
            "点本插件的「权限」，勾上「翻译 translate」（想一次翻一批再勾上 "
            "batch），保存后重试。" % (text or "403 forbidden"))


def _classify(result: dict) -> tuple:
    """把平台错误分两类：fatal = 这次整件事做不了；stop = 停在这儿，残稿也能交。"""
    text = str(result.get("error") or "")
    code = str(result.get("code") or "")
    if code == "quota_exceeded" or "配额" in text:
        return "stop", ("今天的翻译配额用完了，剩下的段落先留着没翻"
                        "（下次跑会命中缓存，不会白花）。平台原话：%s" % text)
    if code == "unauthorized" or "令牌" in text:
        return "fatal", ("平台不认这个插件的令牌（401）。请在 设置 → 平台 / 插件 里"
                         "重置它的令牌，然后重试。")
    if "停用" in text:
        return "fatal", ("这个插件被停用了（%s）。请在 设置 → 平台 / 插件 里"
                         "重新勾上它。" % text)
    if code == "offline" or "连不上平台" in text:
        return "stop", text
    return "stop", "调用翻译平台失败：%s" % (text or "未知错误")


def _post(base_url: str, token: str, path: str, body: dict) -> dict:
    url = base_url.rstrip("/") + path
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "Authorization": "Bearer " + token}
    request = urllib.request.Request(url, data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8"))
        except Exception:                             # noqa: BLE001
            return {"ok": False, "code": "http_%s" % exc.code,
                    "error": "平台返回 HTTP %s" % exc.code}
    except urllib.error.URLError as exc:
        return {"ok": False, "code": "offline",
                "error": "连不上平台（%s）：%s" % (base_url, getattr(exc, "reason", exc))}
    except Exception as exc:                          # noqa: BLE001
        return {"ok": False, "code": "error", "error": "请求出错：%s" % exc}


def _get(base_url: str, token: str, path: str) -> dict:
    headers = {"Accept": "application/json", "Authorization": "Bearer " + token}
    request = urllib.request.Request(base_url.rstrip("/") + path, method="GET",
                                     headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8"))
        except Exception:                             # noqa: BLE001
            return {"ok": False, "code": "http_%s" % exc.code, "error": "HTTP %s" % exc.code}
    except Exception as exc:                          # noqa: BLE001
        return {"ok": False, "code": "error", "error": str(exc)}


def _cache_key(text: str) -> str:
    return hashlib.sha1(("en2zh|" + text).encode("utf-8")).hexdigest()


def _load_translation_cache() -> dict:
    path = CACHE_DIR / "translate.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(key): str(value) for key, value in data.items() if value}
    except Exception:                                 # noqa: BLE001
        pass
    return {}


def _save_translation_cache(cache: dict) -> None:
    path = CACHE_DIR / "translate.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        keys = list(cache)
        if len(keys) > 4000:                          # 别让缓存文件无限长
            cache = {key: cache[key] for key in keys[-4000:]}
        path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except Exception:                                 # noqa: BLE001
        pass


# ------------------------------------------------------------------ 成文
def _compose_markdown(docs: list, items: list, version_label: str, version_notes: list,
                      keyword_rows: list, glossary_rows: list, warnings: list,
                      api_calls: int, generated_at: str) -> str:
    lines = []
    lines.append("# DDO 构卡资料汇编 · %s" % version_label)
    lines.append("")
    lines.append("> 由 **DDO 翻译助手**的插件「DDO 论坛构卡汇编」生成　%s" % generated_at)
    lines.append("> 来源清单 %d 条 · 正文段落 %d 段 · 真实接口调用 %d 次"
                 "（其余是本地命中，没花钱）" % (len(docs), len(items), api_calls))
    if version_notes:
        lines.append("> 版本线索：%s" % "；".join(version_notes[:3]))
    if warnings:
        lines.append(">")
        lines.append("> **%d 条提示**：" % len(warnings))
        for item in warnings[:8]:
            lines.append("> * %s" % item)
    lines.append("")
    lines.append("## 一、版本与来源")
    lines.append("")
    lines.append("| # | 板块 | 标题 | 作者 | 段落 | 状态 |")
    lines.append("|---|------|------|------|------|------|")
    for index, doc in enumerate(docs, 1):
        title = (doc["title"] or doc["url"]).replace("|", "／")
        section = (doc["section"] or doc["label"] or "—").replace("|", "／")
        state = "抓取失败：%s" % doc["error"] if doc["error"] else (
            "板块目录" if doc["kind"] == "section" else "已收录")
        if doc["cached"] and not doc["error"]:
            state += "（用缓存）"
        lines.append("| %d | %s | %s | %s | %d | %s |"
                     % (index, section[:26], title[:60], (doc["author"] or "—")[:18],
                        len(doc["paragraphs"]), state[:40]))
    lines.append("")
    if keyword_rows:
        lines.append("## 二、机制要点（关键词命中）")
        lines.append("")
        lines.append("| 关键词 | 中文 | 命中 | 出现在 |")
        lines.append("|--------|------|------|--------|")
        for row in keyword_rows[:40]:
            lines.append("| %s | %s | %d | %s |"
                         % (row["term"], row["zh"], row["count"],
                            "、".join(row["sources"]) or "—"))
        lines.append("")

    lines.append("## 三、构卡正文（中英对照）")
    lines.append("")
    current_doc = None
    counter = 0
    for item in items:
        doc = docs[item["doc"]]
        if current_doc != item["doc"]:
            current_doc = item["doc"]
            lines.append("### %s" % (doc["title"] or doc["url"]))
            lines.append("")
            meta = []
            if doc["section"]:
                meta.append("板块：%s" % doc["section"])
            if doc["author"]:
                meta.append("楼主：%s" % doc["author"])
            if doc["versions"]:
                meta.append("版本线索：%s" % "、".join(doc["versions"][:3]))
            meta.append("[原帖](%s)" % doc["url"])
            lines.append("*%s*" % "　".join(meta))
            lines.append("")
        counter += 1
        lines.append("**%d.**　%s"
                     % (counter, item["zh"] or "（这句没翻成）：%s" % item["text"]))
        lines.append("")
        lines.append("<sub>EN：%s</sub>" % _squash(item["text"]))
        lines.append("")

    if glossary_rows:
        lines.append("## 四、术语附录")
        lines.append("")
        lines.append("| 英文 | 中文 | 平台术语表 |")
        lines.append("|------|------|-----------|")
        for row in glossary_rows[:40]:
            lines.append("| %s | %s | %s |"
                         % (row["term"], row["zh"], row.get("official") or "—"))
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("原文版权归 DDO 官方论坛原作者与 Standing Stone Games 所有；"
                 "译文由本机翻译平台生成，仅供个人阅读参考。"
                 "术语以游戏内实际译名为准，发现更好的译法可以在界面里 F10 纠错。")
    lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------------ 小工具
def _as_int(value, default: int) -> int:
    try:
        number = int(value)
    except Exception:                                 # noqa: BLE001
        return default
    return number if number > 0 else default


def _read_request() -> dict:
    try:
        raw = sys.stdin.read()
    except Exception:                                 # noqa: BLE001
        raw = ""
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except Exception:                                 # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def _reply(ok: bool, message: str, text: str = "", output=None, files=None) -> int:
    print(json.dumps({"ok": bool(ok), "message": message, "text": text,
                      "output": output, "files": files or []},
                     ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
