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
    parser = argparse.ArgumentParser()
    parser.add_argument("out", nargs="?", help="输出目录（默认 work\\demo）")
    parser.add_argument("--size", default="560x620", help="录制时的窗口大小，如 560x620")
    parser.add_argument("--fps", type=float, default=3.0, help="动图帧率（默认 3）")
    parser.add_argument("--gif-scale", type=float, default=0.6,
                        help="动图缩小到多少（默认 0.6，发群有大小限制）")
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
    print("正在放演示并录制（约十几秒，请别动鼠标…）")
    app.play_demo()
    deadline = time.time() + 45
    while time.time() < deadline:
        app.root.update()
        box = _window_box(app, screen)
        image = capture.grab_fast(box, None)          # box 已经是物理像素，别再换算
        if image is None:
            image = capture.grab_dxgi(box)
        if image is not None:
            frames.append(_trim_dead_border(image))
            shot = frames[-1]
        if not getattr(app, "_demo_timer", None):
            break                                   # 演示放完了
        time.sleep(interval)
    # 再补最后两帧，让动图结尾停一下（别一放完就跳走）
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

    png_path = out_dir / "演示_主窗口.png"
    shot.save(png_path)
    print("截图：%s（%dx%d）" % (png_path, shot.width, shot.height))

    gif_path = out_dir / "演示_主窗口.gif"
    # 缩小 + 256 色，压到能直接发群（不然 700x775 的十几秒动图要好几 MB）
    scale = max(0.2, min(1.0, float(args.gif_scale)))
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
