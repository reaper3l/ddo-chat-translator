"""拖动诊断：量"拖着窗口走的时候，窗口落后光标多少像素、每个鼠标消息处理多久"。

用法（源码运行的同学）：

    python tools\\drag_check.py

窗口打开后，**用鼠标按住标题栏拖它 10 秒左右**（左右晃一晃），不要松手；
时间到了程序自己会打印结果。看两个数：

* **落后像素**：0~3 像素 = 完全跟手；几十像素 = 明显跟不上（就是"延时很高"）；
* **每个事件耗时**：正常 1~5ms；经常几十毫秒 = UI 线程被别的东西堵住了。

顺带会测一遍"没拖动时的空闲 CPU"，用来对照。
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

    samples = []
    original_drag = app.frameless._on_drag      # noqa: SLF001  诊断就是要包它

    def wrapped(event):
        started = time.perf_counter()
        original_drag(event)
        elapsed = (time.perf_counter() - started) * 1000.0
        try:
            window = theme.window_rect(app.root) or (0, 0, 0, 0)
            cursor_x, cursor_y = _cursor()
            lag_x = abs(cursor_x - window[0])
            lag_y = abs(cursor_y - window[1])
        except Exception:                          # noqa: BLE001
            lag_x = lag_y = 0
        samples.append((elapsed, lag_x, lag_y))

    app.frameless._on_drag = wrapped            # noqa: SLF001
    print("现在用鼠标按住标题栏拖这个窗口 %.0f 秒（左右晃一晃，别松手）…"
          % DRAG_SECONDS, flush=True)
    deadline = time.time() + DRAG_SECONDS
    while time.time() < deadline:
        app.root.update()
        time.sleep(0.005)
    app.frameless._on_drag = original_drag      # noqa: SLF001

    print("\n" + "=" * 58)
    print("空闲 CPU（单核占比）：%.1f%%" % idle_cpu)
    if not samples:
        print("这次没抓到拖动事件 —— 是不是没拖？再跑一次试试。")
    else:
        costs = sorted(s[0] for s in samples)
        lags = sorted(max(s[1], s[2]) for s in samples)
        print("拖动事件数：%d" % len(samples))
        print("每个事件处理耗时：平均 %.1f ms、中位 %.1f ms、最慢 %.1f ms"
              % (sum(costs) / len(costs), costs[len(costs) // 2], costs[-1]))
        print("窗口落后光标：平均 %.1f 像素、中位 %.1f、最大 %.1f"
              % (sum(lags) / len(lags), lags[len(lags) // 2], lags[-1]))
        print("（落后 ≤3 像素＝跟手；几十像素＝明显延时）")
    print("=" * 58)
    try:
        app.root.destroy()
    except Exception:                              # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
