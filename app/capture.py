"""屏幕截图。

坑点：Tk 的坐标空间和截图的像素空间不一定一致。

* 进程不感知 DPI 时，Windows 会把 Tk 报的坐标按显示缩放虚拟化
  （125% 缩放下 2560 物理像素只报 2048），而截图拿到的是另一套像素，
  于是"框选的区域"和"实际采样的区域"对不上。
* 即使进程感知了 DPI，个别机器/Pillow 版本下 `all_screens=True` 抓到的
  位图尺寸仍可能和 Tk 报的屏幕尺寸不同。

所以这里做了两件事：
1. `screen_scale()` 把「截图空间 / Tk 空间」的比例算出来，抓图前先换算坐标；
2. 抓完校验尺寸，不一致就退回普通模式再抓一次，并把情况报到界面上。
"""
from __future__ import annotations

import hashlib
from typing import Optional, Sequence


def _prefer_physical_pixels() -> None:
    """尽量让截图拿到物理像素。

    进程整体已经是"按显示器感知"时这步是空操作；万一某些环境里
    当前线程的 DPI 上下文被改过（Tk/Pillow 都可能动它），显式设一次
    能让 GDI 按物理像素出图。设置失败也不影响后面的自动换算。
    """
    try:
        import ctypes

        ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass


def capture_space() -> Optional[tuple]:
    """估算截图能看到的像素空间（主屏）：来自 Win32 的 SM_CXSCREEN/SM_CYSCREEN。

    这个值会跟着进程的 DPI 感知状态变：不感知时是虚拟化后的尺寸，
    感知时是物理尺寸。它只是估算，真正的判断用 measure_scale()。
    """
    try:
        import ctypes

        width = int(ctypes.windll.user32.GetSystemMetrics(0))
        height = int(ctypes.windll.user32.GetSystemMetrics(1))
        if width > 0 and height > 0:
            return width, height
    except Exception:
        pass
    return None


_CALIBRATION = {"tk": None, "scale": (1.0, 1.0), "capture": None}


def _sane(value: float) -> bool:
    return 0.5 <= value <= 2.0


def measure_scale(tk_screen: Optional[Sequence[int]] = None,
                  force: bool = False) -> tuple:
    """实测换算比例：抓一张整屏，跟 Tk 报的屏幕尺寸相除。

    这是"地面真相"——不猜 Pillow/Windows 内部怎么处理 DPI，
    直接量出截图实际拿到的像素空间。结果会缓存，避免每帧都抓整屏。
    """
    if not tk_screen:
        return 1.0, 1.0
    tk_pair = (int(tk_screen[0]), int(tk_screen[1]))
    if not force and _CALIBRATION.get("tk") == tk_pair:
        return _CALIBRATION["scale"]
    try:
        tk_w, tk_h = float(tk_pair[0]), float(tk_pair[1])
        if tk_w <= 0 or tk_h <= 0:
            return 1.0, 1.0
        full = grab_full()
        if full is None:
            return estimated_scale(tk_pair)
        sx = full.width / tk_w
        sy = full.height / tk_h
        if not (_sane(sx) and _sane(sy)):
            sx = sy = 1.0
        _CALIBRATION.update({"tk": tk_pair, "scale": (sx, sy),
                             "capture": full.size})
        return sx, sy
    except Exception:
        return 1.0, 1.0


def estimated_scale(tk_screen: Optional[Sequence[int]] = None) -> tuple:
    """估算比例（抓不了整屏时的退路）。"""
    if not tk_screen:
        return 1.0, 1.0
    space = capture_space()
    if not space:
        return 1.0, 1.0
    try:
        tk_w, tk_h = float(tk_screen[0]), float(tk_screen[1])
        if tk_w <= 0 or tk_h <= 0:
            return 1.0, 1.0
        sx, sy = space[0] / tk_w, space[1] / tk_h
    except Exception:
        return 1.0, 1.0
    return (sx, sy) if (_sane(sx) and _sane(sy)) else (1.0, 1.0)


def active_scale(tk_screen: Optional[Sequence[int]] = None) -> tuple:
    """当前生效的比例：优先用实测值，其次用估算值。"""
    if not tk_screen:
        return 1.0, 1.0
    tk_pair = (int(tk_screen[0]), int(tk_screen[1]))
    if _CALIBRATION.get("tk") == tk_pair:
        return _CALIBRATION["scale"]
    return estimated_scale(tk_pair)


def scale_report(tk_screen: Optional[Sequence[int]] = None) -> dict:
    """给日志/自检工具用的一份说明。"""
    measured = measure_scale(tk_screen)
    return {
        "tk_screen": tuple(tk_screen) if tk_screen else None,
        "capture_space(估算)": capture_space(),
        "measured_capture(实测整屏)": _CALIBRATION.get("capture"),
        "scale(实测)": measured,
        "scale(估算)": estimated_scale(tk_screen),
    }


def convert_region(region: Sequence[int], tk_screen: Optional[Sequence[int]] = None):
    """把 Tk 空间的区域换算到截图空间。"""
    left, top, right, bottom = (int(value) for value in region)
    sx, sy = active_scale(tk_screen)
    if sx != 1.0 or sy != 1.0:
        left = int(round(left * sx))
        top = int(round(top * sy))
        right = int(round(right * sx))
        bottom = int(round(bottom * sy))
    if right - left < 2 or bottom - top < 2:
        right, bottom = left + 2, top + 2
    return [left, top, right, bottom]


def _try_pillow_grab(box):
    _prefer_physical_pixels()
    try:
        from PIL import ImageGrab
    except Exception:
        return None
    try:
        image = ImageGrab.grab(bbox=tuple(box), all_screens=True)
        if image is not None and image.size == (box[2] - box[0], box[3] - box[1]):
            return image
    except TypeError:
        pass
    except Exception:
        return None
    try:
        return ImageGrab.grab(bbox=tuple(box))
    except Exception:
        return None


def grab(region: Optional[Sequence[int]] = None,
         tk_screen: Optional[Sequence[int]] = None):
    """抓取屏幕区域，返回 PIL.Image；失败返回 None。

    tk_screen 传 Tk 认为的屏幕尺寸（winfo_screenwidth/height）；
    两者不一致时会自动按比例换算坐标。
    """
    try:
        from PIL import ImageGrab
    except Exception:
        return None

    try:
        if region:
            box = convert_region(region, tk_screen)
            left, top, right, bottom = box
            if right <= left or bottom <= top:
                return None
            return _try_pillow_grab(box)
        return ImageGrab.grab()
    except Exception:
        return None


def grab_full():
    """整屏截图（含所有显示器），给自检/取证工具用。"""
    _prefer_physical_pixels()
    try:
        from PIL import ImageGrab

        return ImageGrab.grab(all_screens=True)
    except Exception:
        return None


def signature(image) -> str:
    """画面指纹，用来跳过"像素完全没变"的帧。"""
    if image is None:
        return ""
    try:
        return hashlib.sha1(image.convert("RGB").tobytes()).hexdigest()
    except Exception:
        return ""
