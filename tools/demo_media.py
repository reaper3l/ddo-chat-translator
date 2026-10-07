"""作者侧工具：生成演示素材（截图 + 动图），用来做宣传 / 发群。

用法（需要有图形界面的机器）：
    python tools\\demo_media.py [输出目录] [--size 560x620] [--fps 4]

它会真的把程序窗口打开，放一遍「演示一下」里的那段示例聊天，同时把窗口区域截下来：

    演示_主窗口.png    最后一屏（内容最满）的静态图
    演示_主窗口.gif    整个过程（约十几秒）的动图

注意：
* 窗口大小默认取 560x620 —— 比日常用的紧凑尺寸大一点，发到群里看得清；
  想按你平时的样子录就传 --size（例如 `--size 343x295`）。
* **不会动你的配置和学习库**：演示只往显示区画（不联网、不写学习库），
  这个工具也不走 quit_app()，所以不会回写窗口位置之类的配置。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import capture                      # noqa: E402
from app.ui.main_window import MainWindow    # noqa: E402


def _ensure_dpi_aware():
    """先设好 DPI 感知，跟程序本体一致 —— 不然窗口坐标和截图像素会差一个缩放，
    录出来的画面就会偏一格（这个坑实测踩过）。"""
    try:
        from app import dpi
        from app.config import load_config

        dpi.enable(str(load_config().get("dpi_mode", "auto")))
    except Exception:                            # noqa: BLE001
        pass


def _window_box(app, screen):
    """窗口在屏幕上的**物理像素**框 [左,上,右,下]。

    坑（实测踩过）：Tk 报的坐标是按显示缩放**虚拟化**过的（125% 下 560 会报成 560
    但实际是 700 像素），而框选区域的坐标又走另一套换算 —— 直接拿 `winfo_*` 去乘
    屏幕比例，抓出来的画面会整整偏一格、还把旁边的桌面也录进去。所以这里用 Win32
    的 `GetWindowRect`，并且**先把本线程设成"按物理像素报"**（DPI 感知上下文 -4）：
    实测拿到的矩形和逐像素扫描出来的窗口边缘完全一致。
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    try:
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        rect = wintypes.RECT()
        if user32.GetWindowRect(app.root.winfo_id(), ctypes.byref(rect)):
            return [rect.left, rect.top, rect.right, rect.bottom]
    except Exception:                              # noqa: BLE001
        pass
    finally:
        try:
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-1))
        except Exception:                          # noqa: BLE001
            pass
    # 退路：按 Tk 的值乘屏幕比例（可能偏一点，但总比不录强）
    left = app.root.winfo_rootx()
    top = app.root.winfo_rooty()
    width = max(80, app.root.winfo_width())
    height = max(80, app.root.winfo_height())
    return capture.convert_region([left, top, left + width, top + height], screen)


def _rect_of(widget):
    """任意窗口的**物理像素**矩形（和 `_window_box` 同一套办法）。"""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    try:
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        rect = wintypes.RECT()
        if user32.GetWindowRect(widget.winfo_id(), ctypes.byref(rect)):
            return [rect.left, rect.top, rect.right, rect.bottom]
    except Exception:                              # noqa: BLE001
        pass
    finally:
        try:
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-1))
        except Exception:                          # noqa: BLE001
            pass
    try:
        return [widget.winfo_rootx(), widget.winfo_rooty(),
                widget.winfo_rootx() + widget.winfo_width(),
                widget.winfo_rooty() + widget.winfo_height()]
    except Exception:                              # noqa: BLE001
        return None


def _toplevels(widget):
    """递归找出所有"真窗口"（教程里的设置页挂在"设置中心"下面，不是主窗口的直接子窗口）。

    只认 master 是 主窗口 / 别的真窗口 的那些；**鼠标悬停提示窗会被跳过** ——
    它是以某个按钮为父建的，录进去就是一块莫名其妙的白条（实测在成图里出现过）。
    """
    import tkinter as tk

    found = []
    for child in widget.winfo_children():
        if isinstance(child, tk.Toplevel) and isinstance(
                getattr(child, "master", None), (tk.Tk, tk.Toplevel)):
            found.append(child)
        found.extend(_toplevels(child))
    return found


