"""DDO 网站攻略汉化 —— 示例插件。

它演示了插件该怎么做：

1. 从 stdin 读宿主发来的 JSON（里面有平台地址和本插件的令牌）；
2. 自己搞定"拿文本"这件事（剪贴板 / 网址 / input.txt）；
3. 调平台把英文翻成中文（**一次批量发出去**，比逐条省钱）；
4. 把中英对照的 Markdown 写到磁盘，再用 JSON 回话。

用到的只有标准库，拷到任何装了 Python 3 的机器上都能跑。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUTPUT_DIR = HERE / "output"
MAX_PARAGRAPHS = 400
CHUNK = 20                      # 一次 POST 多少段（平台内部还会再按设置合并）

# 行内不该单独成段的短行（标题、列表项还是保留，这里只丢空行和装饰线）
_SKIP = re.compile(r"^\s*([-=*_]{3,})?\s*$")


# ------------------------------------------------------------------ 入口
def main(argv) -> int:
    action = argv[1] if len(argv) > 1 else ""
    request = _read_request()
    payload = request.get("payload") or {}
    platform = request.get("platform") or {}
    base_url = str(platform.get("base_url") or os.environ.get("DDO_PLATFORM_URL") or "")
    token = str(platform.get("token") or os.environ.get("DDO_PLATFORM_TOKEN") or "")

    try:
        source_text, origin = _gather_text(action, payload)
    except ValueError as exc:
        return _reply(False, str(exc))
    if not source_text.strip():
        return _reply(False, "没拿到要翻译的英文。"
                             "（剪贴板是空的？或者这次动作没有给文本）")

    paragraphs = _split(source_text)
    if not paragraphs:
        return _reply(False, "文本里没有可翻译的段落。")
    truncated = False
    if len(paragraphs) > MAX_PARAGRAPHS:
        paragraphs = paragraphs[:MAX_PARAGRAPHS]
        truncated = True

    if not base_url:
        return _reply(False, "宿主没有告诉插件平台地址（DDO_PLATFORM_URL 是空的）")

    translated, errors, api_calls = [], [], 0
    for start in range(0, len(paragraphs), CHUNK):
        group = paragraphs[start:start + CHUNK]
        result = _post(base_url, token, "/v1/translate/batch",
                       {"texts": group, "direction": "en2zh"})
        if not result.get("ok"):
            return _reply(False, "调用翻译平台失败：%s" % result.get("error"))
        for item in result.get("results") or []:
            translated.append(str(item.get("zh") or ""))
            api_calls += int(item.get("api_calls") or 0)
            if not item.get("ok") and item.get("error"):
                errors.append(str(item["error"]))

    markdown = _compose(origin, paragraphs, translated)
    # 输出位置默认在插件目录的 output\；宿主/调用方可以用 payload.output_dir 改（测试用得上）
    out_dir = Path(str(payload.get("output_dir") or OUTPUT_DIR))
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / ("攻略_%s.md" % time.strftime("%Y%m%d_%H%M%S"))
    try:
        out_path.write_text(markdown, encoding="utf-8")
    except Exception as exc:                      # noqa: BLE001
        return _reply(False, "译文写不出去：%s" % exc)

    message = "已翻译 %d 段，生成：%s" % (len(paragraphs), out_path)
    if truncated:
        message += "（文本太长，只翻了前 %d 段）" % MAX_PARAGRAPHS
    if errors:
        message += "　其中 %d 段出错" % len(errors)
    return _reply(True, message, text=markdown,
                  output={"paragraphs": len(paragraphs), "api_calls": api_calls,
                          "errors": errors[:5]},
                  files=[str(out_path)])


# ------------------------------------------------------------------ 取文本
def _read_request() -> dict:
    try:
        raw = sys.stdin.read()
    except Exception:                             # noqa: BLE001
        raw = ""
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except Exception:                             # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def _gather_text(action: str, payload: dict):
    if payload.get("text"):
        return str(payload["text"]), str(payload.get("origin") or "传入的文本")
    if action == "translate-url":
        url = str(payload.get("url") or _clipboard() or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            raise ValueError("请先把网址复制到剪贴板，或用 payload.url 传进来")
        return _fetch(url), url
    if action == "translate-file":
        path = Path(str(payload.get("path") or (HERE / "input.txt")))
        if not path.is_file():
            raise ValueError("找不到 %s（可以把它放在插件目录里）" % path)
        return path.read_text(encoding="utf-8", errors="replace"), str(path)
    text = _clipboard()
    if not text.strip():
        raise ValueError("剪贴板是空的。先 Ctrl+C 复制一段英文，再点这个动作。")
    return text, "剪贴板"


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
    except Exception:                             # noqa: BLE001
        return ""


def _fetch(url: str) -> str:
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("网址要以 http:// 或 https:// 开头")
    request = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (DDO-Translator-Plugin)"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
    except urllib.error.URLError as exc:
        raise ValueError("打不开这个网址：%s" % exc.reason)
    text = raw.decode("utf-8", errors="replace")
    return _strip_html(text) if "<" in text[:2000] else text


_TAG = re.compile(r"<[^>]+>")
_SCRIPT = re.compile(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>")


def _strip_html(html: str) -> str:
    """够用就行的 HTML → 纯文本（不引第三方库）。"""
    html = _SCRIPT.sub(" ", html)
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    html = re.sub(r"(?i)</(p|div|li|tr|h[1-6])>", "\n", html)
    text = _TAG.sub("", html)
    for entity, char in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                         ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")):
        text = text.replace(entity, char)
    return text


# ------------------------------------------------------------------ 翻译
def _post(base_url: str, token: str, path: str, body: dict) -> dict:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + path, data=data, method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8"))
        except Exception:                         # noqa: BLE001
            return {"ok": False, "error": "平台返回 HTTP %s" % exc.code}
    except urllib.error.URLError as exc:
        return {"ok": False, "error": "连不上平台（%s）：%s" % (base_url, exc.reason)}
    except Exception as exc:                      # noqa: BLE001
        return {"ok": False, "error": "请求出错：%s" % exc}


# ------------------------------------------------------------------ 排版
def _split(text: str) -> list:
    paragraphs = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        block = block.strip()
        if not block or _SKIP.match(block):
            continue
        # 太长的块按句子切开，免得一条就顶到平台的字数上限
        if len(block) > 1500:
            paragraphs.extend(_split_long(block))
        else:
            paragraphs.append(block)
    return paragraphs


def _split_long(block: str) -> list:
    pieces, current = [], ""
    for sentence in re.split(r"(?<=[.!?。！？])\s+", block):
        if len(current) + len(sentence) > 1200 and current:
            pieces.append(current.strip())
            current = sentence
        else:
            current = (current + " " + sentence).strip()
    if current.strip():
        pieces.append(current.strip())
    return pieces


def _compose(origin: str, paragraphs: list, translated: list) -> str:
    lines = ["# 攻略汉化（%s）" % origin, "",
             "> 由 DDO 翻译助手 · 示例插件生成　%s" % time.strftime("%Y-%m-%d %H:%M"), ""]
    for index, source in enumerate(paragraphs):
        zh = translated[index] if index < len(translated) else ""
        lines.append(zh or source)
        lines.append("")
        lines.append("<sub>%s</sub>" % source.replace("\n", " "))
        lines.append("")
    return "\n".join(lines)


def _reply(ok: bool, message: str, text: str = "", output=None, files=None) -> int:
    print(json.dumps({"ok": bool(ok), "message": message, "text": text,
                      "output": output, "files": files or []},
                     ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
