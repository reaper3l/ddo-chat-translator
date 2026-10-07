"""拖动诊断：量"拖着窗口走的时候，窗口落后光标多少像素、主线程卡不卡"。

用法（源码运行的同学）：

    python tools\\drag_check.py

窗口打开后，**用鼠标按着标题栏 / 状态栏拖它 10 秒左右**（左右晃一晃），不要松手；
时间到了程序自己会打印结果。看三个数：

* **窗口落后光标**：0~3 像素＝完全跟手；几十像素＝明显跟不上（就是"延时很高"）；
* **两个鼠标消息之间**：正常 5~15ms；经常几十/几百毫秒＝UI 线程被别的东西堵住了；
* **主线程心跳**：正常 10~15ms；某一拍特别长，就是那一下被堵了。

顺带会测一遍"没拖动时的空闲 CPU"用来对照。

以前这里用"替换 app.frameless._on_drag"的办法统计每个事件的处理耗时 —— 那是错的：
`bind()` 的时候 Tk 已经把那个绑定方法注册进 Tcl 了，事后替换属性根本拦不到调用，
所以永远报"没抓到拖动事件"（实测）。现在改成在窗口上再挂一个 `<B1-Motion>`，
量"和上一个鼠标消息隔了多久"以及"窗口落后光标多少"——这两个数才是用户手感。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ctypes                        # noqa: E402
from ctypes import wintypes          # noqa: E402

from app import dpi                  # noqa: E402
from app.config import load_config   # noqa: E402
from app.ui import theme             # noqa: E402
from app.ui.main_window import MainWindow   # noqa: E402

DRAG_SECONDS = 10.0
BEAT_MS = 10


def _cpu_seconds() -> float:
    kernel32 = ctypes.windll.kernel32
    created, exited = wintypes.FILETIME(), wintypes.FILETIME()
    kernel, user = wintypes.FILETIME(), wintypes.FILETIME()
    kernel32.GetProcessTimes(kernel32.GetCurrentProcess(),
                             ctypes.byref(created), ctypes.byref(exited),
                             ctypes.byref(kernel), ctypes.byref(user))

    def as_int(value):
        return (value.dwHighDateTime << 32) | value.dwLowDateTime

    return (as_int(kernel) + as_int(user)) / 1e7


def _cursor() -> tuple:
    point = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def main() -> int:
    config = load_config()
    dpi.enable(str(config.get("dpi_mode", "auto")))
    app = MainWindow()
    try:
        app.config["check_update"] = False
        app.config["tour_done"] = True
        app.config["contribute_invite_done"] = True
    except Exception:                              # noqa: BLE001
        pass
    app.root.deiconify()
    app.root.lift()
    for _ in range(20):
        app.root.update()
        time.sleep(0.02)

    # 空闲 CPU（对照）
    cpu_start, wall_start = _cpu_seconds(), time.perf_counter()
    while time.perf_counter() - wall_start < 2.0:
        app.root.update()
        time.sleep(0.01)
    idle_cpu = (_cpu_seconds() - cpu_start) / (time.perf_counter() - wall_start) * 100

    samples = []                     # [(和上一个鼠标消息隔了多久, 窗口落后光标)]
    state = {"last": None, "offset": None}

    def on_motion(event):
        now = time.perf_counter()
        gap = (now - state["last"]) if state["last"] is not None else 0.0
        state["last"] = now
        rect = theme.window_rect(app.root)
        if rect is None:
            return
        offset = event.x_root - int(rect[0])
        if state["offset"] is None:
            state["offset"] = offset     # 第一下＝抓取点，之后应该一直不变
            return
        samples.append((gap, abs(offset - state["offset"])))

    app.root.bind("<B1-Motion>", on_motion, add="+")

    beats = []
    beat_state = {"last": time.perf_counter(), "stop": False}

    def beat():
        now = time.perf_counter()
        beats.append(now - beat_state["last"])
        beat_state["last"] = now
        if not beat_state["stop"]:
            app.root.after(BEAT_MS, beat)

    app.root.after(BEAT_MS, beat)
    print("现在用鼠标按住标题栏（或状态栏）拖这个窗口 %.0f 秒（左右晃一晃，别松手）…"
          % DRAG_SECONDS, flush=True)
    deadline = time.time() + DRAG_SECONDS
    while time.time() < deadline:
        app.root.update()
        time.sleep(0.005)
    beat_state["stop"] = True
    app.root.unbind("<B1-Motion>")

    print("\n" + "=" * 58)
    print("空闲 CPU（单核占比）：%.1f%%" % idle_cpu)
    if not samples:
        print("这次没抓到拖动 —— 是不是没拖？再跑一次试试。")
    else:
        gaps = sorted(s[0] for s in samples)
        lags = sorted(s[1] for s in samples)
        print("拖动里的鼠标消息数：%d" % len(samples))
        print("两个消息之间：平均 %.1f ms、中位 %.1f ms、最慢 %.1f ms"
              % (sum(gaps) / len(gaps) * 1000, gaps[len(gaps) // 2] * 1000,
                 gaps[-1] * 1000))
        print("窗口落后光标：平均 %.1f 像素、中位 %.0f、最大 %d"
              % (sum(lags) / len(lags), lags[len(lags) // 2], lags[-1]))
        print("（落后 ≤3 像素＝跟手；几十像素＝明显延时）")
    if beats:
        gaps = sorted(beats)
        print("主线程心跳：平均 %.1f ms、中位 %.1f ms、最慢 %.1f ms、>40ms 有 %d 次"
              % (sum(gaps) / len(gaps) * 1000, gaps[len(gaps) // 2] * 1000,
                 gaps[-1] * 1000, sum(1 for g in gaps if g > 0.04)))
    print("=" * 58)
    try:
        app.root.destroy()
    except Exception:                              # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