def _app_box(app):
    """把程序当前所有可见窗口圈在一起的框（教学要连设置页一起录进去）。

    结果会**夹在屏幕范围内**：超出屏幕的部分抓出来是黑块/残影（用户反馈的"撕裂"），
    而窗口跑到屏幕外也会在图里被切掉（"显示不全"）。
    """
    boxes = []
    for window in [app.root] + _toplevels(app.root):
        try:
            if not window.winfo_ismapped():
                continue
            rect = _rect_of(window)
            if rect:
                boxes.append(rect)
        except Exception:                          # noqa: BLE001
            continue
    if not boxes:
        return _window_box(app, (0, 0))
    box = [min(b[0] for b in boxes), min(b[1] for b in boxes),
           max(b[2] for b in boxes), max(b[3] for b in boxes)]
    area = capture.virtual_screen_rect()
    if area:
        box = [max(box[0], area[0]), max(box[1], area[1]),
               min(box[2], area[2]), min(box[3], area[3])]
    return box


def _keep_windows_on_screen(app) -> None:
    """把已经跑到屏幕外的窗口挪回屏幕里（只挪位置、不改大小）。

    为什么要它：设置页/说明卡片有时会被摆在屏幕边缘之外，抓出来就是"缺一块"。
    """
    area = capture.virtual_screen_rect()
    if not area:
        return
    left, top, right, bottom = area
    for window in [app.root] + _toplevels(app.root):
        try:
            if not window.winfo_ismapped():
                continue
            rect = _rect_of(window)
            if not rect:
                continue
            width = max(1, rect[2] - rect[0])
            height = max(1, rect[3] - rect[1])
            x = max(left, min(rect[0], right - width))
            y = max(top, min(rect[1], bottom - height))
            if (x, y) != (rect[0], rect[1]):
                window.wm_geometry("+%d+%d" % (x, y))     # 只挪位置，不动大小
        except Exception:                          # noqa: BLE001
            continue


def _app_windows(app):
    """程序当前可见的窗口及它们的物理矩形：[(是不是高亮框, [左,上,右,下]), ...]。"""
    from app.ui.tour import SPOT_KEY                 # noqa: F401  （只为确认模块在）

    found = []
    for window in [app.root] + _toplevels(app.root):
        try:
            if not window.winfo_ismapped():
                continue
            rect = _rect_of(window)
            if not rect:
                continue
            is_spot = False
            try:
                is_spot = str(window.attributes("-transparentcolor")) == SPOT_KEY
            except Exception:                        # noqa: BLE001
                is_spot = False
            found.append((is_spot, rect))
        except Exception:                            # noqa: BLE001
            continue
    return found


def _compose_on_clean_background(screen_image, box, windows,
                                 background=(12, 14, 20)):
    """把"程序自己的窗口"拼到干净底色上：每个窗口**单独抓**，再按它们的真实相对位置贴。

    为什么不整块抓：教学同时开着主窗口 / 设置页 / 说明卡片，整块抓外接矩形会把中间的
    桌面录进去（用户的桌面上还有别的软件），而且窗口一挪位置，框就跟不上 ——
    结果是"图片显示不全 + 撕裂"（用户实测反馈的正是这个）。
    单独抓每个窗口则永远完整；透明的高亮框抓不了（它背后是桌面），按它的矩形画一圈金框。
    """
    from PIL import Image, ImageDraw

    # 先量出"这些窗口合起来占多大"，再开一张这么大的干净底
    lefts = [rect[0] for _s, rect in windows] or [0]
    tops = [rect[1] for _s, rect in windows] or [0]
    rights = [rect[2] for _s, rect in windows] or [1]
    bottoms = [rect[3] for _s, rect in windows] or [1]
    origin = (min(lefts), min(tops))
    size = (max(1, max(rights) - origin[0]), max(1, max(bottoms) - origin[1]))
    canvas = Image.new("RGB", size, background)

    # 先把所有窗口贴好，**最后**再画高亮框 —— 高亮框是"透明窗"，被后面贴的窗口
    # 盖住就看不见了（顺序反了金框会消失）
    ordered = [item for item in windows if not item[0]] + \
              [item for item in windows if item[0]]
    for is_spot, rect in ordered:
        dx, dy = rect[0] - origin[0], rect[1] - origin[1]
        if is_spot:                                  # 高亮框：只画边框
            ImageDraw.Draw(canvas).rectangle(
                [dx + 3, dy + 3, dx + (rect[2] - rect[0]) - 3,
                 dy + (rect[3] - rect[1]) - 3],
                outline=(255, 204, 77), width=3)
            continue
        piece = capture.grab_fast(rect, None)        # 这个窗口自己那块，永远完整
        if piece is None:
            piece = capture.grab_dxgi(rect)
        if piece is None:
            continue
        canvas.paste(piece, (dx, dy))
    return canvas


