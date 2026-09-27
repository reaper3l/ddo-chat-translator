"""去重：同一句话不要重复送翻译。

策略（比旧版的"按位置比字符相似度"可靠，而且可以单测）：

1. 指纹完全相同且在 TTL 内 → 重复。
2. 指纹只多出一截、且刚刚出现过（3 秒内）→ 重复。
   这解决 OCR 把长句拆行、其中一行滚出屏幕后又被当成新消息的问题。
3. 同长度、只差 1 个字符、长度 >= 8 → 重复（OCR 单字抖动）。
"""
from __future__ import annotations

import time
from collections import deque
from typing import Deque, Dict, Optional


class Deduper:
    def __init__(self, ttl_seconds: float = 90.0, max_items: int = 800,
                 prefix_window: float = 3.0) -> None:
        self.ttl = float(ttl_seconds)
        self.max_items = int(max_items)
        self.prefix_window = float(prefix_window)
        self._seen: Dict[str, float] = {}
        self._order: Deque[str] = deque()

    def _purge(self, now: float) -> None:
        while self._order:
            fp = self._order[0]
            ts = self._seen.get(fp)
            if ts is None or now - ts > self.ttl:
                self._order.popleft()
                self._seen.pop(fp, None)
            else:
                break
        while len(self._order) > self.max_items:
            fp = self._order.popleft()
            self._seen.pop(fp, None)

    def check(self, fp: str, now: Optional[float] = None) -> bool:
        """返回 True 表示这条是重复的，不用再翻译。"""
        if not fp:
            return False
        now = time.time() if now is None else now
        self._purge(now)

        if fp in self._seen:
            self._seen[fp] = now
            return True

        for other, ts in self._seen.items():
            if now - ts > self.prefix_window:
                continue
            if len(other) >= 8 and (fp.startswith(other) or other.startswith(fp)):
                self._remember(fp, now)
                return True
            if len(other) == len(fp) and len(fp) >= 8 and self._hamming_within_one(fp, other):
                self._remember(fp, now)
                return True

        self._remember(fp, now)
        return False

    def _remember(self, fp: str, now: float) -> None:
        self._seen[fp] = now
        self._order.append(fp)

    @staticmethod
    def _hamming_within_one(a: str, b: str) -> bool:
        diff = 0
        for left, right in zip(a, b):
            if left != right:
                diff += 1
                if diff > 1:
                    return False
        return True

    def clear(self) -> None:
        self._seen.clear()
        self._order.clear()

    def __len__(self) -> int:
        return len(self._seen)
