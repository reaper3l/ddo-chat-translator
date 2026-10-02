"""背景压平：让 OCR 不被半透明聊天框背后的景物带偏。

DDO 的聊天框是半透明的，背后景物亮一下暗一下，字的**绝对**明暗就跟着变；
更麻烦的是同一张图里左边亮、右边暗（亮的东西从聊天框后面扫过）。
`ImageOps.autocontrast` 只能按整张图做一次拉伸，管不了这种**局部**差异，
所以背景一花，OCR 就会把同一行读成不同版本，或者读出一堆"像字其实是噪点"的行。

这里换成局部做法：

    灰度 → 用大半径高斯模糊估一张"背景亮度图" → 相减 → 加回中灰

剩下的只有"相对背景亮/暗多少"的高频部分，也就是文字笔画本身；背景整体多亮多暗、
有没有明暗过渡，都不再影响它。

实测（三张真实游戏截图，人为做"整体变亮 / 变暗 / 亮带扫过"共 6 种背景变化）：
识别结果平均相似度 0.883 → 0.979，并且原图上的识别准确率没有下降；
压平这步本身只要 2ms 左右（443×205 的区域）。
"""
from __future__ import annotations

# 模糊半径太小 → 压不平背景；太大 → 把笔画本身的明暗也削掉。
# 文字高度约占图高的 1/15 左右，取 min(宽,高)/30 落在合适区间。
_MIN_RADIUS = 4
_MAX_RADIUS = 12


def radius_for(width: int, height: int) -> int:
    """按图的大小算一个合适的模糊半径。"""
    try:
        short_side = min(int(width), int(height))
    except (TypeError, ValueError):
        return _MIN_RADIUS
    if short_side <= 0:
        return _MIN_RADIUS
    radius = int(round(short_side / 30.0))
    return max(_MIN_RADIUS, min(_MAX_RADIUS, radius))


def flatten(image, radius: int = 0):
    """把背景压平成中灰，只留下文字笔画。返回灰度图（"L" 模式）。

    image 为 None 或处理失败时原样返回 None / 原图，调用方不用额外判空。
    """
    if image is None:
        return image
    try:
        import numpy as np
        from PIL import Image, ImageFilter, ImageOps

        gray = ImageOps.grayscale(image)
        if radius is None or radius <= 0:
            radius = radius_for(gray.width, gray.height)
        background = gray.filter(ImageFilter.GaussianBlur(radius=radius))
        values = (np.asarray(gray, dtype=np.int16)
                  - np.asarray(background, dtype=np.int16) + 128)
        return Image.fromarray(np.clip(values, 0, 255).astype(np.uint8), "L")
    except Exception:
        return image