def _trim_dead_border(image, limit: int = 0):
    """裁掉贴着边缘那一条"没画到东西"的黑边。

    为什么要它：窗口被系统按显示缩放拉伸时，底部/右边偶尔会多出几十像素没画到内容
    （纯黑）。发群之前把这条黑边去掉，图看着干净。

    limit=0 表示自动（每一边最多裁掉 35%）—— 窗口本身没有纯黑的区域（底色是
    #12141c），所以裁掉的一定是"没画到东西"的部分。
    """
    import numpy as np

    if limit <= 0:
        limit = max(40, int(image.height * 0.35))
    pixels = np.asarray(image.convert("RGB"), dtype=np.int16)
    filled = pixels.sum(axis=2) > 24              # 窗口底色是 #12141c，不会被误判成黑
    rows = np.nonzero(filled.any(axis=1))[0]
    cols = np.nonzero(filled.any(axis=0))[0]
    if not len(rows) or not len(cols):
        return image
    top, bottom = int(rows[0]), int(rows[-1]) + 1
    left, right = int(cols[0]), int(cols[-1]) + 1
    # 每一边最多裁掉 limit 像素（中间本来就黑的区域绝不动）
    top = min(top, limit)
    left = min(left, limit)
    bottom = image.height - min(image.height - bottom, limit)
    right = image.width - min(image.width - right, limit)
    if (top, left, bottom, right) == (0, 0, image.height, image.width):
        return image
    return image.crop((left, top, right, bottom))


