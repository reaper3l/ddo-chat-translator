"""DPI 感知开关（主程序和诊断工具都要在**创建窗口/截图之前**调用）。

为什么单独成一个模块：以前这段只在 main.py 里，于是 tools\\trace.py、
tools\\capture_probe.py 这些诊断工具是"DPI 不感知"跑的 —— Windows 会把它们的
坐标和截图按缩放比例虚拟化（125% 时抓到的图比实际小一圈），OCR 自然认不出字，
报告回来的诊断结果就成了假象。
"""
from __future__ import annotations

import sys


def enable(mode: str = "auto") -> str:
    """让进程感知 DPI。返回一句给日志用的状态。

    auto（默认）：按显示器感知 DPI（per-monitor v2）——框选坐标和截图坐标
    落在同一个物理像素空间里。
    legacy：旧行为（不感知 DPI），个别机器上仍不对时可以退回。
    """
    if sys.platform != "win32":
        return "non-windows"

    import ctypes

    if str(mode or "auto").lower() == "legacy":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(0)
            return "legacy(unaware)"
        except Exception:
            return "legacy(failed)"

    # 1) Win10 1703+：per-monitor v2（DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4）
    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return "per-monitor-v2"
    except Exception:
        pass
    # 2) Win8.1+：per-monitor
    try:
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            return "per-monitor"
    except Exception:
        pass
    # 3) Vista+：system aware
    try:
        if ctypes.windll.user32.SetProcessDPIAware():
            return "system"
    except Exception:
        pass
    return "unknown"
