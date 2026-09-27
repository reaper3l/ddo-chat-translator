"""帧间差异分析：判断画面变了没有、变的是哪几行，从而"只识别变化的部分"。

全部是纯函数，方便单测。思路：
* 每次截图后把画面缩成 grid×grid 的灰度图（默认 48×48，约 2300 字节）；
* 按行统计"灰度差超过 tolerance 的像素数"，得到哪些行变了；
* 只把"变化的行"对应的像素带送去 OCR，其余部分沿用上一帧的识别结果。

聊天框最常见的场景是：旧的往上滚、新的出现在最下面 —— 那样只需要识别底部一小条，
OCR 时间能降到原来的 1/4~1/5。
"""
from __future__ import annotations
from typing import List, Sequence, Tuple

DEFAULT_GRID = 48


def row_diff_counts(current: bytes, previous: bytes, grid: int = DEFAULT_GRID,
                    tolerance: int = 6) -> List[int]:
    """按行统计"灰度差超过 tolerance 的像素个数"，返回长度为 grid 的列表。"""
    if not current or not previous or len(current) != grid * grid \
            or len(previous) != grid * grid:
        return [grid] * grid            # 拿不到有效指纹 → 当作整屏都变了
    counts: List[int] = []
    for row in range(grid):
        base = row * grid
        line = current[base:base + grid]
        old = previous[base:base + grid]
        counts.append(sum(1 for a, b in zip(line, old) if abs(a - b) > tolerance))
    return counts


def analyse_frame(current: bytes, previous: bytes, grid: int = DEFAULT_GRID,
                  tolerance: int = 6, threshold: int = 2) -> Tuple[bool, int, int]:
    """返回 (是否变化, 起始行, 结束行)。行号是 0..grid-1 的闭区间；没变化时为 (-1, -1)。"""
    counts = row_diff_counts(current, previous, grid, tolerance)
    rows = [index for index, count in enumerate(counts) if count > threshold]
    if not rows:
        return False, -1, -1
    return True, rows[0], rows[-1]


def band_pixels(band_rows: Tuple[int, int], height: int, grid: int = DEFAULT_GRID,
                overlap_rows: int = 2) -> Tuple[int, int]:
    """把"变化行范围"换算成像素带 [y_start, y_end)，上下各留一点余量避免切断整行。"""
    first, last = band_rows
    if first < 0 or height <= 0:
        return 0, height
    row_height = height / float(grid)
    start = int(max(0, (first - overlap_rows) * row_height))
    end = int(min(height, (last + 1 + overlap_rows) * row_height))
    if end <= start:
        end = height
    return start, end


def keep_lines_above(lines: Sequence[Tuple[float, float, str]],
                     y_start: float) -> List[Tuple[float, float, str]]:
    """保留上一帧里"完全在识别带上方"的行（会用它们的原文，不重复 OCR）。

    行的结构是 (y_top, y_bottom, text)；和识别带相交的行必须丢掉，
    因为那条带子会重新识别它们。
    """
    kept: List[Tuple[float, float, str]] = []
    for item in lines:
        y_bottom = item[1] if len(item) > 1 else item[0]
        if y_bottom <= y_start:
            kept.append(item)
    return kept


def merge_lines(previous: Sequence[Tuple[float, float, str]],
                fresh: Sequence[Tuple[float, float, str]],
                y_start: float) -> List[Tuple[float, float, str]]:
    """把"上一帧未变化的上半部分"和"这一帧新识别的下半部分"拼回完整一帧。"""
    return keep_lines_above(previous, y_start) + list(fresh)
