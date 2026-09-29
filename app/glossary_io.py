"""术语表（词典）的导出 / 导入。

两种格式，都是给人看、也方便互相分享的：

* **JSON**（默认，`.json`）：带格式头和导出时间，字段是 `terms`；
* **CSV**（`.csv` / `.txt`）：两列 `英文术语,中文`，带一行表头 —— 想用 Excel 批量编辑、
  或者从别人那里收一份表格，都方便。

导入时只写入"和现在不一样"的条目：导入别人整份术语表不会把你机器上的内置词条
一条条复制进用户数据（那会让 memory.json 白白变胖）。
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Dict, List, Tuple

FORMAT = "ddo-glossary-1"
HEADER = ("英文术语", "中文")


def dump_terms(terms: Dict[str, str], fmt: str = "json",
               now: datetime | None = None) -> str:
    """把 {术语: 中文} 导出成文本（fmt: json / csv）。"""
    items = sorted(((str(k).strip(), str(v).strip())
                    for k, v in (terms or {}).items()
                    if str(k).strip() and str(v).strip()),
                   key=lambda pair: pair[0].lower())
    if fmt.lower().startswith("csv"):
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(HEADER)
        writer.writerows(items)
        return buffer.getvalue()
    payload = {
        "format": FORMAT,
        "exported_at": (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(items),
        "terms": {term: zh for term, zh in items},
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _looks_like_header(row: List[str]) -> bool:
    first = (row[0] if row else "").strip().lower()
    return first in ("term", "term_en", "english", "英文", "英文术语", "术语")


def load_terms(text: str, filename: str = "") -> Dict[str, str]:
    """把导出文件读回 {术语: 中文}；认不出来就抛 ValueError（界面会提示）。"""
    body = (text or "").strip()
    if not body:
        raise ValueError("文件是空的")

    if body.startswith("{") or body.startswith("["):
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ValueError("JSON 格式不对：%s" % exc) from exc
        if isinstance(data, dict) and isinstance(data.get("terms"), dict):
            data = data["terms"]
        if not isinstance(data, dict):
            raise ValueError("认不出这个 JSON：应该有 terms 字段，或者本身就是 {术语: 中文}")
        result: Dict[str, str] = {}
        for term, zh in data.items():
            if isinstance(zh, str) and str(term).strip() and zh.strip():
                result[str(term).strip()] = zh.strip()
            elif isinstance(zh, dict) and str(zh.get("zh", "")).strip():
                # 也接受 memory.json 里那种 {"text":..., "zh":...} 的结构
                key = str(zh.get("text") or term).strip()
                if key:
                    result[key] = str(zh["zh"]).strip()
        if not result:
            raise ValueError("文件里没有任何词条")
        return result

    # 其它的当 CSV/TSV：逗号、制表符、分号都能吃
    sample = body.splitlines()[0]
    delimiter = "\t" if "\t" in sample else (";" if ";" in sample and
                                            "," not in sample else ",")
    rows = list(csv.reader(io.StringIO(body), delimiter=delimiter))
    result = {}
    for index, row in enumerate(rows):
        if len(row) < 2:
            continue
        term, zh = row[0].strip(), row[1].strip()
        if index == 0 and _looks_like_header(row):
            continue
        if term and zh:
            result[term] = zh
    if not result:
        raise ValueError("没读出词条：CSV 需要两列（英文术语,中文）")
    return result


def plan_import(current: Dict[str, str],
                incoming: Dict[str, str]) -> Tuple[Dict[str, str], int]:
    """决定哪些条目要写进"我的词条"。

    返回 (要写入的, 和现有译法相同的条数)。现有译法取自**当前生效的术语表**
    （内置 + 我的），所以导入一份完整导出文件时，只有真正新增/改动的才会写。
    """
    lowered = {str(k).strip().lower(): str(v).strip() for k, v in (current or {}).items()}
    to_write: Dict[str, str] = {}
    unchanged = 0
    for term, zh in (incoming or {}).items():
        term = str(term).strip()
        zh = str(zh).strip()
        if not term or not zh:
            continue
        if lowered.get(term.lower()) == zh:
            unchanged += 1
            continue
        to_write[term] = zh
    return to_write, unchanged