def main() -> int:
    _ensure_dpi_aware()
    parser = argparse.ArgumentParser()
    parser.add_argument("out", nargs="?", help="输出目录（默认 work\\demo）")
    parser.add_argument("--size", default="560x620", help="录制时的窗口大小，如 560x620")
    parser.add_argument("--fps", type=float, default=3.0, help="动图帧率（默认 3）")
    parser.add_argument("--gif-scale", type=float, default=0.6,
                        help="动图缩小到多少（默认 0.6，发群有大小限制）")
    parser.add_argument("--tour", action="store_true",
                        help="录「新手教学」（分步高亮 + 怎么设置）而不是示例聊天")
    parser.add_argument("--tour-step", type=float, default=4.0,
                        help="教学每一步停留几秒（默认 4）")
    args = parser.parse_args()

    out_dir = Path(args.out) if args.out else ROOT.parent / "work" / "demo"
    out_dir.mkdir(parents=True, exist_ok=True)

    app = MainWindow()
    # 别让"检查更新 / 参与改进邀请"这类弹窗闯进画面（只改内存，不落盘）
    app.config["check_update"] = False
    app.config["contribute_invite_done"] = True
    app.config["contribute_invite_version"] = "demo"
    app.root.geometry(args.size)
    app.root.update()
    app.root.deiconify()
    app.root.lift()
    app.root.update_idletasks()

    screen = (app.root.winfo_screenwidth(), app.root.winfo_screenheight())
    capture.measure_scale(screen, force=True)
    interval = 1.0 / max(1.0, float(args.fps))

    frames = []
    shot = None
    base = None            # 固定坐标系原点：整个录制过程都用它，画面才不会跳
    canvas_size = [0, 0]

    def snap(box_now, windows_now):
        """抓一帧（每个窗口单独抓再拼）。box_now 只用来保证坐标系不跳。"""
        nonlocal shot, base, canvas_size
        if base is None:
            base = (box_now[0], box_now[1])
        image = _compose_on_clean_background(None, None, windows_now)
        canvas_size[0] = max(canvas_size[0], image.width)
        canvas_size[1] = max(canvas_size[1], image.height)
        frames.append(image)
        shot = image

    prefix = "教学" if args.tour else "演示"
    if args.tour:
        print("正在放「新手教学」并录制（每一步停 %.1f 秒，请别动鼠标…）"
              % max(1.0, args.tour_step))
        app.open_tour()
        tour = getattr(app, "_tour", None)
        if tour is None:
            print("教学没开起来（是不是正在监听？先停下再录）")
            app.root.destroy()
            return 1
        steps = len(tour._steps())
        per_step = max(2, int(round(max(1.0, args.tour_step) / max(0.05, interval))))
        step_last = {}      # 每一步"最后那一帧"（等窗口画稳再抓，不带白块）
        # 按"一步一录"来，别用计时循环 —— 那样录到哪一步全看机器快慢（实测会漏掉后半段）
        for step_index in range(steps):
            # 换步后**先等窗口画好再抓**：新开的窗口（设置页 / 互译窗口）在被画出来之前
            # 是一片白的，抢着抓就会在成图里留一条白条，看着像撕裂（用户实测反馈过）
            for _ in range(8):
                app.root.update()
                time.sleep(0.12)
            _keep_windows_on_screen(app)
            app.root.update()
            for _ in range(per_step):
                app.root.update()
                _keep_windows_on_screen(app)       # 先保证窗口都在屏幕里，不然会被切
                # 说明卡片是"延后 40ms 再摆一次"才定下来的（见 tour._point_at），
                # 这里等它落位再抓 —— 边挪边抓会抓到"撕开"的半张图
                time.sleep(0.2)
                app.root.update()
                snap(_app_box(app), _app_windows(app))
                time.sleep(interval)
            if frames:
                step_last[step_index] = frames[-1]
            if step_index + 1 < steps:
                try:
                    tour._go_next()
                except Exception:                  # noqa: BLE001
                    break
        # 每一步单独存一张图（发群时按 1~6 顺序发，比挤在一张里清楚得多）
        for index, image in sorted(step_last.items()):
            path = out_dir / ("教学_步骤%d.png" % (index + 1))
            image.save(path)
            print("教学第 %d 步：%s（%dx%d）"
                  % (index + 1, path.name, image.width, image.height))
        if step_last:
            shot = step_last[max(step_last)]
    else:
        print("正在放演示并录制（约十几秒，请别动鼠标…）")
        app.play_demo()
        deadline = time.time() + 45
        while time.time() < deadline:
            app.root.update()
            _keep_windows_on_screen(app)
            box = _window_box(app, screen)      # 演示模式只录主窗口
            screen_image = capture.grab_fast(box, None) or capture.grab_dxgi(box)
            if screen_image is not None:
                if base is None:
                    base = (box[0], box[1])
                canvas_size[0] = max(canvas_size[0], box[2] - base[0])
                canvas_size[1] = max(canvas_size[1], box[3] - base[1])
                frames.append(_trim_dead_border(screen_image))
                shot = frames[-1]
            if not getattr(app, "_demo_timer", None):
                break                           # 演示放完了
            time.sleep(interval)
    if not args.tour:
        # 演示模式：再补最后两帧，让动图结尾停一下（别一放完就跳走）。
        # 注意只在演示模式补 —— 教学模式下这两帧只有主窗口，补进去会让动图结尾
        # 突然"跳"成一张小图（看着就是撕裂）。
        for _ in range(2):
            app.root.update()
            image = capture.grab_fast(_window_box(app, screen), None)
            if image is not None:
                frames.append(_trim_dead_border(image))
                shot = frames[-1]
            time.sleep(0.3)

    app.stop_demo()
    if shot is None:
        print("一帧都没抓到 —— 窗口是不是被挡住了？")
        app.root.destroy()
        return 1

    png_path = out_dir / ("%s_主窗口.png" % prefix)
    # 教学模式的 shot 已经在上面按"每一步最后那一帧"取好了（步骤 6 那张最全）
    shot.save(png_path)
    print("截图：%s（%dx%d）" % (png_path, shot.width, shot.height))

    gif_path = out_dir / ("%s_主窗口.gif" % prefix)
    # 缩小 + 256 色，压到能直接发群（不然 700x775 的十几秒动图要好几 MB）
    scale = max(0.2, min(1.0, float(args.gif_scale)))
    # 教学每一步的窗口不一样大 → 先统一到同一张画布（左上对齐），否则动图会花
    if frames:
        from PIL import Image

        width = max(frame.width for frame in frames)
        height = max(frame.height for frame in frames)
        unified = []
        for frame in frames:
            canvas = Image.new("RGB", (width, height), (12, 14, 20))
            canvas.paste(frame, (0, 0))
            unified.append(canvas)
        frames = unified
    gif_frames = []
    for frame in frames:
        size = (max(1, int(frame.width * scale)), max(1, int(frame.height * scale)))
        gif_frames.append(frame.resize(size).convert("P", palette=1, colors=128))
    if gif_frames:
        gif_frames[0].save(gif_path, save_all=True, append_images=gif_frames[1:],
                           duration=int(1000 * interval), loop=0, optimize=True)
        print("动图：%s（%d 帧，%.1f MB，%dx%d）"
              % (gif_path, len(gif_frames),
                 gif_path.stat().st_size / 1048576.0,
                 gif_frames[0].width, gif_frames[0].height))

    app.root.destroy()
    print("完成。发群之前先自己看一眼这两张（尤其动图有没有把别的东西录进去）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
