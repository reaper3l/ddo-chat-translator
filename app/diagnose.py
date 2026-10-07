"""Bug 反馈包：把"复现问题需要的东西"自动收集起来，打包成一个 zip。

用户只管描述问题，剩下的（版本、环境、配置、日志、最近的翻译记录、最近一次识别到的
原文行、可选的一张画面截图）由程序自己收 —— 提交到 issue 就能直接定位问题。

**安全第一**：
* 配置里的 API Key / Token / 密码这类字段一律替换成 `***已隐藏***`；
* 收集到的每一段文本还会再扫一遍，把"真实密钥字符串"替换掉（防止它出现在日志/缓存里）；
* 用户目录路径换成 `%USERPROFILE%`，不要把用户名带出去；
* 画面截图**由用户自己决定**要不要带（对话框里可取消勾选）。

这里只做"收集 + 打包"，界面在 `app/ui/report.py`。
"""
from __future__ import annotations

import json
import platform
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import AUTHOR, HOMEPAGE, __version__, paths

SECRET_MARK = "***已隐藏***"
# 名字里带这些词的配置项都算敏感（deepseek_key / access_token / password…）
SECRET_NAME_RE = re.compile(r"(key|token|secret|password|passwd|credential)", re.I)
LOG_TAIL_LINES = 200
TRANSLATION_LINES = 200


def is_secret_name(name: str) -> bool:
    return bool(SECRET_NAME_RE.search(str(name or "")))


def secret_values(config: Optional[Dict[str, Any]]) -> List[str]:
    """配置里那些**非空的**敏感值 —— 用来在其它文本里把它们抹掉。"""
    found = []
    for name, value in (config or {}).items():
        if not is_secret_name(name):
            continue
        text = str(value or "").strip()
        if len(text) >= 6:                 # 太短的（比如 "1"）抹了反而会误伤正文
            found.append(text)
    return found


