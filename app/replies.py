"""把模型回的那几行"中文 | English"解析成 (中文, 英文) 列表。

模型不一定每次都严格照格式来（会加编号、加 Markdown、用别的分隔符、甚至回 JSON），
所以这里宽容一点：常见的几种写法都认，实在认不出就跳过那一行。
纯函数，方便单测。
"""
from __future__ import annotations

import json
import re
from typing import List, Tuple

# 分隔符：| ｜ / => → ：:　—— 按"最像分隔符"的顺序试
_SEPARATORS = ("|", "｜", "=>", "→", "—", " - ", "：", ":")
_NUMBER_RE = re.compile(r"^\s*(?:\d+[\.、)）]\s*|[-*•]\s*)")
_FENCE_RE = re.compile(r"^\s*```[a-zA-Z]*\s*|\s*```\s*$")


def _strip_decoration(line: str) -> str:
    text = _FENCE_RE.sub("", line.strip())
    text = _NUMBER_RE.sub("", text)
    text = text.strip().strip("*_` ")
    # 去掉"中文：""英文："这类小标题
    text = re.sub(r"^(?:中文|英文|中|英)\s*[:：]\s*", "", text)
    return text.strip()


def _split_pair(line: str) -> Tuple[str, str] | None:
    for sep in _SEPARATORS:
        if sep in line:
            left, _, right = line.partition(sep)
            left, right = _strip_decoration(left), _strip_decoration(right)
            if left and right:
                return left, right
    return None


def parse_suggestions(text: str, limit: int = 6) -> List[Tuple[str, str]]:
    """解析模型输出，返回 [(中文, 英文), ...]。"""
    raw = (text or "").strip()
    if not raw:
        return []

    # 1) 有的模型会回 JSON 数组
    if raw.lstrip().startswith("["):
        try:
            data = json.loads(raw)
        except Exception:
            data = None
        if isinstance(data, list):
            pairs: List[Tuple[str, str]] = []
            for item in data:
                if isinstance(item, dict):
                    zh = str(item.get("zh") or item.get("中文") or "").strip()
                    en = str(item.get("en") or item.get("英文") or "").strip()
                    if zh and en:
                        pairs.append((zh, en))
            if pairs:
                return pairs[:limit]

    # 2) 常规：一行一条 "中文 | English"
    pairs = []
    for line in raw.splitlines():
        line = _FENCE_RE.sub("", line.strip())
        if not line or line.startswith("#"):
            continue
        pair = _split_pair(line)
        if pair is None:
            continue
        zh, en = pair
        if not zh or not en:
            continue
        # 英文那半必须是英文（避免把"中文 | 中文"这种也收进来）
        if not re.search(r"[A-Za-z]", en):
            continue
        if (zh, en) in pairs:
            continue
        pairs.append((zh, en))
        if len(pairs) >= limit:
            break
    return pairs
