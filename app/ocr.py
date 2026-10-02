"""OCR 封装：RapidOCR + PP-OCRv5 模型。

图像预处理严格保持"只做 1.2 倍对比度增强"——旧版试过 2 倍对比度 + 2 倍锐化，
会导致文字块 y 坐标错位、短消息跑到第一行，所以这里不动这个参数。
"""
from __future__ import annotations

import statistics
import threading
from typing import List, Sequence, Tuple

BoxItem = Tuple[str, list]


def _points(box) -> list:
    if not box:
        return []
    if isinstance(box[0], (list, tuple)):
        return [p for p in box[0] if isinstance(p, (list, tuple)) and len(p) >= 2]
    if len(box) >= 2:
        return [box]
    return []


def _center_y(box) -> float:
    points = _points(box)
    if not points:
        return 0.0
    try:
        return sum(float(p[1]) for p in points) / len(points)
    except Exception:
        return 0.0


def _height(box) -> float:
    points = _points(box)
    if not points:
        return 0.0
    try:
        ys = [float(p[1]) for p in points]
        return max(ys) - min(ys)
    except Exception:
        return 0.0


def _left_x(box) -> float:
    points = _points(box)
    if not points:
        return 0.0
    try:
        return min(float(p[0]) for p in points)
    except Exception:
        return 0.0


def sort_by_position(items: Sequence[BoxItem]) -> List[BoxItem]:
    """按 y 从上到下、同一行按 x 从左到右排序。"""
    return sorted(items, key=lambda item: (_center_y(item[1]), _left_x(item[1])))


def rows_to_lines(items: Sequence[BoxItem], scale: float = 1.0,
                  offset: float = 0.0) -> List[Tuple[float, float, str]]:
    """把 OCR 结果转成 [(y_top, y_bottom, text), ...]（坐标换算回"区域坐标"）。

    items 里的框坐标是"放大后"的坐标，所以要除以 scale；offset 是这条带子
    在原始区域里的起始 y（只识别了下面一条时用得到）。
    """
    result: List[Tuple[float, float, str]] = []
    factor = scale if scale and scale > 0 else 1.0
    for text, box in items:
        if not text:
            continue
        center = _center_y(box) / factor
        height = _height(box) / factor
        if height <= 0:
            height = 16.0
        top = center - height / 2.0 + offset
        result.append((top, top + height, text))
    result.sort(key=lambda item: item[0])
    return result


def group_rows(items: Sequence[BoxItem], tol_ratio: float = 0.6) -> List[BoxItem]:
    """把同一视觉行的碎片拼成一行。

    OCR 有时会把 "(小队):[小队]" 和后面的正文拆成两个框；这里按"中心 y 差在
    0.6 倍字高以内"归为同一行，再按 x 顺序用空格拼起来。
    """
    if not items:
        return []
    ordered = sort_by_position(items)
    heights = [h for h in (_height(item[1]) for item in ordered) if h > 0]
    tolerance = (statistics.median(heights) if heights else 16.0) * tol_ratio

    rows: List[List[BoxItem]] = []
    row_centers: List[float] = []
    for item in ordered:
        center = _center_y(item[1])
        for index in range(len(rows) - 1, -1, -1):
            if abs(center - row_centers[index]) <= tolerance:
                rows[index].append(item)
                row_centers[index] = (center + row_centers[index]) / 2
                break
        else:
            rows.append([item])
            row_centers.append(center)

    result: List[BoxItem] = []
    for row in rows:
        row.sort(key=lambda item: _left_x(item[1]))
        text = " ".join(part for part, _box in row if part).strip()
        text = text.replace("  ", " ").strip()
        if text:
            result.append((text, row[0][1]))
    return result


