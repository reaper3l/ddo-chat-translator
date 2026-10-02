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


def _config_snapshot():
    """自检会创建真实窗口，关窗口时程序可能把窗口位置写回配置 —— 先备份再还原，
    保证自检不留下任何痕迹。"""
    from app import paths

    try:
        return paths.read_json(paths.CONFIG_PATH, None), paths.CONFIG_PATH
    except Exception:
        return None, None


def _config_restore(snapshot) -> None:
    data, path = snapshot
    if data is None or path is None:
        return
    from app import paths

    try:
        paths.write_json(path, data)
    except Exception:
        pass


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
    snapshot = _config_snapshot()
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

    # ---- 用户实测：翻译完成后想接着输入下一段，却发现打不进字 ----
    # 输入框本身一直是 normal（没有任何地方禁用它），所以问题在焦点：
    # 无边框窗口 + 异步返回，焦点可能停在按钮/方向下拉上，或者被别的窗口拿走。
    # 规则：翻译结束（成功或失败）后，只要焦点不在任何文本框上，就把它拉回输入框。
    class _FakeResult:
        ok = True
        error = ""
        text = "omw - on my way"

    class _FakeEngine:
        supports_chat = True

        def available(self):
            return True

        def describe(self):
            return "假引擎（自检用，不联网）"

        def translate(self, text, messages, timeout=20):
            return _FakeResult()

    real_engine = app.pipeline.engine
    try:
        app.pipeline.engine = _FakeEngine()
        dialog.input.delete("1.0", "end")
        dialog.input.insert("end", "我马上到")
        dialog.output.focus_force()          # 模拟"焦点跑到别的控件上"
        dialog.translate()
        for _ in range(60):
            pump(app, 0.05)
            if not dialog.busy and dialog.output.get("1.0", "end").strip():
                break
        check("翻译完成后焦点自动回到输入框（用户实测：翻完打不了字）",
              dialog.window.focus_get() is dialog.input
              and dialog.output.get("1.0", "end").strip() == _FakeResult.text,
              "焦点=%r，译文=%r" % (dialog.window.focus_get(),
                                    dialog.output.get("1.0", "end").strip()))
        before = dialog.input.get("1.0", "end").strip()
        dialog.input.event_generate("<KeyPress>", keysym="x", when="tail")
        pump(app, 0.3)
        check("翻译完成后能直接接着敲字",
              dialog.input.get("1.0", "end").strip() == before + "x",
              "%r → %r" % (before, dialog.input.get("1.0", "end").strip()))

        # 连续翻译第二条：确认"翻完就能接着打、再翻也正常"这个循环
        dialog.input.delete("1.0", "end")
        dialog.input.insert("end", "谢谢")
        dialog.output.focus_force()
        dialog.translate()
        for _ in range(60):
            pump(app, 0.05)
            if not dialog.busy:
                break
        check("连续第二次翻译：焦点同样回到输入框",
              dialog.window.focus_get() is dialog.input,
              "焦点=%r" % (dialog.window.focus_get(),))
    finally:
        app.pipeline.engine = real_engine

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
        try:
            dialog.window.destroy()          # 中译英那个也收掉，免得挡住后面的检查
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

    # ---------------- 悬停提示不能一直挂着 ----------------
    from app.ui import theme as theme_module

    auto = theme_module.Tooltip(app.monitor_button, "提示自动消失测试",
                                delay=10, hide_after=300)
    auto._schedule()
    pump(app, 0.15)
    shown = auto._tip is not None
    pump(app, 0.7)
    check("悬停提示到点会自己消失（不会一直挂着）",
          shown and auto._tip is None,
          "出现过=%s，之后还在=%s" % (shown, auto._tip is not None))

    focus = theme_module.Tooltip(app.monitor_button, "失焦就收掉",
                                 delay=10, hide_after=99000)
    focus._schedule()
    pump(app, 0.15)
    shown = focus._tip is not None
    app.monitor_button.winfo_toplevel().event_generate("<FocusOut>")
    pump(app, 0.15)
    check("窗口失焦（点回游戏）时提示会收掉",
          shown and focus._tip is None,
          "出现过=%s，失焦后还在=%s" % (shown, focus._tip is not None))

    # ---------------- 缩放窗口时，窗口按钮不能被灯条挤掉 ----------------
    app.config["frameless"] = True
    app.config["toolbar_collapsed"] = True
    app.apply_settings()
    pump(app, 0.3)
    def squeezed(widget) -> bool:
        """Tk 挤不下时会把控件压窄（或挪出可视区）—— 这两种都算"看不见了"。
        注意不能用 winfo_ismapped()：被压窄/挪出去的控件依然"已映射"。"""
        try:
            parent = widget.master
            if widget.winfo_width() < widget.winfo_reqwidth() - 1:
                return True
            if widget.winfo_x() + widget.winfo_width() > parent.winfo_width() + 1:
                return True
            if parent.winfo_width() < parent.winfo_reqwidth() - 1:
                return True          # 连按钮容器都被压窄了
        except Exception:
            return False
        return False

    bad = []
    for width in (700, 560, 500, 460, 430, 400, 360, 330, 300, 270, 240, 210):
        app.root.geometry("%dx300" % width)
        pump(app, 0.3)
        if not app.min_button.winfo_ismapped() or not app.quit_button.winfo_ismapped():
            bad.append("%dpx(按钮没映射)" % width)
        elif squeezed(app.min_button) or squeezed(app.quit_button):
            bad.append("%dpx(按钮被压窄：—=%d/%d ✕=%d/%d)"
                       % (width, app.min_button.winfo_width(),
                          app.min_button.winfo_reqwidth(), app.quit_button.winfo_width(),
                          app.quit_button.winfo_reqwidth()))
    check("收窄窗口时最小化/关闭按钮一直完整（不会被灯条挤掉）",
          not bad, "全部正常" if not bad else "出问题的宽度：" + "、".join(bad))

    # 用户实测的 bug：窗口很窄时灯条会整条收起来，但再拖宽它不会自己回来
    # （必须点一下 DDO 收放工具条才恢复）。
    for width in (300, 260, 230):
        app.root.geometry("%dx300" % width)
        pump(app, 0.3)
    narrow_stage = app._strip_stage
    app.root.geometry("560x300")
    pump(app, 0.6)
    check("窗口拖宽后小灯会自己回来（不会一直空着）",
          bool(app.channel_strip.winfo_manager()) and len(app._lamp_boxes) > 0,
          "窄时档位=%s → 拖宽后档位=%s，小灯 %d 个"
          % (narrow_stage, app._strip_stage, len(app._lamp_boxes)))
    app.root.geometry("414x300")
    pump(app, 0.3)

    # ---------------- 弹窗要开在主窗口旁边（不是屏幕左上角） ----------------
    app.root.geometry("414x300+900+520")
    pump(app, 0.4)
    mx, my = app.root.winfo_rootx(), app.root.winfo_rooty()
    mw, mh = app.root.winfo_width(), app.root.winfo_height()
    opened = []
    try:
        from app.ui.settings import SettingsDialog as SettingsDialogCls

        settings = SettingsDialogCls(app)
        page = settings.open_category("频道")
        cn2en = CnToEnDialog(app)
        opened = [cn2en.window, page, settings.window]
        pump(app, 0.5)
        for name, window in (("中译英", cn2en.window), ("设置分类页", page),
                             ("设置中心", settings.window)):
            x, y = window.winfo_rootx(), window.winfo_rooty()
            w, h = window.winfo_width(), window.winfo_height()
            near = (abs(x - (mx + mw)) <= 60 or abs((x + w) - mx) <= 60
                    or (mx <= x <= mx + mw) or (mx <= x + w <= mx + mw))
            left_top = x < 8 and y < 8
            check("%s 开在主窗口旁边（不在屏幕左上角）" % name,
                  near and not left_top,
                  "弹窗 (%d,%d) %dx%d，主窗口 (%d,%d) %dx%d"
                  % (x, y, w, h, mx, my, mw, mh))
    finally:
        for window in opened:
            try:
                window.destroy()
            except Exception:
                pass

    try:
        app.quit_app()
    except Exception:
        pass
    _config_restore(snapshot)

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