def redact_config(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """配置字典的脱敏副本（递归，敏感项换掉）。"""
    result: Dict[str, Any] = {}
    for name, value in (config or {}).items():
        if isinstance(value, dict):
            result[name] = redact_config(value)
        elif is_secret_name(name) and str(value or "").strip():
            result[name] = SECRET_MARK
        else:
            result[name] = value
    return result


def scrub(text: str, secrets: Iterable[str] = (), home: Optional[str] = None) -> str:
    """把文本里的密钥 / 用户名抹掉。"""
    body = str(text or "")
    for secret in secrets or ():
        if secret:
            body = body.replace(secret, SECRET_MARK)
    if home:
        body = body.replace(str(home), "%USERPROFILE%")
        body = body.replace(str(home).replace("\\", "/"), "%USERPROFILE%")
    return body


def _tail(path: Path, lines: int, secrets=(), home: Optional[str] = None) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return "（读不到 %s：%s）" % (path.name, exc)
    rows = text.splitlines()[-lines:]
    return scrub("\n".join(rows), secrets, home)


def _translation_lines(records, secrets=(), home: Optional[str] = None) -> str:
    """最近显示过的消息（原文 → 译文），这是排查"翻译/识别不对"最有用的一手材料。"""
    rows: List[str] = []
    for record in (records or [])[-TRANSLATION_LINES:]:
        kind = record.get("kind", "chat")
        source = str(record.get("source", "")).replace("\n", " ")
        translated = str(record.get("translated", "")).replace("\n", " ")
        if kind == "system":
            rows.append("[系统] %s" % source)
        else:
            rows.append("原文：%s\n译文：%s" % (source, translated))
    return scrub("\n\n".join(rows) or "（这次运行还没有翻译记录）", secrets, home)


def _environment(app_dir: Optional[Path] = None) -> str:
    rows = [
        "程序版本：v%s" % __version__,
        "作者 / 主页：%s / %s" % (AUTHOR, HOMEPAGE),
        "运行方式：%s" % ("打包版 exe" if getattr(sys, "frozen", False) else "源码 python"),
        "Python：%s" % sys.version.split()[0],
        "操作系统：%s %s (%s)" % (platform.system(), platform.release(), platform.version()),
        "程序目录：%s" % (app_dir or paths.APP_DIR),
    ]
    return "\n".join(rows)


def build_report(problem: str = "", config: Optional[dict] = None,
                 records=None, last_lines=None, memory=None,
                 pipeline_stats=None,
                 app_dir: Optional[Path] = None,
                 log_path: Optional[Path] = None) -> str:
    """生成报告文本（已脱敏）。"""
    home = str(Path.home())
    secrets = secret_values(config)
    sections: List[Tuple[str, str]] = []
    if problem and problem.strip():
        sections.append(("你填写的问题描述", scrub(problem.strip(), secrets, home)))
    sections.append(("运行环境", _environment(app_dir)))
    sections.append(("配置（API Key 等已隐藏）",
                     json.dumps(redact_config(config), ensure_ascii=False, indent=2)))
    sections.append(("最近翻译记录（原文 → 译文）",
                     _translation_lines(records, secrets, home)))
    lines_text = "\n".join(str(line) for line in (last_lines or []))
    sections.append(("最近一次识别到的原始行（OCR 结果，排查识别问题用）",
                     scrub(lines_text or "（还没有识别过）", secrets, home)))
    if pipeline_stats:
        # 监听时的实际开销分布：整帧 vs 只认变化的那几行、跳过了多少帧。
        # 玩家报"游戏卡"时，看这几个数就知道程序有没有在做多余的事。
        try:
            stats = dict(pipeline_stats)
            sections.append(("监听性能计数", json.dumps(
                {key: stats.get(key, 0) for key in
                 ("frames", "skipped_frame", "band_ocr", "full_ocr",
                  "ocr_lines", "api_calls", "batched", "filtered")},
                ensure_ascii=False)))
        except Exception as exc:
            sections.append(("监听性能计数", "读取失败：%s" % exc))
    if memory is not None:
        try:
            stats = dict(getattr(memory, "stats", {}) or {})
            terms = memory.term_list()
            sections.append(("学习库摘要", "共有词条 %d 条，统计：%s"
                             % (len(terms), json.dumps(stats, ensure_ascii=False))))
        except Exception as exc:
            sections.append(("学习库摘要", "读取失败：%s" % exc))
    sections.append(("日志尾部（最近 %d 行）" % LOG_TAIL_LINES,
                     _tail(log_path or paths.LOG_PATH, LOG_TAIL_LINES, secrets, home)))

    head = ("DDO 翻译助手 · Bug 反馈报告\n"
            "生成时间：%s\n" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    body = []
    for title, content in sections:
        body.append("=" * 64)
        body.append("【%s】" % title)
        body.append("=" * 64)
        body.append(content)
        body.append("")
    return head + "\n" + "\n".join(body)


def write_bundle(target_dir: Path, report: str, extras: Optional[Dict[str, Any]] = None,
                 stamp: Optional[str] = None) -> Path:
    """把报告和附加文件打包成 zip，返回压缩包路径。

    `extras` 的值可以是字符串（当文本文件写进去）或字节（当二进制写进去）。
    """
    stamp = stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    folder = Path(target_dir)
    folder.mkdir(parents=True, exist_ok=True)
    bundle = folder / ("bug报告_v%s_%s.zip" % (__version__, stamp))
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("报告.txt", report)
        for name, content in (extras or {}).items():
            if isinstance(content, (bytes, bytearray)):
                archive.writestr(name, bytes(content))
            else:
                archive.writestr(name, str(content))
    return bundle


def issue_url() -> str:
    """新建 issue 的地址（带着标题，用户点开就能写）。"""
    return "%s/issues/new?issue[title]=%s" % (HOMEPAGE.rstrip("/"),
                                              "%E5%8F%8D%E9%A6%88%EF%BC%9A")
