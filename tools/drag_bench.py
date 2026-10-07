"""拖动基准（作者侧）：量"拖着窗口走的时候，窗口落后鼠标多少、主线程卡不卡"。

用法（源码运行的同学）：

    python tools\\drag_bench.py              # 合成鼠标消息（不碰你的真鼠标，最快）
    python tools\\drag_bench.py --real       # 真鼠标（系统输入）拖，最接近手感
    python tools\\drag_bench.py --real --points   # 挨个试"从哪儿拖得动窗口"
    python tools\\drag_bench.py --tour       # 开着新手教学测
    python tools\\drag_bench.py --settle 5   # 先空转 5 秒（看启动那一下堵不堵）

怎么看结果：

* **窗口落后**：0~3 像素＝跟手；几十像素＝明显跟不上；
* **主线程心跳**：正常 10~15ms；某一拍几十/几百毫秒＝UI 线程被别的东西堵住了；
* **窗口位移 0**＝这个按下的位置根本不是拖动把手（按下去没反应）。
"""
from __future__ import annotations

import argparse
import ctypes
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tkinter as tk                             # noqa: E402
from ctypes import wintypes                      # noqa: E402

from app import dpi                              # noqa: E402
from app.config import load_config               # noqa: E402
from app.ui import theme                         # noqa: E402
from app.ui.main_window import MainWindow        # noqa: E402

MOVE_STEP = 6            # 每个鼠标消息挪几像素（真鼠标也差不多这么快）
EVENT_INTERVAL = 0.008   # 8ms 一个消息（≈125Hz）
BEAT_MS = 10             # 主循环心跳节拍

_VERBOSE = {"on": False}
_USER32 = ctypes.windll.user32
_USER32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
_USER32.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
_USER32.GetAwarenessFromDpiAwarenessContext.restype = ctypes.c_int
_USER32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
_USER32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
_USER32.GetAncestor.restype = wintypes.HWND
_USER32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]

LEFT_DOWN = 0x0002
LEFT_UP = 0x0004


# --------------------------------------------------------------------- 小工具
def awareness() -> int:
    """当前线程的 DPI 感知等级：0=不感知 1=系统 2=按显示器 3=按显示器 v2。"""
    return int(_USER32.GetAwarenessFromDpiAwarenessContext(
        _USER32.GetThreadDpiAwarenessContext()))


def cursor_pos():
    point = wintypes.POINT()
    _USER32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def move_to(x, y):
    _USER32.SetCursorPos(int(x), int(y))


def button(flag):
    _USER32.mouse_event(flag, 0, 0, 0, 0)


def root_hwnd(window) -> int:
    return int(_USER32.GetAncestor(wintypes.HWND(int(window.winfo_id())), 2)
               or window.winfo_id())


