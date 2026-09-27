"""截图 / 坐标取证：一条命令把"框选区域 vs 实际截图"的差异量出来。

用法：
    python tools/capture_probe.py                  # 用配置里已保存的区域
    python tools/capture_probe.py --region 400,700,1400,1000

它会：
  1. 打印 Tk 认为的屏幕尺寸、截图实际能看到的像素空间、两者比例；
  2. 抓整屏，在图上用红框标出「你框选的区域」、用黄框标出「换算后的区域」；
  3. 把整屏图和区域图存到 data/ 下，直接用看图软件打开就能判断。

把打印出来的数字（尤其是"截图空间"和"比例"两行）发出来就能定位问题。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import capture, paths                     # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="截图坐标取证")
    parser.add_argument("--region", help="x1,y1,x2,y2；不填就用配置里的")
    args = parser.parse_args()

    print("=" * 62)
    print("截图 / 坐标取证")
    print("=" * 62)

    tk_screen = None
    try:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        tk_screen = (root.winfo_screenwidth(), root.winfo_screenheight())
        print("Tk 屏幕尺寸        ：%dx%d" % tk_screen)
        print("Tk DPI / scaling   ：%.0f / %s"
              % (root.winfo_fpixels("1i"), root.tk.call("tk", "scaling")))
        root.destroy()
    except Exception as exc:
        print("Tk 启动失败（不影响下面的截图测试）：%s" % exc)

    space = capture.capture_space()
    print("截图空间（SM_CX/CY）：%s" % ("%dx%d" % space if space else "未知"))
    scale = capture.measure_scale(tk_screen, force=True)
    measured = capture.scale_report(tk_screen)
    print("实测整屏截图        ：%s" % (measured.get("measured_capture(实测整屏)"),))
    print("换算比例（实测）    ：x%.3f, y%.3f  %s"
          % (scale[0], scale[1],
             "（一致，无需换算）" if scale == (1.0, 1.0) else "（不一致，已自动换算）"))
    estimate = capture.estimated_scale(tk_screen)
    if estimate != scale:
        print("换算比例（估算）    ：x%.3f, y%.3f（仅作对比）" % estimate)

    region = None
    if args.region:
        try:
            region = [int(value) for value in args.region.split(",")]
        except Exception:
            print("--region 格式不对，应形如 400,700,1400,1000")
            return 1
    if not region:
        from app.config import load_config

        region = load_config().get("region")
    if not region:
        print("没有区域可测：先在程序里框选，或用 --region 指定")
        return 1

    print("\n框选区域（Tk 坐标）：%s  宽%d 高%d"
          % (region, region[2] - region[0], region[3] - region[1]))
    converted = capture.convert_region(region, tk_screen)
    print("换算后区域（截图坐标）：%s  宽%d 高%d"
          % (converted, converted[2] - converted[0], converted[3] - converted[1]))

    full = capture.grab_full()
    if full is None:
        print("整屏截图失败")
        return 1
    print("整屏截图尺寸：%dx%d" % full.size)

    paths.ensure_dirs()
    image = capture.grab(region, tk_screen)
    if image is None:
        print("区域截图失败")
        return 1
    print("区域截图尺寸：%dx%d（期望 %dx%d）%s"
          % (image.width, image.height,
             converted[2] - converted[0], converted[3] - converted[1],
             " 一致" if image.size == (converted[2] - converted[0],
                                     converted[3] - converted[1]) else " 不一致！"))

    try:
        from PIL import ImageDraw

        overlay = full.copy()
        draw = ImageDraw.Draw(overlay)
        draw.rectangle([region[0], region[1], region[2], region[3]],
                       outline=(255, 0, 0), width=4)
        draw.rectangle([converted[0], converted[1], converted[2], converted[3]],
                       outline=(255, 220, 0), width=2)
        overlay_path = paths.DATA_DIR / "probe_overlay.png"
        overlay.save(overlay_path)
        print("\n已保存整屏标注图：%s" % overlay_path)
        print("  红框 = 你框选的坐标，黄框 = 换算后实际截取的位置")
        print("  打开它：红框应该正好套住游戏聊天框；如果黄框才套住，说明换算生效了")
    except Exception as exc:
        print("画标注图失败：%s" % exc)

    region_path = paths.DATA_DIR / "probe_region.png"
    image.save(region_path)
    print("已保存区域截图：%s" % region_path)
    print("  这张图应该正好是聊天框里的内容")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
