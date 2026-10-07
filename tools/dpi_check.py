"""DPI / 坐标自检：确认"框选的区域"和"程序截到的画面"在同一个坐标系里。

用法：
    python tools/dpi_check.py             # 打印 DPI、Tk 坐标、截图尺寸
    python tools/dpi_check.py --cursor    # 把鼠标周围 400x200 截下来存成 PNG

--cursor 的用法：把鼠标停在游戏聊天框正中间，然后运行它，再打开生成的 PNG。
如果图里正好是鼠标位置附近的画面，说明坐标已经对齐。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import capture, paths                     # noqa: E402

AWARENESS = {
    0: "不感知 DPI（unaware，旧版行为）",
    1: "系统级感知（system aware）",
    2: "按显示器感知（per-monitor aware）",
}


def process_awareness() -> str:
    if sys.platform != "win32":
        return "非 Windows"
    try:
        import ctypes

        value = ctypes.c_int()
        result = ctypes.windll.shcore.GetProcessDpiAwareness(None, ctypes.byref(value))
        if result == 0:
            return AWARENESS.get(value.value, "未知(%d)" % value.value)
    except Exception as exc:
        return "查询失败：%s" % exc
    return "未知"


def system_dpi() -> int:
    if sys.platform != "win32":
        return 96
    try:
        import ctypes

        return int(ctypes.windll.user32.GetDpiForSystem())
    except Exception:
        return 96


def cursor_position():
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        point = POINT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            return point.x, point.y
    except Exception:
        pass
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="DPI 与坐标自检")
    parser.add_argument("--cursor", action="store_true",
                        help="截取鼠标周围一小块区域并存成 PNG")
    parser.add_argument("--size", default="400x200",
                        help="配合 --cursor 使用，默认 400x200")
    args = parser.parse_args()

    print("=" * 62)
    print("DPI / 坐标自检")
    print("=" * 62)
    print("Python：%s" % sys.executable)
    print("进程 DPI 感知：%s" % process_awareness())
    dpi = system_dpi()
    print("系统 DPI：%d（缩放 %.0f%%）" % (dpi, dpi / 96 * 100))

    # 按真实程序的做法开一次 DPI 感知（main.py 就是这么干的，而且必须在建 Tk 之前）。
    # 必须在这里调：程序一旦把主线程改成"不感知"，Tk 报的坐标就会被缩放虚拟化，
    # 于是"框选的区域"和"截到的画面"对不上 —— 这个工具的存在就是为了抓这种情况。
    try:
        from app import dpi as dpi_module
        from app.config import load_config

        mode = str(load_config().get("dpi_mode", "auto"))
        print("按配置开 DPI 感知（dpi_mode=%s）：%s"
              % (mode, dpi_module.enable(mode)))
        print("开完之后进程 DPI 感知：%s" % process_awareness())
    except Exception as exc:                       # noqa: BLE001
        print("开 DPI 感知失败：%s" % exc)

    try:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        tk_dpi = root.winfo_fpixels("1i")
        print("Tk 认为的 DPI：%.0f（缩放 %.0f%%）" % (tk_dpi, tk_dpi / 96 * 100))
        print("Tk 屏幕尺寸：%dx%d" % (root.winfo_screenwidth(),
                                    root.winfo_screenheight()))
        print("Tk scaling：%s" % root.tk.call("tk", "scaling"))
        root.destroy()
    except Exception as exc:
        print("Tk 启动失败：%s" % exc)
        print("界面起不来时先解决这个：Python 安装要包含 tcl/tk。")
        return 1

    full = capture.grab(None)
    # 注意：full.size 是 (宽, 高) 元组，直接塞进 "%s" % 会被当成多个参数（以前这里
    # 会直接抛 TypeError，工具跑一半就断了）。
    print("整屏截图尺寸：%s" % ((full.size,) if full else ("失败",)))

    if args.cursor:
        position = cursor_position()
        if not position:
            print("拿不到鼠标位置")
            return 1
        try:
            width, height = (int(value) for value in args.size.lower().split("x"))
        except Exception:
            width, height = 400, 200
        x, y = position
        box = [x - width // 2, y - height // 2, x + width // 2, y + height // 2]
        print("\n鼠标位置：%s" % (position,))
        print("截取区域：%s" % (box,))
        image = capture.grab(box)
        if image is None:
            print("截图失败")
            return 1
        aligned = image.size == (width, height)
        print("实际拿到：%dx%d（期望 %dx%d）%s"
              % (image.width, image.height, width, height,
                 "  尺寸一致" if aligned else "  尺寸不一致，坐标没对齐"))
        paths.ensure_dirs()
        out = paths.DATA_DIR / ("dpi_test_%s.png" % time.strftime("%H%M%S"))
        image.save(out)
        print("已保存：%s" % out)
        print("打开这张图看看：里面是不是鼠标位置附近那一小块画面。")

    print("\n判断标准：")
    print("- 正常应当是「按显示器感知」，并且 Tk 屏幕尺寸 == 整屏截图尺寸。")
    print("- 如果 Tk 屏幕尺寸比截图尺寸小（例如 1536x864 对 1920x1080），")
    print("  说明坐标仍被缩放虚拟化：把 data/config.json 的 dpi_mode 设成 auto")
    print("  并重启；仍然不行就设成 legacy 再试（两种都试一下，哪个准用哪个）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