def hwnd_title(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    _USER32.GetWindowTextW(wintypes.HWND(int(hwnd)), buffer, 256)
    return buffer.value


def hwnd_under(x: int, y: int) -> int:
    _USER32.WindowFromPoint.argtypes = [wintypes.POINT]
    _USER32.WindowFromPoint.restype = wintypes.HWND
    return int(_USER32.WindowFromPoint(wintypes.POINT(int(x), int(y))))


def belongs_to(window, hwnd: int) -> bool:
    """这个句柄是不是（我们主窗口自己或它的子窗）。Tk 的窗口是层层嵌套的，
    只比顶层句柄会误判（实测报过假警），所以顺着父链往上找。"""
    ours = root_hwnd(window)
    node = int(hwnd)
    for _ in range(12):
        if node == ours:
            return True
        parent = int(_USER32.GetParent(wintypes.HWND(node)) or 0)
        if not parent or parent == node:
            break
        node = parent
    return False


def pump(app, seconds: float) -> None:
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        app.root.update()
        time.sleep(0.002)


def describe_dpi(app, label: str) -> None:
    print("[DPI] %s：主线程感知等级=%d，Tk 屏幕=%dx%d"
          % (label, awareness(), app.root.winfo_screenwidth(),
             app.root.winfo_screenheight()), flush=True)


# ------------------------------------------------------------------ 拖动实现
def synthetic_drag(app, seconds: float) -> list:
    """合成鼠标消息（只走 Tk，不碰真鼠标）：量每个事件处理和窗口落后。"""
    handle = app.top_frame
    handle.update_idletasks()
    start_x = app.root.winfo_x() + 40
    grab_offset = 40
    handle.event_generate("<ButtonPress-1>", x=40, y=10,
                          rootx=start_x, rooty=app.root.winfo_y() + 10, when="now")
    app.root.update()
    samples = []
    cursor_x = start_x
    direction = 1
    next_event = time.perf_counter()
    end = next_event + seconds
    while time.perf_counter() < end:
        now = time.perf_counter()
        if now >= next_event:
            cursor_x += MOVE_STEP * direction
            if abs(cursor_x - start_x) > 220:
                direction = -direction
            started = time.perf_counter()
            handle.event_generate("<B1-Motion>", x=40, y=10,
                                  rootx=cursor_x, rooty=app.root.winfo_y() + 10,
                                  when="now")
            cost = (time.perf_counter() - started) * 1000.0
            app.root.update_idletasks()
            lag = abs(cursor_x - app.root.winfo_x() - grab_offset)
            samples.append((cost, lag, time.perf_counter()))
            next_event += EVENT_INTERVAL
        else:
            app.root.update()
        time.sleep(0.001)
    handle.event_generate("<ButtonRelease-1>", x=40, y=10,
                          rootx=cursor_x, rooty=app.root.winfo_y() + 10)
    app.root.update()
    return samples


def real_drag(app, seconds: float, point) -> list:
    """真鼠标（系统输入）拖：窗口真的在跟人走。返回 [(耗时, 落后, 时刻)]。"""
    grab_x, grab_y, label = point
    grab_offset = grab_x - app.root.winfo_x()
    start_x = app.root.winfo_x()
    saved = cursor_pos()
    samples = []
    print("抓「%s」(%d, %d) 拖 %.1f 秒…" % (label, grab_x, grab_y, seconds), flush=True)
    try:
        move_to(grab_x, grab_y)
        pump(app, 0.15)
        top = hwnd_under(grab_x, grab_y)
        if not belongs_to(app.root, top):
            print("      ← 抓点上不是我们的窗口（%s），这次作废"
                  % (hwnd_title(top) or "没有标题"), flush=True)
        button(LEFT_DOWN)
        pump(app, 0.1)
        if _VERBOSE["on"]:
            print("      按下后：frameless 模式=%r（move=跟手拖动已开始），窗口在 %d"
                  % (app.frameless._mode, app.root.winfo_x()), flush=True)
        cursor_x = grab_x
        # 单向往右扫（来回摆的话最后可能正好回到出发位置，"位移 0"就看不出好坏）
        distance = max(120, min(360, int(app.root.winfo_screenwidth())
                                - app.root.winfo_x() - app.root.winfo_width() - 40))
        target_x = grab_x + distance
        end = time.perf_counter() + seconds
        trace = []
        while cursor_x < target_x and time.perf_counter() < end:
            cursor_x = min(target_x, cursor_x + MOVE_STEP)
            move_to(cursor_x, grab_y)
            app.root.update()
            app.root.update_idletasks()
            lag = abs(cursor_pos()[0] - app.root.winfo_x() - grab_offset)
            samples.append((0.0, lag, time.perf_counter()))
            if len(trace) < 8 or len(samples) % 80 == 0:
                trace.append((cursor_x, app.root.winfo_x(), lag))
            time.sleep(0.004)
        button(LEFT_UP)
        app.root.update()
        if _VERBOSE["on"]:
            print("      （光标 x, 窗口 x, 落后）：%r" % (trace,), flush=True)
    finally:
        move_to(*saved)
    moved = app.root.winfo_x() - start_x
    try:                                       # 摆回原处：下面还要试别的抓点
        app.root.geometry("+%d+%d" % (start_x, app.root.winfo_y()))
        pump(app, 0.15)
    except Exception:                          # noqa: BLE001
        pass
    print("      窗口位移 %d 像素%s" % (
        moved, "   ← 没动：这个位置按下去不是拖动把手" if abs(moved) < 20 else ""),
        flush=True)
    return samples


def grab_points(app):
    """几个候选"抓哪儿拖窗口"的位置（按 DDO 标题 / 工具条留白 / 状态栏留白 …）。"""
    app.root.update_idletasks()
    for widget in (app.brand, app.top_frame, app.status_bar, app.text):
        widget.update_idletasks()
    points = [(app.brand.winfo_rootx() + app.brand.winfo_width() // 2,
               app.brand.winfo_rooty() + app.brand.winfo_height() // 2,
               "DDO 标题")]
    points.append((app.top_frame.winfo_rootx() + app.top_frame.winfo_width() // 2,
                   app.top_frame.winfo_rooty() + 3,
                   "工具条上沿留白"))
    points.append((app.status_bar.winfo_rootx() + app.status_bar.winfo_width() // 2,
                   app.status_bar.winfo_rooty() + 2,
                   "状态栏上沿留白"))
    points.append((app.text.winfo_rootx() + app.text.winfo_width() // 2,
                   app.text.winfo_rooty() + app.text.winfo_height() // 2,
                   "聊天区正中"))
    return points


# -------------------------------------------------------------------- 报告
def report(samples, beats) -> None:
    print("\n" + "=" * 62)
    if not samples:
        print("一个拖动事件都没发出去 —— 基准本身有问题，别信这份结果")
    else:
        lags = sorted(s[1] for s in samples)
        costs = sorted(s[0] for s in samples)
        print("拖动事件数：%d" % len(samples))
        if costs[-1] > 0:
            print("每个事件处理耗时：平均 %.1f ms、中位 %.1f ms、最慢 %.1f ms"
                  % (sum(costs) / len(costs), costs[len(costs) // 2], costs[-1]))
        print("窗口落后光标：平均 %.1f 像素、中位 %.0f、最大 %d"
              % (sum(lags) / len(lags), lags[len(lags) // 2], lags[-1]))
    phases = []
    for _gap, phase in beats:
        if phase not in phases:
            phases.append(phase)
    for phase in phases:
        gaps = sorted(g for g, p in beats if p == phase)
        print("[%s] 主线程心跳：平均 %.1f ms、中位 %.1f ms、最慢 %.1f ms、>40ms 有 %d 次"
              % (phase, sum(gaps) / len(gaps) * 1000, gaps[len(gaps) // 2] * 1000,
                 gaps[-1] * 1000, sum(1 for g in gaps if g > 0.04)))
    print("=" * 62)


def parse_args(argv):
    parser = argparse.ArgumentParser(description="拖动跟手度基准")
    parser.add_argument("seconds", nargs="?", type=float, default=3.0,
                        help="拖多久（秒），默认 3")
    parser.add_argument("--real", action="store_true", help="用真鼠标（会挪动你的光标）")
    parser.add_argument("--points", action="store_true",
                        help="挨个试几个位置：从哪儿能拖动窗口")
    parser.add_argument("--tour", action="store_true", help="先打开新手教学")
    parser.add_argument("--listening", action="store_true", help="先开始监听")
    parser.add_argument("--settle", type=float, default=0.0, help="拖动前先空转几秒")
    parser.add_argument("--verbose", action="store_true", help="多打细节")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    _VERBOSE["on"] = bool(args.verbose)
    config = load_config()
    dpi.enable(str(config.get("dpi_mode", "auto")))
    app = MainWindow()
    for key, value in (("check_update", False), ("tour_done", True),
                       ("contribute_invite_done", True)):
        try:
            app.config[key] = value
        except Exception:                          # noqa: BLE001
            pass
    app.root.deiconify()
    app.root.lift()
    pump(app, 0.6)
    describe_dpi(app, "建完主窗口")

    if args.listening:
        app.pipeline.start()
        print("已开始监听（真实抓屏 + OCR）", flush=True)
    if args.tour:
        app.open_tour()
        pump(app, 0.8)
        describe_dpi(app, "教学打开之后")

    beats = []
    state = {"last": time.perf_counter(), "stop": False, "phase": "启动"}

    def beat():
        now = time.perf_counter()
        beats.append((now - state["last"], state["phase"]))
        state["last"] = now
        if not state["stop"]:
            app.root.after(BEAT_MS, beat)

    app.root.after(BEAT_MS, beat)
    if args.settle:
        print("%.1f 秒空转（看启动/开监听那一下堵不堵）…" % args.settle, flush=True)
        pump(app, args.settle)

    state["phase"] = "拖动"
    samples = []
    if args.real or args.points:
        points = grab_points(app) if (args.points or not args.real) else []
        if not points:
            brand = app.brand
            points = [(brand.winfo_rootx() + brand.winfo_width() // 2,
                       brand.winfo_rooty() + brand.winfo_height() // 2, "DDO 标题")]
        for point in points:
            samples.extend(real_drag(app, args.seconds, point))
    else:
        samples = synthetic_drag(app, args.seconds)

    state["stop"] = True
    if args.listening:
        app.pipeline.stop()
    report(samples, beats)
    try:
        app.root.destroy()
    except Exception:                              # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
