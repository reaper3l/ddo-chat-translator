"""窗口自检（需要图形界面，会短暂显示窗口）：焦点 + 工具条收起动画。

用法（**需要有图形界面的机器**，会短暂显示窗口）：
    python tools\\window_check.py

两部分：
1. **焦点**（用户实测反馈：中译英窗口有时候点回去打不了字）：原因是
   `focus_set()` 在窗口还没映射时静默无效，而 overrideredirect（无边框）窗口
   Windows 不保证点它就给它键盘焦点。修复在 `app/ui/theme.py` 的
   `install_dialog_focus()` 里；这里把几种情况真实跑一遍。
2. **工具条收起/展开的过渡动画**：宽度应该是逐帧变化的，而不是一步到位。

注意：这个检查**不能放进 ui_smoke**，因为 ui_smoke 会把主窗口 withdraw()，
主窗口不显示时对话框根本不映射，Tk 的焦点命令也就无从生效。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.ui import theme                    # noqa: E402
from app.ui.cn2en import CnToEnDialog       # noqa: E402
from app.ui.main_window import MainWindow   # noqa: E402

RESULTS = []


def pump(app, seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.root.update()
        time.sleep(0.02)


def check(name: str, condition: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(condition)))
    print("  %s  %s%s" % ("ok  " if condition else "FAIL", name,
                          ("：" + detail) if detail else ""))


def main() -> int:
    print("=" * 62)
    print("窗口自检：焦点 + 工具条收起动画（会短暂显示窗口）")
    print("=" * 62)

    app = MainWindow()
    app.root.deiconify()            # 真实使用时主窗口是显示的
    app.root.geometry("+40+40")
    app.root.update()

    dialog = CnToEnDialog(app)
    pump(app, 0.9)                  # 等 after(80)/after(400) 的自动聚焦跑完

    check("打开中译英后，焦点在输入框上",
          dialog.window.focus_get() is dialog.input,
          "当前焦点 %r" % (dialog.window.focus_get(),))

    app.root.focus_force()          # 模拟"焦点跑到游戏/别的窗口去了"
    pump(app, 0.2)
    check("焦点被别处抢走后，确实不在对话框里",
          dialog.window.focus_get() is not dialog.input)

    # when="tail" = 交给事件循环处理，和真实点击一样
    dialog.window.event_generate("<Button-1>", x=200, y=300, when="tail")
    pump(app, 0.3)
    check("点回对话框后，焦点回到输入框",
          dialog.window.focus_get() is dialog.input,
          "当前焦点 %r" % (dialog.window.focus_get(),))

    dialog.input.focus_force()
    dialog.input.delete("1.0", "end")
    dialog.input.insert("end", "马上到")
    check("输入框真的能输入文字",
          dialog.input.get("1.0", "end").strip() == "马上到")

    dialog.output.event_generate("<Button-1>", x=10, y=10)
    pump(app, 0.3)
    check("点输出框时焦点给输出框（方便框选复制）",
          dialog.window.focus_get() is dialog.output)

    settings = None
    try:
        from app.ui.settings import SettingsDialog

        settings = SettingsDialog(app)
        pump(app, 0.6)
        # 设置中心的分类页是独立小窗口，这里打开"翻译"页看看有没有输入控件
        page = settings.open_category("翻译")
        pump(app, 0.6)
        target = theme.find_first_input(page)
        check("设置 → 翻译页有可聚焦的输入控件", target is not None,
              "找到 %r" % (target,))
        page.destroy()

        correction = None
        from app.ui.learn import CorrectionDialog

        correction = CorrectionDialog(app, "elite right?", "精英难度对吧？")
        pump(app, 0.6)
        check("纠错窗口的焦点在「改成正确的中文」输入框，而不是只读原文框",
              correction.window.focus_get() is correction.edit,
              "当前焦点 %r" % (correction.window.focus_get(),))
        correction.window.destroy()
    except Exception as exc:
        check("设置/纠错窗口焦点检查", False, str(exc))
    finally:
        for window in (settings.window if settings else None, dialog.window):
            try:
                if window is not None:
                    window.destroy()
            except Exception:
                pass

    # ---------------- 工具条收起/展开的过渡动画 ----------------
    app.config["ui_animation"] = True
    app.config["toolbar_collapsed"] = False
    app._apply_toolbar_collapsed(animate=False)
    pump(app, 0.2)
    expanded = app.actions_holder.winfo_width()
    check("展开时功能按钮占着位置", expanded > 40, "%d px" % expanded)

    app.config["toolbar_collapsed"] = True
    app._apply_toolbar_collapsed(animate=True)
    samples = []
    for _ in range(6):
        app.root.update()
        samples.append(app.actions_holder.winfo_width())
        time.sleep(0.02)
    pump(app, 0.4)
    actions_end = app.actions_holder.winfo_width()
    strip_end = app.strip_holder.winfo_width()
    lampps = len(app._lamp_boxes)
    check("收起时有过渡动画（宽度逐帧变小，不是一步到位）",
          len(set(samples)) >= 3 and samples[0] >= samples[-1],
          "%s → %d" % (samples, actions_end))
    # 注意：Tk 的 Frame 宽度最小是 1（设成 0 也会报 1），所以判断用 <= 1
    check("动画结束后：按钮收起、灯条就位",
          actions_end <= 1 and strip_end > 10 and lampps > 0,
          "按钮 %d px，灯条 %d px，小灯 %d 个" % (actions_end, strip_end, lampps))

    app.config["toolbar_collapsed"] = False
    app._apply_toolbar_collapsed(animate=True)
    pump(app, 0.5)
    check("展开动画结束后：按钮回来、灯条让位",
          app.actions_holder.winfo_width() > 40
          and app.strip_holder.winfo_width() <= 1,
          "按钮 %d px，灯条 %d px" % (app.actions_holder.winfo_width(),
                                     app.strip_holder.winfo_width()))

    try:
        app.quit_app()
    except Exception:
        pass

    failed = [name for name, ok in RESULTS if not ok]
    print("\n" + "=" * 62)
    if failed:
        print("窗口自检：%d/%d 步失败" % (len(failed), len(RESULTS)))
        for name in failed:
            print("  - %s" % name)
    else:
        print("窗口自检：%d 步全部通过" % len(RESULTS))
    print("=" * 62)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
