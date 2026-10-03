"""公共词典上线前的「语料回放」自检。

干什么：拿一批真实闲聊句，把「加词之前 / 加词之后」的术语表各跑一遍，逐句比匹配
结果，看新词会不会在常见句子里到处乱改。

为什么要有它：公共词典是自动生成、自动下发的，最怕的不是少一个词，而是多了一个
到处乱改的词。举个真会出事的例子 —— 有人贡献了一条短语 on my way（本意是游戏里
「路上 / 马上到」），听起来很合理；可它会在成百上千句常见闲聊里被抽出来单独翻译，
整句就散了。光看那一条词根本看不出来，只有把它放进真实语料里跑一遍才看得见。

拦什么（默认拦，--replay-force 可强行放行）：
  * 过度泛化：某个新词命中 >= min_hits 行，且占语料比例 >= max_ratio。
    只看比例、不设「命中多少行」的绝对线 —— 语料就几百句，绝对值没意义
    （实测：内置表里的 pop 命中 5/226 句 = 2%，那是正常用法，不该拦）。

报什么（不拦）：
  * 哪些句子的匹配结果变了（最多列几条，扫一眼就知道改动是不是合理）；
  * 新词一次都没在语料里出现（多数小众缩写本来就如此）；
  * 客户端根本不会采用的词（单个常用词、太短、译文里没有中文）—— 标出来让你知道
    它白写了，不会生效，所以也不算"改坏"。

这里只比术语匹配，不调用翻译接口：离线、快、结果可复现。
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import paths, textutil
from .glossary import NEVER_PROTECT, Glossary

DEFAULT_MIN_HITS = 3
DEFAULT_MAX_RATIO = 0.05
EXAMPLE_LIMIT = 10
MAX_LINES_READ = 5000


@dataclass
class ReplayReport:
    """回放结果。ok 只由 over_reach 决定 —— 其余都是给人看的。"""

    lines: int = 0
    new_terms: int = 0
    hits: Dict[str, int] = field(default_factory=dict)
    changed: List[Tuple[str, str, str]] = field(default_factory=list)
    over_reach: List[Tuple[str, int, float]] = field(default_factory=list)
    no_hits: List[str] = field(default_factory=list)
    ignored: List[str] = field(default_factory=list)
    min_hits: int = DEFAULT_MIN_HITS
    max_ratio: float = DEFAULT_MAX_RATIO

    @property
    def ok(self) -> bool:
        return not self.over_reach


def client_usable(term: str, translation: str) -> bool:
    """客户端到底会不会把这个词当保护词用 —— 和 app/glossary.py 里的门槛保持一致。

    单个常用词（NEVER_PROTECT 里那批）、1~2 个字母的缩写、译文里没有中文的条目，
    客户端一律跳过：它们不会生效，也就谈不上"改坏"。
    """
    if not term or not translation:
        return False
    if not textutil.has_cjk(translation):
        return False
    key = Glossary._norm_key(term)
    if not key:
        return False
    if " " not in key and (key in NEVER_PROTECT or len(key) < 3):
        return False
    return True


def read_corpus(path: Path) -> List[str]:
    """读语料：纯文本一行一句（# 开头是注释），或者 JSON 数组 / {"lines": [...]}。"""
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("lines") or data.get("corpus") or []
        if isinstance(data, list):
            return [str(item).strip() for item in data if str(item).strip()]
        return []
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    return lines


def memory_lines(path: Optional[Path] = None) -> List[str]:
    """从本机记忆库里捞真实句子：看过的原文 + 观察到的样句 + 人工纠正过的句子。

    只在本机跑、读完就扔 —— 这些句子不会被写进任何要发出去的文件。
    """
    target = Path(path) if path else paths.MEMORY_PATH
    data = paths.read_json(target, {}) or {}
    if not isinstance(data, dict):
        return []
    lines: List[str] = []
    for item in (data.get("phrases") or {}).values():
        source = item.get("source") if isinstance(item, dict) else None
        if source:
            lines.append(str(source).strip())
    for item in (data.get("candidates") or {}).values():
        samples = item.get("samples") if isinstance(item, dict) else None
        for sample in (samples or []):
            if sample:
                lines.append(str(sample).strip())
    for item in (data.get("corrections") or []):
        source = item.get("source") if isinstance(item, dict) else None
        if source:
            lines.append(str(source).strip())
    return lines


def collect_lines(corpus_paths: Iterable[Path] = (), use_memory: bool = True,
                  memory_path: Optional[Path] = None) -> List[str]:
    """把语料拼起来：给定的文件 + 本机记忆库，去重并保持原顺序。"""
    lines: List[str] = []
    for path in corpus_paths:
        try:
            lines.extend(read_corpus(Path(path)))
        except Exception as exc:                   # noqa: BLE001
            print("读语料失败（跳过）：%s（%s）" % (path, exc))
    if use_memory:
        try:
            lines.extend(memory_lines(memory_path))
        except Exception as exc:                   # noqa: BLE001
            print("读本机记忆库失败（跳过）：%s" % exc)

    seen = set()
    unique: List[str] = []
    for line in lines:
        line = (line or "").strip()
        if not line or line in seen:
            continue
        seen.add(line)
        unique.append(line)
        if len(unique) >= MAX_LINES_READ:
            break
    return unique


