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
import sys
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


# --------------------------------------------------------------------------
# 只抓指定区域（GDI BitBlt）
# Pillow 的 ImageGrab.grab(bbox=...) 实际上是"先抓整个屏幕，再在 Python 里裁剪"：
# 2560×1440 的屏每帧要多拷 14MB。这里直接 BitBlt 目标矩形，省掉这部分固定开销。
# 为了安全，调用方会先拿它和 Pillow 的结果比对一次，不一致就自动回退。
# --------------------------------------------------------------------------
def _grab_region_gdi(box):
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        from PIL import Image

        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        # 64 位下必须声明原型，否则句柄会被截断成 32 位（经典坑）
        user32.GetDC.restype = wintypes.HDC
        user32.GetDC.argtypes = [wintypes.HWND]
        user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        gdi32.CreateCompatibleDC.restype = wintypes.HDC
        gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
        gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
        gdi32.SelectObject.restype = wintypes.HGDIOBJ
        gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int,
                                 wintypes.DWORD]
        gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT,
                                    wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p,
                                    wintypes.UINT]
        gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        gdi32.DeleteDC.argtypes = [wintypes.HDC]

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD),
            ]

        left, top, right, bottom = (int(v) for v in box)
        width, height = right - left, bottom - top
        if width <= 0 or height <= 0:
            return None

        screen_dc = user32.GetDC(None)
        if not screen_dc:
            return None
        mem_dc = gdi32.CreateCompatibleDC(screen_dc)
        bitmap = gdi32.CreateCompatibleBitmap(screen_dc, width, height)
        if not mem_dc or not bitmap:
            return None
        old_bitmap = gdi32.SelectObject(mem_dc, bitmap)
        try:
            gdi32.BitBlt(mem_dc, 0, 0, width, height, screen_dc, left, top, 0x00CC0020)
            info = BITMAPINFOHEADER()
            info.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            info.biWidth = width
            info.biHeight = -height          # 负数 = 自上而下，省得再翻转
            info.biPlanes = 1
            info.biBitCount = 32
            info.biCompression = 0           # BI_RGB
            buffer = ctypes.create_string_buffer(width * height * 4)
            if not gdi32.GetDIBits(mem_dc, bitmap, 0, height, buffer,
                                   ctypes.byref(info), 0):
                return None
            image = Image.frombuffer("RGB", (width, height), buffer,
                                     "raw", "BGRX", 0, 1)
            return image.copy()              # 拷一份，缓冲区释放后仍可用
        finally:
            try:
                gdi32.SelectObject(mem_dc, old_bitmap)
            except Exception:
                pass
            try:
                gdi32.DeleteObject(bitmap)
            except Exception:
                pass
            try:
                gdi32.DeleteDC(mem_dc)
            except Exception:
                pass
            try:
                user32.ReleaseDC(None, screen_dc)
            except Exception:
                pass
    except Exception:
        return None


def grab_fast(region, tk_screen: Optional[Sequence[int]] = None):
    """快速抓图：只抓指定区域（失败返回 None，调用方回退到 grab()）。"""
    if not region:
        return None
    try:
        box = convert_region(region, tk_screen)
    except Exception:
        return None
    image = _grab_region_gdi(box)
    if image is None:
        return None
    expected = (box[2] - box[0], box[3] - box[1])
    if image.size != expected:
        return None
    return image


def images_similar(first, second, size: int = 64, tolerance: float = 12.0) -> bool:
    """判断两张截图是否"看起来一样"（缩成 64×64 灰度后比较平均灰度差）。

    用来校验"快速抓图"的结果是否和系统截图一致；不一致就回退，不会带着错图跑。
    """
    if first is None or second is None:
        return False
    try:
        from PIL import Image

        resample = getattr(getattr(Image, "Resampling", Image), "BILINEAR")
        left = first.convert("L").resize((size, size), resample).tobytes()
        right = second.convert("L").resize((size, size), resample).tobytes()
        if len(left) != len(right) or not left:
            return False
        total = sum(abs(a - b) for a, b in zip(left, right))
        return (total / float(len(left))) <= tolerance
    except Exception:
        return False


def signature(image) -> str:
    """画面指纹，用来跳过"像素完全没变"的帧。"""
    if image is None:
        return ""


def frame_signature(image, size: int = 48) -> bytes:
    """把画面缩成极小的灰度图，用来"便宜地"判断这一帧和上一帧是否一样。

    聊天框大部分时间是静止的；先用这个（约 2300 字节比较）判断有没有变化，
    没变化就完全跳过 OCR —— 这是监听时最省 CPU 的一招。
    """
    if image is None:
        return b""
    try:
        from PIL import Image

        resample = getattr(getattr(Image, "Resampling", Image), "BILINEAR")
        return image.convert("L").resize((size, size), resample).tobytes()
    except Exception:
        return b""


def frames_differ(current: bytes, previous: bytes,
                  tolerance: int = 6, threshold: int = 6) -> bool:
    """比较两帧指纹：允许 tolerance 级灰阶差异、最多 threshold 个像素点不同。

    这样能忽略光标闪烁、轻微抖动这类"没意义的变化"，但只要有新聊天行
    （大片像素变化）就会判定为"变了"。
    """
    from . import frame as frame_module

    changed, _first, _last = frame_module.analyse_frame(
        current, previous, tolerance=tolerance, threshold=threshold)
    return changed
    try:
        return hashlib.sha1(image.convert("RGB").tobytes()).hexdigest()
    except Exception:
        return ""