class OcrEngine:
    """懒加载 + 可释放的 RapidOCR 包装。"""

    def __init__(self) -> None:
        self._engine = None
        self._error = ""
        self._lock = threading.Lock()
        # 低于这个置信度的识别结果直接丢掉（0 = 不过滤）。
        # 背景花时 OCR 会吐出"像字其实是噪点"的行，或者把同一行读成各种错字版本。
        self._min_score = 0.5
        self._dropped_low_score = 0
        # 识别前是否做"背景压平"（聊天框半透明、背后景物忽明忽暗时用）
        self._flatten = True

    def set_flatten(self, value: bool) -> None:
        """开/关背景压平（见 app/preprocess.py）。"""
        self._flatten = bool(value)

    def set_min_score(self, value: float) -> None:
        """设置置信度门槛（0~1；0 表示不过滤）。"""
        try:
            self._min_score = max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            self._min_score = 0.5

    @property
    def dropped_low_score(self) -> int:
        """被"置信度太低"丢掉的行数（给状态栏/自检看）。"""
        return self._dropped_low_score

    @property
    def error(self) -> str:
        return self._error

    def available(self) -> bool:
        return self._engine is not None

    def load(self, threads: int = 0) -> bool:
        """加载 OCR 模型。threads>0 时限制推理线程数（默认给游戏留出 CPU）。"""
        with self._lock:
            if self._engine is not None:
                return True
            try:
                from rapidocr_onnxruntime import RapidOCR
            except Exception as exc:
                self._error = "没装 rapidocr_onnxruntime：%s" % exc
                return False
            options = {}
            if threads and threads > 0:
                options["intra_op_num_threads"] = int(threads)
                options["inter_op_num_threads"] = 1
            for attempt in (
                dict(options, det_model_name="PP-OCRv5_mobile_det",
                     rec_model_name="PP-OCRv5_mobile_rec",
                     cls_model_name="mobile_cls"),
                dict(det_model_name="PP-OCRv5_mobile_det",
                     rec_model_name="PP-OCRv5_mobile_rec",
                     cls_model_name="mobile_cls"),
                dict(options),
                {},
            ):
                try:
                    self._engine = RapidOCR(**attempt)
                    break
                except Exception as exc:
                    self._error = "OCR 模型加载失败：%s" % exc
            if self._engine is None:
                return False
            self._error = ""
            return True

    def recognize(self, image, upscale: float = 1.0) -> List[BoxItem]:
        """返回 [(文本, 框), ...]，已按屏幕位置排好序。

        upscale>1 时先把图放大再识别：聊天框通常只有几百像素宽，字很小，
        放大后识别质量（尤其是空格、相邻行是否被合并）会明显改善。
        框坐标会一起放大，但这里只用来排序/分行，不受影响。
        """
        if self._engine is None and not self.load():
            return []
        try:
            from PIL import Image, ImageEnhance, ImageOps

            from . import preprocess

            if self._flatten:
                # DDO 的聊天框是半透明的：背后景物一动，同一行字的明暗就变了。
                # 自动对比度只能按整张图拉伸，管不了"左边亮右边暗"这种局部差异，
                # 所以这里改成把背景整体压平成中灰，只留文字笔画（见 preprocess.py）。
                rgb = preprocess.flatten(image).convert("RGB")
            else:
                # 老做法（设置里关掉"背景压平"时用）：整图自动对比度 + 一点点对比度
                rgb = image.convert("RGB")
                try:
                    rgb = ImageOps.autocontrast(rgb, cutoff=1)
                except Exception:
                    pass
                rgb = ImageEnhance.Contrast(rgb).enhance(1.2)
            if upscale and abs(upscale - 1.0) > 0.01:
                rgb = rgb.resize((max(1, int(rgb.width * upscale)),
                                  max(1, int(rgb.height * upscale))), Image.LANCZOS)
            result, _elapsed = self._engine(rgb)
        except Exception as exc:
            self._error = "识别出错：%s" % exc
            return []

        items: List[BoxItem] = []
        for row in result or []:
            if len(row) < 2:
                continue
            text = str(row[1]).strip()
            if not text:
                continue
            # 置信度：背景花的时候 OCR 会吐出一些"看着像字其实是噪点"的行，
            # 它们会被当成新消息（或者同一行的另一个读法）显示出来。低于阈值直接丢。
            if self._min_score > 0:
                try:
                    score = float(row[2]) if len(row) > 2 else 1.0
                except (TypeError, ValueError):
                    score = 1.0
                if score < self._min_score:
                    self._dropped_low_score += 1
                    continue
            items.append((text, row[0]))
        return sort_by_position(items)

    def close(self) -> None:
        with self._lock:
            self._engine = None