def compare(before_terms: Dict[str, str], new_terms: Dict[str, str],
            lines: Sequence[str], min_hits: int = DEFAULT_MIN_HITS,
            max_ratio: float = DEFAULT_MAX_RATIO) -> ReplayReport:
    """比对「加这些新词之前 / 之后」，每条语料的术语匹配结果。"""
    before_terms = {str(k): str(v) for k, v in (before_terms or {}).items() if k and v}
    new_terms = {str(k): str(v) for k, v in (new_terms or {}).items() if k and v}
    usable = {k: v for k, v in new_terms.items() if client_usable(k, v)}
    ignored = sorted(k for k in new_terms if k not in usable)

    after_terms = dict(before_terms)
    after_terms.update(usable)

    corpus = [line for line in (lines or []) if line and line.strip()]
    report = ReplayReport(lines=len(corpus), new_terms=len(usable), ignored=ignored,
                          min_hits=min_hits, max_ratio=max_ratio)
    report.no_hits = sorted(usable)
    if not corpus or not usable:
        return report

    before = Glossary(before_terms)
    after = Glossary(after_terms)

    # 用译文认领「多出来的占位符」：新词命中了哪一行就记到它头上。
    # 两个新词译文完全一样时会认到先出现的那个 —— 只影响归因，不影响拦不拦。
    owner: Dict[str, str] = {}
    for term, translation in usable.items():
        owner.setdefault(translation, term)
    report.hits = dict.fromkeys(usable, 0)

    for line in corpus:
        old_text, old_mapping, _ = before.protect(line)
        new_text, new_mapping, _ = after.protect(line)
        if old_text == new_text:
            continue
        report.changed.append((line, old_text, new_text))
        old_count = Counter(old_mapping.values())
        for translation, count in Counter(new_mapping.values()).items():
            term = owner.get(translation)
            if term is None or count <= old_count.get(translation, 0):
                continue
            report.hits[term] = report.hits.get(term, 0) + 1    # 一行最多记一次

    for term, count in report.hits.items():
        ratio = count / float(len(corpus))
        if count >= min_hits and ratio >= max_ratio:
            report.over_reach.append((term, count, ratio))
    report.over_reach.sort(key=lambda item: (-item[1], item[0]))
    report.no_hits = sorted(term for term, count in report.hits.items() if count == 0)
    return report


def format_report(report: ReplayReport, examples: int = EXAMPLE_LIMIT) -> str:
    """把结果排成人能扫一眼的几行。"""
    out = ["语料回放：%d 句语料，%d 条会生效的新词（另有 %d 条客户端不会采用）"
           % (report.lines, report.new_terms, len(report.ignored))]
    if not report.new_terms:
        if report.ignored:
            out.append("  会生效的新词是 0 条 —— 那些词客户端本来就跳过，不会改变任何句子。")
        else:
            out.append("  没有新增或改动的词，不用回放。")
    elif not report.lines:
        out.append("  语料是空的 —— 回放没起作用（检查 --corpus / 本机记忆库）。")
    else:
        listed = ["%s×%d" % (term, report.hits[term])
                  for term, count in sorted(report.hits.items(),
                                            key=lambda kv: (-kv[1], kv[0]))
                  if count][:8]
        out.append("  命中情况：" + ("、".join(listed) if listed
                                    else "（新词都没在语料里出现）"))
        if report.over_reach:
            out.append("  [X] 过度泛化（会大范围改词，已拦下）：")
            for term, count, ratio in report.over_reach:
                out.append("      %s —— 命中 %d/%d 句（%.0f%%）"
                           % (term, count, report.lines, ratio * 100.0))
        if report.changed:
            out.append("  匹配结果会变的句子（%d 句，列前 %d 条）："
                       % (len(report.changed), min(examples, len(report.changed))))
            for line, old_text, new_text in report.changed[:examples]:
                out.append("      原句：%s" % line)
                out.append("      之前：%s" % old_text)
                out.append("      之后：%s" % new_text)
        if report.no_hits:
            out.append("  语料里没出现（不拦，常见于小众缩写）：%s"
                       % "、".join(report.no_hits[:10]))
    if report.ignored:
        out.append("  客户端不会采用（单个常用词 / 太短 / 译文没中文，白写）：%s"
                   % "、".join(report.ignored[:10]))
    out.append("  结论：%s" % ("通过" if report.ok else "不通过"))
    return "\n".join(out)
