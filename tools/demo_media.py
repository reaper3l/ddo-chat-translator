"""作者侧工具：生成演示素材（截图 + 动图），用来做宣传 / 发群。

用法（需要有图形界面的机器）：
    python tools\\demo_media.py [输出目录] [--size 560x620] [--fps 4]
    python tools\\demo_media.py --tour          # 录「新手教学」每一步

它会真的把程序窗口打开，放一遍「演示一下」里的那段示例聊天（或走一遍新手教学），
同时把**主窗口**区域截下来：

    演示_主窗口.png / 演示_主窗口.gif
    教学_步骤1.png … 教学_步骤N.png（--tour 时每一步一张）

注意：
* 窗口大小默认取 560x620 —— 比日常用的紧凑尺寸大一点，发到群里看得清；
  想按你平时的样子录就传 --size（例如 `--size 343x295`）。
* **不会动你的配置和学习库**：演示只往显示区画（不联网、不写学习库），
  这个工具也不走 quit_app()，所以不会回写窗口位置之类的配置。
* 教学现在是"主窗口内部翻一页"（不再有金框/说明卡片那些浮窗），所以录教学就是录主窗口
  —— 以前那套"每个窗口单独抓再拼到干净底色上"的复杂逻辑跟着删掉了。
* 录之前**把别的窗口最小化**（录制期间别动鼠标）：录的是主窗口那一块屏幕，
  别的窗口压在上面就会一起录进去。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import capture                      # noqa: E402
from app.ui import theme                     # noqa: E402
from app.ui.main_window import MainWindow    # noqa: E402


def _ensure_dpi_aware() -> None:
    """先设好 DPI 感知，跟程序本体一致 —— 不然窗口坐标和截图像素会差一个缩放，
    录出来的画面就会偏一格（这个坑实测踩过）。"""
    try:
        from app import dpi
        from app.config import load_config

        dpi.enable(str(load_config().get("dpi_mode", "auto")))
    except Exception:                            # noqa: BLE001
        pass


def _window_box(app, screen):
    """主窗口在屏幕上的**物理像素**框 [左,上,右,下]。

    直接走 `theme.window_rect()`：它就是"用 Win32 量、临时按物理像素报、用完还原"
    那一套（这个工具以前自己写了一份，收尾还把线程写死成"不感知 DPI"）。
    """
    rect = theme.window_rect(app.root)
    if rect:
        return [int(value) for value in rect]
    left = app.root.winfo_rootx()                # 退路：按 Tk 的值换算（可能偏一点）
    top = app.root.winfo_rooty()
    width = max(80, app.root.winfo_width())
    height = max(80, app.root.winfo_height())
    return capture.convert_region([left, top, left + width, top + height], screen)


def _keep_on_screen(app) -> None:
    """主窗口跑到屏幕外就挪回屏幕里（只挪位置、不改大小）—— 不然抓出来就是"缺一块"。"""
    area = capture.virtual_screen_rect()
    if not area:
        return
    left, top, right, bottom = area
    try:
        rect = theme.window_rect(app.root)
        if not rect:
            return
        width = max(1, rect[2] - rect[0])
        height = max(1, rect[3] - rect[1])
        x = max(left, min(rect[0], right - width))
        y = max(top, min(rect[1], bottom - height))
        if (x, y) != (rect[0], rect[1]):
            app.root.wm_geometry("+%d+%d" % (x, y))       # 只挪位置，不动大小
    except Exception:                            # noqa: BLE001
        pass


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
                        help="录「新手教学」（一步一步怎么设置）而不是示例聊天")
    parser.add_argument("--tour-step", type=float, default=3.0,
                        help="教学每一步停留几秒（默认 3）")
    args = parser.parse_args()

    out_dir = Path(args.out) if args.out else ROOT.parent / "work" / "demo"
    out_dir.mkdir(parents=True, exist_ok=True)

    app = MainWindow()
    # 别让"检查更新 / 参与改进邀请"这类弹窗闯进画面（只改内存，不落盘）
    app.config["check_update"] = False
    app.config["contribute_invite_done"] = True
    app.config["contribute_invite_version"] = "demo"
    app.config["tour_done"] = True               # 教学由本工具手动放，别自动弹
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

    def snap() -> None:
        """抓一帧主窗口（教学现在也在主窗口里面，所以只录主窗口就够）。"""
        nonlocal shot
        _keep_on_screen(app)
        box = _window_box(app, screen)
        image = capture.grab_fast(box, None) or capture.grab_dxgi(box)
        if image is None:
            return
        frames.append(_trim_dead_border(image))
        shot = frames[-1]

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
        step_last = {}
        # 按"一步一录"来，别用计时循环 —— 那样录到哪一步全看机器快慢（实测会漏掉后半段）
        for step_index in range(steps):
            for _ in range(6):                     # 换步后先等窗口画好再抓
                app.root.update()
                time.sleep(0.12)
            for _ in range(per_step):
                app.root.update()
                time.sleep(0.2)
                app.root.update()
                snap()
                time.sleep(interval)
            if frames:
                step_last[step_index] = frames[-1]
            if step_index + 1 < steps:
                try:
                    tour._go_next()
                except Exception:                  # noqa: BLE001
                    break
        # 每一步单独存一张图（发群时按序号发，比挤在一张里清楚得多）
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
        deadline = time.time() + 60
        while time.time() < deadline:
            app.root.update()
            snap()
            if not getattr(app, "_demo_timer", None):
                break                           # 演示放完了
            time.sleep(interval)
        # 结尾再补两帧，让动图停一下（别一放完就跳走）
        for _ in range(2):
            app.root.update()
            snap()
            time.sleep(0.3)

    app.stop_demo()
    if shot is None:
        print("一帧都没抓到 —— 窗口是不是被挡住了？")
        app.root.destroy()
        return 1

    png_path = out_dir / ("%s_主窗口.png" % prefix)
    shot.save(png_path)
    print("截图：%s（%dx%d）" % (png_path, shot.width, shot.height))

    if frames:
        from PIL import Image

        gif_path = out_dir / ("%s_主窗口.gif" % prefix)
        # 缩小 + 256 色，压到能直接发群（不然十几秒的动图要好几 MB）
        scale = max(0.2, min(1.0, float(args.gif_scale)))
        # 每一步的窗口大小可能不一样 → 先统一到同一张画布（左上对齐），否则动图会花
        width = max(frame.width for frame in frames)
        height = max(frame.height for frame in frames)
        gif_frames = []
        for frame in frames:
            canvas = Image.new("RGB", (width, height), (12, 14, 20))
            canvas.paste(frame, (0, 0))
            if scale < 1.0:
                canvas = canvas.resize(
                    (max(1, int(canvas.width * scale)), max(1, int(canvas.height * scale))),
                    Image.LANCZOS)
            gif_frames.append(canvas.convert("P", palette=Image.ADAPTIVE, colors=256))
        gif_frames[0].save(str(gif_path), save_all=True,
                           append_images=gif_frames[1:],
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
