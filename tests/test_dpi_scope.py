"""DPI 上下文作用域：临时切"按物理像素"算坐标时，**用完必须还原**。

这条是回归测试（用户实测踩过）：早先新手教学的金框是"鼠标穿透"的浮窗，拿窗口句柄时
需要按物理像素算，于是临时把当前线程切成 per-monitor；但收尾写死
`SetThreadDpiAwarenessContext(-1)`（不感知），把 **Tk 主线程**改成了"不感知 DPI"。
之后 Windows 会把所有窗口位置/尺寸按显示缩放虚拟化（实测 125% 下
`geometry("+1000+400")` 真的跑到 (1250,500)、343×295 变 429×369），
拖动时窗口跑得比鼠标快 25%，用户反馈"拖动框体不跟手"。
（那个浮窗现在已经没有了 —— 教学改成主窗口内部一页；这条测试继续守着这个坑。）
"""
from __future__ import annotations

import ctypes
import sys

from app.ui import theme

# 按显示器感知：2=per-monitor（v1）3=per-monitor v2。两种都算"按物理像素"，
# 具体是哪一个取决于系统/Python 启动时已经定好的那档（实测本机是 2）。
PER_MONITOR = (2, 3)


def _set_thread_context(value: int) -> None:
    ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(value))


def _current_context() -> int:
    user32 = ctypes.windll.user32
    user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    return int(user32.GetThreadDpiAwarenessContext())


def test_physical_scope_restores_previous_context():
    """进来前是什么感知等级，出去以后还得是什么（不能变成"不感知"）。"""
    if sys.platform != "win32":
        return
    original = _current_context()
    try:
        _set_thread_context(-4)                    # 按显示器 v2：和真实程序一致
        before = theme.thread_dpi_awareness()
        assert before in PER_MONITOR, (
            "没能切到「按显示器感知」（拿到 %r），这个环境下这条检查没意义" % (before,))
        with theme._physical_pixel_scope():
            inside = theme.thread_dpi_awareness()
        after = theme.thread_dpi_awareness()
        assert inside == before, "作用域内应该仍然是按物理像素"
        assert after == before, "用完没还原 DPI 上下文：会毁掉 Tk 的坐标（拖动就不跟手）"
    finally:
        _set_thread_context(original)


def test_awareness_helper_reads_a_level():
    """自检工具靠这个函数下结论，它得能读出等级（不是 None/异常）。"""
    if sys.platform != "win32":
        return
    level = theme.thread_dpi_awareness()
    assert level in (0, 1, 2, 3, 4), "读出来的 DPI 感知等级不合理：%r" % (level,)
