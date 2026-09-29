"""界面自检：把每个窗口真实创建一遍再关掉，专门抓 UI 接线错误。

用法（需要有图形界面的机器）：
    python tools/ui_smoke.py

它会顺序做这些事，每一步打印 ok / FAIL：
    1. 创建主窗口、渲染一条假消息
    2. 打开并关闭：纠错窗口 / 中译英 / 设置 / 学习中心 / 词典 / 框选预览 / 区域选择器
    3. 重建术语表、应用设置、更新状态栏

不会联网、不会截图、不会动你的真实配置（会临时备份 config.json 的路径，
整个流程只读写 data/ 里的文件，和平时使用一样）。
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tkinter as tk                          # noqa: E402
from tkinter import filedialog, messagebox, simpledialog, ttk   # noqa: E402

RESULTS = []


def step(name, function):
    try:
        function()
        RESULTS.append((name, None))
        print("  ok    %s" % name)
    except Exception as exc:
        RESULTS.append((name, exc))
        print("  FAIL  %s" % name)
        print("        %s: %s" % (type(exc).__name__, exc))
        print("        " + traceback.format_exc(limit=3).replace("\n", "\n        "))


def silence_dialogs() -> None:
    """把会弹出来卡住的对话框换成自动返回。"""
    messagebox.showinfo = lambda *a, **k: "ok"
    messagebox.showwarning = lambda *a, **k: "ok"
    messagebox.showerror = lambda *a, **k: "ok"
    messagebox.askyesno = lambda *a, **k: True
    filedialog.askopenfilename = lambda *a, **k: ""
    filedialog.asksaveasfilename = lambda *a, **k: ""
    simpledialog.askstring = lambda *a, **k: ""


def find_widget(root, kind, text=None):
    """在控件树里找一个控件（用于检查按钮是否真的可见）。"""
    for child in root.winfo_children():
        if isinstance(child, kind) and (text is None or str(child.cget("text")) == text):
            return child
        found = find_widget(child, kind, text)
        if found is not None:
            return found
    return None


def assert_inside(window, widget, label: str) -> None:
    """确认控件真的在窗口可视范围内（防止被内容挤出窗口）。"""
    window.update_idletasks()
    if widget is None:
        raise AssertionError("找不到「%s」" % label)
    if not widget.winfo_ismapped():
        raise AssertionError("「%s」没有显示出来" % label)
    top = widget.winfo_rooty()
    bottom = top + widget.winfo_height()
    win_top = window.winfo_rooty()
    win_bottom = win_top + window.winfo_height()
    if bottom > win_bottom + 2 or top < win_top - 2:
        raise AssertionError("「%s」被挤出窗口：控件 y=%d..%d，窗口 y=%d..%d"
                             % (label, top, bottom, win_top, win_bottom))


def is_shown(widget) -> bool:
    """这个控件会不会显示出来。

    注意不能只看 winfo_ismapped()：本自检为了避免在你屏幕上闪窗口，一开始就
    `root.withdraw()` 了，主窗口没映射时**它里面所有子控件都报告未映射**，
    于是"折叠/展开工具条"这类检查会假失败。所以这里改用"有没有被 pack 管理"
    来判断（pack_forget 之后 winfo_manager() 会变成空串）。
    """
    if widget is None:
        return False
    try:
        if widget.winfo_ismapped():
            return True
    except Exception:
        pass
    try:
        return bool(widget.winfo_manager())
    except Exception:
        return False


def is_dark_color(color) -> bool:
    """判断一个颜色是否偏暗（比精确匹配十六进制更耐 Tk 的规范化）。"""
    text = str(color).strip().lstrip("#")
    if len(text) != 6:
        return False
    try:
        red, green, blue = (int(text[index:index + 2], 16) for index in (0, 2, 4))
    except Exception:
        return False
    return (red + green + blue) / 3.0 < 110


def main() -> int:
    print("=" * 62)
    print("DDO 聊天翻译助手 · 界面自检")
    print("=" * 62)
    silence_dialogs()

    # 自检会真的创建/关闭窗口，可能把窗口位置写回配置 —— 先备份，结束时还原，
    # 保证自检过程不会改动用户的任何设置。
    from app import paths as paths_module

    config_backup = paths_module.read_json(paths_module.CONFIG_PATH, None)

    from app import disclaimer
    from app.pipeline import DisplayItem
    from app.ui.cn2en import CnToEnDialog
    from app.ui.learn import CorrectionDialog, DictionaryDialog, LearningCenterDialog
    from app.ui.main_window import MainWindow
    from app.ui.region import RegionPicker, show_preview
    from app.ui.settings import SettingsDialog

    print("\n[1] 主窗口")
    holder = {}

    def create_main():
        app = MainWindow()
        app.config["check_update"] = False      # 自检不联网（启动检查是后台请求）
        # 使用须知已经同意过了：这一关单独用下面的步骤测（否则会弹出来挡住后面的步骤）
        app.config["agreement_version"] = disclaimer.DISCLAIMER_VERSION
        app.root.withdraw()
        app.root.update_idletasks()
        holder["app"] = app

    step("创建主窗口", create_main)
    app = holder.get("app")
    if app is None:
        print("\n主窗口都建不起来，后面的检查没法继续。")
        return 1

    def pump(times: int = 3) -> None:
        for _ in range(times):
            app.root.update()
            app.root.update_idletasks()

    step("事件循环（轮询/首次提示）", lambda: pump(3))

    def first_run_agreement():
        """首次启动必须先过"使用须知"：同意才继续，不同意就退出程序。"""
        app.config["agreement_version"] = 0
        app.config["agreement_accepted_at"] = ""
        app.startup_gate()                      # 没同意过 → 应该弹出来
        pump(2)
        dialog = app._agreement_dialog
        if dialog is None:
            raise AssertionError("首次启动没有弹出使用须知窗口")
        # 自检里主窗口是 withdraw 的，子窗口跟着报告"未映射"，所以用 is_shown 判断
        # （它除了 winfo_ismapped 还会看控件有没有被 pack 管理）
        agree = find_widget(dialog.window, ttk.Button, "我已阅读并同意")
        if not is_shown(agree):
            raise AssertionError("使用须知窗口里找不到「我已阅读并同意」")
        if find_widget(dialog.window, ttk.Button, "不同意，退出") is None:
            raise AssertionError("使用须知窗口缺少「不同意，退出」")
        body = find_widget(dialog.window, tk.Text)
        if body is None or "免责声明" not in body.get("1.0", "end"):
            raise AssertionError("使用须知窗口里没有条款正文")
        if app.config["agreement_version"] != 0:
            raise AssertionError("还没点同意就写进了同意状态")
        agree.invoke()
        pump(2)
        if app.config["agreement_version"] != disclaimer.DISCLAIMER_VERSION:
            raise AssertionError("点了同意却没有记录条款版本")
        if not app.config.get("agreement_accepted_at"):
            raise AssertionError("点了同意却没有记录同意时间")
        if app._agreement_dialog is not None:
            raise AssertionError("同意之后窗口没关掉")
        # 同意过之后再走一次启动流程：不该再弹（否则每次开程序都要点一次）
        app.startup_gate()
        pump(1)
        if app._agreement_dialog is not None:
            raise AssertionError("同意过了还重复弹使用须知")
        # 「关于」里的入口：只看，不改同意状态
        viewer = app.open_agreement()
        pump(2)
        close = find_widget(viewer.window, ttk.Button, "关闭")
        if not is_shown(close):
            raise AssertionError("回看条款的窗口里找不到「关闭」")
        if find_widget(viewer.window, ttk.Button, "我已阅读并同意") is not None:
            raise AssertionError("回看条款时不该再出现「我已阅读并同意」")
        close.invoke()
        pump(2)

    step("使用须知：首次启动必须同意（不同意=退出程序）", first_run_agreement)

    def render_chat():
        app._render(DisplayItem(1, "chat", "小队", "Sckham",
                                "need heals for shroud on elite",
                                "需要治疗，幽影堡，精英难度", note="演示"))
        app._render(DisplayItem(2, "chat", "常规", "Alice",
                                "omw", "马上到", note="记忆命中"))
        app._render(DisplayItem(3, "chat", "小队", "Bob",
                                "test error", "test error", error="接口超时（演示）"))
        app._render(DisplayItem(4, "system", "小队", "", "Grelik加入了你的队伍",
                                "Grelik加入了你的队伍", note="系统"))
        pump(2)

    step("渲染聊天/系统/报错三种消息", render_chat)

    def trim_and_clear():
        old = app.config.get("max_lines")
        app.config["max_lines"] = 2
        app._render(DisplayItem(9, "chat", "小队", "X", "a b c", "甲乙丙"))
        app.config["max_lines"] = old
        app.copy_selection()
        app.clear_display()
        pump(2)

    step("显示区裁剪 / 复制 / 清空", trim_and_clear)

    step("更新状态栏与统计", lambda: (app.set_status("自检", "ok"), app._update_stats()))
    step("重建术语表", app.rebuild_glossary)
    step("应用设置", app.apply_settings)
    step("切换显示英文原文", app.toggle_original)

    print("\n[2] 各个窗口")

    def open_and_close(factory):
        window = factory()
        target = getattr(window, "window", None) or getattr(window, "dialog", None)
        app.root.update_idletasks()
        if target is not None:
            target.destroy()
        app.root.update_idletasks()
        return window

    step("纠错窗口", lambda: open_and_close(
        lambda: CorrectionDialog(app, "need heals", "需要治疗")))
    step("中译英窗口", lambda: open_and_close(lambda: CnToEnDialog(app)))
    step("设置窗口", lambda: open_and_close(lambda: SettingsDialog(app)))

    def settings_buttons_visible():
        dialog = SettingsDialog(app)
        dialog.window.update_idletasks()
        # 设置窗口也不该有那个白标题栏（改成自绘深色标题栏）
        if not dialog.window.overrideredirect():
            raise AssertionError("设置窗口还是系统标题栏（应为无边框 + 自绘标题栏）")
        if find_widget(dialog.window, ttk.Button, "✕") is None:
            raise AssertionError("设置窗口没有自绘的关闭按钮")
        # 每个分类单独开一个窗口，逐个确认底部按钮在窗口里
        for title, _desc in SettingsDialog.CATEGORIES:
            page = dialog.open_category(title)
            page.update_idletasks()
            if not page.overrideredirect():
                raise AssertionError("「%s」页还是系统标题栏" % title)
            if find_widget(page, ttk.Button, "✕") is None:
                raise AssertionError("「%s」页没有自绘的关闭按钮" % title)
            if title == "关于":
                assert_inside(page, find_widget(page, ttk.Button, "关闭"), "关闭按钮")
            else:
                assert_inside(page, find_widget(page, ttk.Button, "保存并关闭"),
                              "保存并关闭按钮（%s）" % title)
                assert_inside(page, find_widget(page, ttk.Button, "应用"),
                              "应用按钮（%s）" % title)
            page.destroy()
        dialog.window.destroy()

    step("设置分类各自独立窗口且按钮可见", settings_buttons_visible)

    def appearance_preview_follows_channel_color():
        """频道颜色现在统一在「设置 → 频道」里改：改完外观页的预览要跟着变。"""
        dialog = SettingsDialog(app)
        page = dialog.open_category("外观")
        page.update_idletasks()
        tab = dialog.appearance
        if tab is None:
            raise AssertionError("外观页没有创建出来")
        if hasattr(tab, "channel_vars"):
            raise AssertionError("外观页不该再有单独的频道颜色输入框（已合并到频道页）")
        # 频道颜色只有一份（config["channel_colors"]，由频道表派生）：
        # 改它 → 外观预览、主窗口文字、工具条小灯都跟着变
        original = dict(app.config.get("channel_colors") or {})
        app.config["channel_colors"] = dict(original, **{"小队": "#ff00ff"})
        tab.refresh_preview()
        page.update_idletasks()
        actual = str(tab.preview.tag_cget("channel_小队", "foreground")).lower()
        if actual != "#ff00ff":
            raise AssertionError("预览里的频道颜色没跟着变（当前 %r）" % actual)
        app.config["channel_colors"] = original
        tab.refresh_preview()
        dialog.window.destroy()      # 子窗口会跟着主窗口一起销毁

    step("外观预览跟随频道颜色变化", appearance_preview_follows_channel_color)

    def frameless_and_icon_toolbar():
        """无边框窗口开关 + 图标按钮开关都要真的生效。"""
        original_frameless = bool(app.config.get("frameless", False))
        original_icons = bool(app.config.get("toolbar_icons_only", False))

        app.config["frameless"] = True
        app.config["toolbar_icons_only"] = True
        app.apply_settings()
        app.root.update_idletasks()
        if not app.root.overrideredirect():
            raise AssertionError("开了无边框，但 overrideredirect 没有生效")
        if not is_shown(app.min_button):
            raise AssertionError("无边框模式下看不到最小化按钮")
        if not is_shown(app.grip):
            raise AssertionError("无边框模式下看不到右下角缩放角")
        texts = [str(child.cget("text")) for child in app.actions.winfo_children()]
        if not texts:
            raise AssertionError("工具栏按钮没了")
        if any(len(text.strip()) > 2 for text in texts):
            raise AssertionError("只显示图标时按钮不该有长文字：%s" % texts)

        app.config["frameless"] = False
        app.config["toolbar_icons_only"] = original_icons
        app.apply_settings()
        app.root.update_idletasks()
        if app.root.overrideredirect():
            raise AssertionError("关掉无边框没生效")
        if is_shown(app.min_button):
            raise AssertionError("关掉无边框后最小化按钮还在")

        app.config["frameless"] = original_frameless
        app.apply_settings()

    step("无边框窗口 + 图标工具栏切换", frameless_and_icon_toolbar)

    def toolbar_and_transparency():
        """工具条折叠、状态栏开关、两种背景透明模式都要真的生效。"""
        from app.ui import theme as theme_module

        keys = ("toolbar_collapsed", "show_status_bar", "transparency_mode", "alpha")
        original = {key: app.config.get(key) for key in keys}

        app.config["toolbar_collapsed"] = True
        app.apply_settings()
        app.root.update_idletasks()
        if is_shown(app.actions):
            raise AssertionError("折叠后工具按钮还在显示")
        if "▸" not in str(app.brand.cget("text")):
            raise AssertionError("折叠后标题没有提示可展开：%r" % app.brand.cget("text"))

        app.config["toolbar_collapsed"] = False
        app.apply_settings()
        app.root.update_idletasks()
        if not is_shown(app.actions):
            raise AssertionError("展开后工具按钮没有显示")

        app.config["show_status_bar"] = False
        app.apply_settings()
        app.root.update_idletasks()
        if is_shown(app.status_bar):
            raise AssertionError("关掉状态栏后它还在")
        app.config["show_status_bar"] = True
        app.apply_settings()
        app.root.update_idletasks()
        if not is_shown(app.status_bar):
            raise AssertionError("打开状态栏没生效")

        app.config["transparency_mode"] = "alpha"
        app.config["alpha"] = "0.8"
        app.apply_settings()
        app.root.update_idletasks()
        if abs(float(app.root.attributes("-alpha")) - 0.8) > 0.03:
            raise AssertionError("整窗透明度没生效：%s" % app.root.attributes("-alpha"))

        app.config["transparency_mode"] = "key"
        app.apply_settings()
        app.root.update_idletasks()
        key = str(app.root.attributes("-transparentcolor")).lower()
        if "010203" not in key:
            raise AssertionError("「只透明背景」没生效（transparentcolor=%r）" % key)
        if abs(float(app.root.attributes("-alpha")) - 1.0) > 0.03:
            raise AssertionError("只透明背景时不该还是整窗半透明")
        if theme_module.KEY_COLOR.lower() not in key:
            raise AssertionError("颜色键和代码里的常量不一致：%r" % key)

        for key_name, value in original.items():
            app.config[key_name] = value
        app.apply_settings()

    step("工具条折叠 / 状态栏 / 背景透明", toolbar_and_transparency)

    def theme_and_toolbar_click():
        """点折叠按钮要能真的把工具条显示出来；主题要真的被应用（默认暗色）。"""
        from tkinter import ttk as ttk_module

        from app.ui import theme as theme_module

        original = app.config.get("toolbar_collapsed")
        app.config["toolbar_collapsed"] = True
        app.apply_settings()
        app.root.update_idletasks()
        if is_shown(app.actions):
            raise AssertionError("折叠状态下工具按钮不该显示")
        app._toggle_toolbar()            # 模拟点标题/▸
        app.root.update_idletasks()
        if not is_shown(app.actions):
            raise AssertionError("点了标题，工具条没有展开")
        if "▸" in str(app.brand.cget("text")):
            raise AssertionError("展开后标题还带着折叠箭头：%r" % app.brand.cget("text"))
        expanded_width = app.top_frame.winfo_reqwidth()
        # 图标模式下展开也应该很窄；文字模式（图标+文字）天然更宽，用宽松阈值
        limit = 420 if app.config.get("toolbar_icons_only", False) else 720
        if expanded_width > limit:
            raise AssertionError("展开后工具条太宽（%d px > %d），会限制窗口能缩多小"
                                 % (expanded_width, limit))
        monitor_width = app.monitor_button.winfo_reqwidth()
        if monitor_width > 60:
            raise AssertionError("监听按钮太宽（%d px），挤占了图标按钮的位置"
                                 % monitor_width)
        app._toggle_toolbar()
        app.root.update_idletasks()
        if is_shown(app.actions):
            raise AssertionError("再点一次没有收起")
        if "▸" not in str(app.brand.cget("text")):
            raise AssertionError("折叠后标题没有提示可以展开：%r" % app.brand.cget("text"))
        collapsed_width = app.top_frame.winfo_reqwidth()
        if app.frameless.min_w > 240:
            raise AssertionError("折叠后最小宽度没放宽（%d），窗口还是收不小"
                                 % app.frameless.min_w)
        # 折叠后那段留白里放了"频道灯条"，内容自然比过去长；关键是**窗口收小时
        # 它要自己分级瘦身**，不能把窗口顶开。这里直接验证分级逻辑（窗口在自检里
        # 是 withdraw 状态，改 geometry 不会真的生效，所以不能靠量窗口宽度）。
        enabled = app.config.get("channels_enabled") or {}
        widths = [(stage, app._apply_strip_stage(stage))
                  for stage in ("all_names", "on_names", "dots", "on_dots")]
        by_stage = dict(widths)
        # 档位是"按偏好排的"，不要求严格递窄（只留开着的频道就可能比纯色点还窄），
        # 但整体必须越来越省地方，"只留开着的频道"也不能比"全部频道"还宽
        if not (by_stage["all_names"] > by_stage["dots"] > by_stage["on_dots"]):
            raise AssertionError("灯条分级没有越缩越窄：%s" % widths)
        if by_stage["on_names"] > by_stage["all_names"]:
            raise AssertionError("「只留开着的频道」不该比全部频道还宽：%s" % widths)
        app._apply_strip_stage("on_names")
        on_names = [box[4] for box in app._lamp_boxes]
        if any(not enabled.get(name, True) for name in on_names):
            raise AssertionError("「只留开着的频道」这一档还画了关掉的频道：%s" % on_names)
        # 最窄的一档：整条藏起来（窗口小到放不下时不能把窗口顶开）
        if app._apply_strip_stage("hidden") != 0:
            raise AssertionError("最窄档应该整条藏起来")
        if is_shown(app.channel_strip):
            raise AssertionError("最窄档还占着位置")
        app._apply_strip_stage("full")
        if not is_shown(app.channel_strip):
            raise AssertionError("恢复 full 档以后灯条没回来")
        app._apply_strip_stage("full")
        app._strip_stage = None
        app._fit_channel_strip()
        app.root.update_idletasks()
        _ = collapsed_width          # 只作参考，不再作为断言
        app._toggle_toolbar()            # 再展开，确认最小宽度恢复
        app.root.update_idletasks()
        if app.frameless.min_w < 320:
            raise AssertionError("展开后最小宽度没恢复（%d）" % app.frameless.min_w)

        style = ttk_module.Style(app.root)
        if style.theme_use() != "clam":
            raise AssertionError("主题不是 clam（当前 %s），颜色会跟着系统走" % style.theme_use())
        background = str(style.lookup("TFrame", "background")).lower()
        if not is_dark_color(background):
            raise AssertionError("暗色主题没生效：TFrame background=%r" % background)

        # 设置页的可滚动容器是个 tk.Canvas，必须也是暗色，否则看起来"半亮半暗"
        dialog = SettingsDialog(app)
        page = dialog.open_category("外观")
        page.update_idletasks()
        canvas = find_widget(page, tk.Canvas)
        if canvas is None:
            raise AssertionError("设置页里没找到滚动容器")
        canvas_bg = str(canvas.cget("bg")).lower()
        if not is_dark_color(canvas_bg):
            raise AssertionError("设置页滚动区底色不是暗色：%r" % canvas_bg)
        page.destroy()
        dialog.window.destroy()

        # 提示窗必须置顶，否则会被置顶的主窗口挡住
        tooltip = theme_module.Tooltip(app.monitor_button, "提示测试")
        tooltip._show()
        app.root.update_idletasks()
        if tooltip._tip is None:
            raise AssertionError("提示窗没有弹出来")
        try:
            topmost = int(tooltip._tip.attributes("-topmost"))
        except Exception as exc:
            raise AssertionError("读不到提示窗的置顶属性：%s" % exc)
        if topmost != 1:
            raise AssertionError("提示窗没有置顶（-topmost=%r），会被主窗口挡住" % topmost)
        tooltip._hide()

        app.config["toolbar_collapsed"] = original
        app.apply_settings()

    step("主题生效 + 工具栏点开收起", theme_and_toolbar_click)

    def channel_strip():
        """收起工具条后那块留白：一排频道小灯，点一下就能开关频道。"""
        from app import channels as channels_module

        original_collapsed = bool(app.config.get("toolbar_collapsed", True))
        original_items = [dict(item) for item in channels_module.effective(app.config)]
        app.config["toolbar_collapsed"] = True
        app._apply_toolbar_collapsed()
        app._apply_strip_stage("all_names")          # 固定成一档（自检里窗口是 withdraw 的）
        app.root.update_idletasks()
        if not is_shown(app.channel_strip):
            raise AssertionError("收起工具条后没显示频道灯条")
        if is_shown(app.actions):
            raise AssertionError("收起后功能按钮还显示着")
        # 底色要和工具条一致（面板色），否则会出现一块比工具条更暗的方块
        from app.ui import theme as theme_module

        strip_bg = str(app.channel_strip.cget("bg")).lower()
        if strip_bg != str(theme_module.PALETTE["surface"]).lower():
            raise AssertionError("灯条底色和工具条不一致（看着像黑块）：%r" % strip_bg)
        canvas_bg = str(app._lamp_canvas.cget("bg")).lower()
        if canvas_bg != strip_bg:
            raise AssertionError("小灯画布底色和灯条不一致：%r" % canvas_bg)
        # 容器不能比画布窄，否则最后一个小灯的圆角会被切掉。
        # 这里比的是**配置的**宽度（cget）而不是 winfo_width()：自检里窗口是
        # withdraw 状态，winfo_width() 不会随 configure 更新，量出来是旧值。
        configured = int(str(app.strip_holder.cget("width")))
        needed = app._lamp_canvas.winfo_reqwidth()
        if needed > configured + 1:
            raise AssertionError(
                "灯条容器比小灯需要的宽度还窄（会裁切）：容器 %d < 画布 %d"
                % (configured, needed))
        boxes = app._lamp_boxes
        names = [name for _x1, _y1, _x2, _y2, name in boxes]
        if not names:
            raise AssertionError("频道小灯没画出来")
        # 画出来的必须正好是"用户勾了要显示小灯"的那些频道（strip 字段）
        expected = [str(entry["name"]) for entry in channels_module.effective(app.config)
                    if bool(entry.get("strip", True))]
        if names != expected:
            raise AssertionError("小灯和「设置 → 频道」里勾选的（小灯列）对不上："
                                 "%s != %s" % (names, expected))
        colors = channels_module.color_map(app.config)
        items = app._lamp_canvas.find_all()
        first_fill = str(app._lamp_canvas.itemcget(items[0], "fill")).lower()
        if first_fill != str(colors.get(names[0], "")).lower():
            raise AssertionError("开着的频道小灯没有用该频道的颜色：%r" % first_fill)

        # 直接点第一盏灯（走的是真实的坐标命中 + 开关逻辑）
        class _Click:
            def __init__(self, x, y):
                self.x, self.y = x, y

        target = "公会" if "公会" in names else names[0]
        x1, y1, x2, y2, _name = next(box for box in boxes if box[4] == target)
        before = bool(channels_module.enabled_map(app.config).get(target, True))
        app._on_lamp_click(_Click((x1 + x2) // 2, (y1 + y2) // 2))
        app.root.update_idletasks()
        after = bool(channels_module.enabled_map(app.config).get(target, True))
        if after == before:
            raise AssertionError("点频道小灯没有切换频道开关")
        app._toggle_channel(target)                  # 还原
        channels_module.sync(app.config, original_items)
        # 一定要落盘还原：_toggle_channel 会把开关写进用户的 config.json，
        # 上面只是改了内存里的表（自检不该留下任何痕迹）
        from app import config as config_module

        config_module.save_config(app.config)
        app.config["toolbar_collapsed"] = original_collapsed
        app._apply_toolbar_collapsed()
        app.root.update_idletasks()

        # 关掉"小灯"勾选以后，那一盏就不该再画出来
        items = channels_module.effective(app.config)
        if items:
            items[0]["strip"] = not bool(items[0].get("strip", True))
            channels_module.sync(app.config, items)
            app._apply_strip_stage("all_names")
            app.root.update_idletasks()
            drawn = [box[4] for box in app._lamp_boxes]
            wanted = [str(entry["name"]) for entry in channels_module.effective(app.config)
                      if bool(entry.get("strip", True))]
            if drawn != wanted:
                raise AssertionError("取消「小灯」勾选后还画着它：%s != %s"
                                     % (drawn, wanted))
            channels_module.sync(app.config, original_items)
            config_module.save_config(app.config)

    step("收起后的频道灯条（点一下开关频道）", channel_strip)

    def settings_save_is_reliable():
        """保存必须"该生效的都生效"：

        * 某个输入框填坏 → 只跳过那一项，其它照常保存（以前是整单作废，
          而且提示写在别的页面上，看着就是"点了保存并关闭没反应"）；
        * 频道开关只有「频道」页一份（以前监控页也有一份，互相覆盖）。
        """
        from app import channels as channels_module
        from app.ui.settings import SettingsDialog as Dialog

        dialog = Dialog(app)
        monitor = dialog.open_category("监控")
        monitor.update_idletasks()
        if hasattr(dialog, "channel_vars"):
            raise AssertionError("监控页不该再有频道勾选（会和频道页打架）")
        if find_widget(monitor, ttk.Button, "打开「频道」设置") is None:
            raise AssertionError("监控页没有跳转到频道页的按钮")

        before_interval = app.config.get("interval_ms")
        before_notes = bool(app.config.get("show_notes", False))
        channel_items = channels_module.effective(app.config)
        target = channel_items[-1]["name"] if channel_items else ""
        before_target = channels_module.enabled_map(app.config).get(target, True)

        # 1) 故意把一个数字框填坏，同时改别的设置 + 翻一个频道
        dialog.open_category("显示与学习")      # 先建页面，才有 show_notes 这个变量
        dialog.vars["interval_ms"][1].set("")
        dialog.vars["show_notes"][1].set(not before_notes)
        dialog.open_category("频道")
        for record in dialog.channel_rows:
            if str(record["name"].get()) == target:
                record["enabled"].set(not before_target)
        ok = dialog.save()
        if ok:
            raise AssertionError("有坏值时 save() 应该返回 False")
        if app.config.get("interval_ms") != before_interval:
            raise AssertionError("坏值那项不该被写进配置")
        if bool(app.config.get("show_notes")) == before_notes:
            raise AssertionError("坏值之外的设置没保存上")
        if channels_module.enabled_map(app.config).get(target) == before_target:
            raise AssertionError("频道开关没保存上（被别的页面覆盖了？）")
        dialog.window.destroy()

    step("设置保存：坏值不阻塞 + 频道只此一处", settings_save_is_reliable)

    def cn2en_reply_suggestions():
        """中译英窗口的"根据上下文推荐回复"：中英对照、点一行复制英文、双击填中文。

        这里不联网：直接把假数据喂给显示逻辑，验证接线（真实生成由用户按需触发）。
        """
        from app.ui.cn2en import CnToEnDialog

        dialog = CnToEnDialog(app, auto_suggest=False)     # 自检里不许偷偷调接口
        try:
            dialog.window.update_idletasks()
            dialog._show_suggestions([("马上到", "omw"), ("谢谢", "ty")])
            dialog.window.update_idletasks()
            rows = dialog.suggest_list.get_children()
            if len(rows) != 2:
                raise AssertionError("建议列表没填进去：%s" % (rows,))
            first = rows[0]
            values = dialog.suggest_list.item(first, "values")
            if tuple(values[:2]) != ("马上到", "omw"):
                raise AssertionError("建议列表内容不对：%s" % (values,))

            dialog.suggest_list.selection_set(first)
            dialog.suggest_list.focus(first)
            dialog._on_suggest_click()                     # 点一行 → 复制英文
            try:
                copied = app.root.clipboard_get()
            except Exception:
                copied = ""
            if copied != "omw":
                raise AssertionError("点建议没复制英文，剪贴板是 %r" % copied)

            dialog._on_suggest_double()                    # 双击 → 中文进输入框
            text = dialog.input.get("1.0", "end").strip()
            if text != "马上到":
                raise AssertionError("双击建议没把中文填进输入框：%r" % text)
        finally:
            try:
                dialog.window.destroy()
            except Exception:
                pass

    step("中译英：上下文推荐回复（中英对照 / 点击复制）", cn2en_reply_suggestions)

    def cn2en_direction_switch():
        """互译窗口的"方向"下拉：自动识别，也能强制翻成英文 / 翻成中文。"""
        from app.ui.cn2en import CnToEnDialog

        dialog = CnToEnDialog(app, auto_suggest=False)
        try:
            dialog.input.delete("1.0", "end")
            dialog.input.insert("1.0", "马上到")
            dialog.direction_var.set("自动（看内容）")
            dialog._refresh_direction()
            if dialog.direction != "zh2en":
                raise AssertionError("自动模式下中文应识别为 中→英")
            dialog.input.delete("1.0", "end")
            dialog.input.insert("1.0", "omw")
            dialog._refresh_direction()
            if dialog.direction != "en2zh":
                raise AssertionError("自动模式下英文应识别为 英→中")
            # 强制翻成中文：输入是中文也按"英→中"，提示里要写明"强制"
            dialog.input.delete("1.0", "end")
            dialog.input.insert("1.0", "马上到")
            dialog.direction_var.set("翻成中文")
            dialog._refresh_direction()
            if dialog.direction != "en2zh":
                raise AssertionError("强制翻成中文没有生效")
            if "强制" not in dialog.direction_label.cget("text"):
                raise AssertionError("强制方向时提示里应该写明")
            dialog.direction_var.set("翻成英文")
            dialog._refresh_direction()
            if dialog.direction != "zh2en":
                raise AssertionError("强制翻成英文没有生效")
            # 选回自动，别把设置留在"强制"上
            dialog.direction_var.set("自动（看内容）")
            dialog._on_direction_changed()
            if app.config.get("cn2en_direction") != "auto":
                raise AssertionError("方向选择没有写回配置")
        finally:
            dialog.window.destroy()

    step("互译：方向自动识别 / 可强制", cn2en_direction_switch)

    def cn2en_paste_support():
        """互译窗口的输入框要能直接粘贴：Ctrl+V 靠 Tk 的 Text 类绑定，右键另有编辑菜单。

        注意：自检里主窗口是 withdraw 的，而 Tk 的粘贴需要窗口可见（实测隐藏窗口下
        <<Paste>> 静默无效），所以这一步只查"接线"对不对；真实的粘贴/复制行为在
        可见窗口下另测（tools 里的临时脚本跑过：Ctrl+V、Ctrl+A、Ctrl+C、右键粘贴都正常）。
        """
        from app.ui.cn2en import CnToEnDialog

        dialog = CnToEnDialog(app, auto_suggest=False)
        try:
            widget = dialog.input
            paste_binding = (widget.bind_class("Text", "<<Paste>>")
                             or widget.bind_class("Text", "<Control-v>")
                             or widget.bind_class("Text", "<Control-Key-v>"))
            if not paste_binding:
                raise AssertionError("输入框没有 Ctrl+V 绑定（Tk 默认绑定丢了？）")
            if not (widget.bind("<Button-3>") or widget.bind("<Button-2>")):
                raise AssertionError("输入框没有绑定右键菜单")
            menus = [child for child in dialog.window.winfo_children()
                     if isinstance(child, tk.Menu)]
            if len(menus) < 2:
                raise AssertionError("输入框/译文框应有各自的右键菜单")
            labels = [menus[0].entrycget(index, "label")
                      for index in range(menus[0].index("end") + 1)
                      if menus[0].type(index) == "command"]
            for wanted in ("粘贴", "复制选中", "剪切", "全选"):
                if wanted not in labels:
                    raise AssertionError("右键菜单缺少「%s」" % wanted)
        finally:
            dialog.window.destroy()

    step("互译：输入框可直接粘贴（Ctrl+V / 右键）", cn2en_paste_support)

    def update_dialog_smoke():
        """「发现新版本」窗口的接线（不联网：喂一个假的 UpdateInfo）。"""
        from app import update as update_module
        from app.ui.update_dialog import UpdateDialog

        original_can = update_module.can_self_update
        info = update_module.UpdateInfo(
            version="9.9.9", tag="v9.9.9", notes="1. 测试用的更新说明\n2. 第二条",
            page_url="https://example.invalid/releases/tag/v9.9.9",
            asset_url="",                     # 没有可下载附件 → 只应引导去发行页
            asset_name="", asset_size=12 * 1024 * 1024, current="3.0.19")
        dialog = UpdateDialog(app, info)
        try:
            dialog.window.update_idletasks()
            body = dialog.window.winfo_children()
            texts = []
            for child in body:
                try:
                    if isinstance(child, tk.Text):
                        texts.append(child.get("1.0", "end"))
                except Exception:
                    pass
            if not any("测试用的更新说明" in text for text in texts):
                raise AssertionError("更新说明没显示出来")
            if find_widget(dialog.window, ttk.Button, "稍后再说") is None:
                raise AssertionError("缺少「稍后再说」按钮")
            if find_widget(dialog.window, ttk.Button, "跳过这个版本") is None:
                raise AssertionError("缺少「跳过这个版本」按钮")
            # 没有可下载附件时不该出现"现在升级"（自动升级）按钮
            if find_widget(dialog.window, ttk.Button, "现在升级（自动下载并重启）"):
                raise AssertionError("没有附件却提供了自动升级按钮")
            dialog.skip_version()               # 点"跳过这个版本"要记住
            if str(app.config.get("update_skipped")) != "9.9.9":
                raise AssertionError("跳过版本没有记进配置")
        finally:
            try:
                dialog.window.destroy()
            except Exception:
                pass
            app.config["update_skipped"] = ""

        # 打包版 + 有安装包但**没有签名** → 不许自动安装（防"被人换了包"）
        update_module.can_self_update = lambda: True
        unsigned = update_module.UpdateInfo(
            version="9.9.9", tag="v9.9.9", notes="这次没有签名块",
            page_url="https://example.invalid/", asset_url="https://x/y.zip",
            asset_name="DDO.zip", asset_size=1024, current="3.0.19")
        dialog = UpdateDialog(app, unsigned)
        try:
            dialog.window.update_idletasks()

            def collect_texts(widget):
                """把控件树里所有 text 属性收集起来（标签可能是 ttk 也可能是 tk）。"""
                found = []
                try:
                    value = widget.cget("text")
                    if isinstance(value, str):
                        found.append(value)
                except Exception:
                    pass
                for child in widget.winfo_children():
                    found.extend(collect_texts(child))
                return found

            texts = collect_texts(dialog.window)
            if not any("签名" in text for text in texts):
                raise AssertionError("没签名的发行版应该明确提示（不让自动升级）")
            if find_widget(dialog.window, ttk.Button, "现在升级（自动下载并重启）"):
                raise AssertionError("没签名却给了自动升级按钮")
            # 即使点了"仍要安装"（没勾允许未签名）也必须被拦住、不进入下载
            dialog.start_update()
            if dialog.busy:
                raise AssertionError("没勾选允许未签名时不该开始下载")
        finally:
            update_module.can_self_update = original_can
            try:
                dialog.window.destroy()
            except Exception:
                pass

    step("检查更新：发现新版本窗口（假数据，不联网）", update_dialog_smoke)

    def window_buttons():
        """窗口按钮：关闭在最右、最小化在它左边；悬停要有明暗反馈（关闭键变红）。"""
        from tkinter import ttk as ttk_module

        from app.ui import theme as theme_module

        app.config["frameless"] = True
        app._apply_frameless()
        app.root.update_idletasks()
        if not is_shown(app.min_button):
            raise AssertionError("无边框模式下最小化按钮没显示")
        slaves = app.window_buttons.pack_slaves()
        # 侧边 pack：先 pack 的在最右边 → 关闭键必须在第 0 位
        if len(slaves) < 2 or slaves[0] is not app.quit_button \
                or slaves[1] is not app.min_button:
            raise AssertionError("窗口按钮顺序不对（应该是 最小化 ➜ 关闭）：%s"
                                 % [w.cget("text") for w in slaves])
        if str(app.quit_button.cget("style")) != "WindowClose.TButton":
            raise AssertionError("关闭键没用 WindowClose 样式")
        if str(app.min_button.cget("style")) != "Window.TButton":
            raise AssertionError("最小化键没用 Window 样式")
        style = ttk_module.Style(app.root)
        close_hover = str(style.lookup("WindowClose.TButton", "background",
                                      ("active",))).lower()
        if close_hover != str(theme_module.PALETTE["danger"]).lower():
            raise AssertionError("关闭键悬停没变红：%r" % close_hover)
        min_hover = str(style.lookup("Window.TButton", "background",
                                    ("active",))).lower()
        if min_hover != str(theme_module.PALETTE["surface_hi"]).lower():
            raise AssertionError("最小化键悬停没有明暗反馈：%r" % min_hover)
        base = str(style.lookup("Window.TButton", "background")).lower()
        if base != str(theme_module.PALETTE["surface"]).lower():
            raise AssertionError("窗口按钮平时底色的和工具条不一致：%r" % base)
        app.config["frameless"] = bool(original_frameless)
        app._apply_frameless()
        app.root.update_idletasks()

    original_frameless = bool(app.config.get("frameless", False))
    step("窗口按钮（顺序 / 悬停反馈）", window_buttons)

    def body_color_follows_channel():
        """正文颜色要跟随频道色：小队的绿、常规的黄，各是各的。"""
        from app.ui import style as style_module

        if not style_module.element_spec(app.config, "body").get("follow_channel"):
            raise AssertionError("正文默认应该是「跟随频道颜色」")
        app._configure_tags()
        app.root.update_idletasks()
        colors = app.config.get("channel_colors") or {}
        squad = str(app.text.tag_cget("body_小队", "foreground")).lower()
        normal = str(app.text.tag_cget("body_常规", "foreground")).lower()
        expected_squad = str(colors.get("小队", "")).lower()
        expected_normal = str(colors.get("常规", "")).lower()
        if squad != expected_squad:
            raise AssertionError("小队正文色（%s）没跟上频道色（%s）"
                                 % (squad, expected_squad))
        if normal != expected_normal:
            raise AssertionError("常规正文色（%s）没跟上频道色（%s）"
                                 % (normal, expected_normal))
        if squad == normal:
            raise AssertionError("不同频道的正文色应该不同（都是 %s）" % squad)

    step("正文颜色跟随频道颜色", body_color_follows_channel)
    step("学习中心", lambda: open_and_close(lambda: LearningCenterDialog(app)))
    step("词典窗口", lambda: open_and_close(lambda: DictionaryDialog(app)))

    def dictionary_export_import():
        """词典的导出/导入接线：导出成 json 再读回来，导入不应该炸。"""
        import tempfile
        from pathlib import Path

        from app import glossary_io

        dialog = DictionaryDialog(app)
        dialog.window.update_idletasks()
        target = Path(tempfile.mkdtemp(prefix="ddo_dict_")) / "glossary.json"
        original_save = filedialog.asksaveasfilename
        original_open = filedialog.askopenfilename
        try:
            filedialog.asksaveasfilename = lambda *a, **k: str(target)
            export_button = find_widget(dialog.window, ttk.Button, "导出全部…")
            if export_button is None:
                raise AssertionError("词典窗口没有「导出全部…」按钮")
            export_button.invoke()
            if not target.exists():
                raise AssertionError("点了导出却没有生成文件")
            terms = glossary_io.load_terms(target.read_text(encoding="utf-8"), str(target))
            if len(terms) < 10:
                raise AssertionError("导出的术语太少（%d 条）" % len(terms))
            # "只导出我的词条"：没有自建词条时会提示（自检里对话框被静音），不能报错
            mine_button = find_widget(dialog.window, ttk.Button, "只导出我的…")
            if mine_button is None:
                raise AssertionError("词典窗口没有「只导出我的…」按钮")
            mine_button.invoke()
            # 再导入同一份文件：应该提示"没有需要导入的"，而不是报错
            filedialog.askopenfilename = lambda *a, **k: str(target)
            import_button = find_widget(dialog.window, ttk.Button, "导入…")
            if import_button is None:
                raise AssertionError("词典窗口没有「导入…」按钮")
            import_button.invoke()
            dialog.refresh()
        finally:
            filedialog.asksaveasfilename = original_save
            filedialog.askopenfilename = original_open
            dialog.window.destroy()

    step("词典：导出 / 导入术语表", dictionary_export_import)

    def preview():
        region = app.config.get("region") or [0, 0, 100, 50]
        show_preview(app.root, region, None, ["(小队)Alice: hello there", "系统消息"])
        app.root.update_idletasks()
        for child in app.root.winfo_children():
            if isinstance(child, tk.Toplevel):
                child.destroy()

    step("框选预览窗口", preview)

    def picker():
        instance = RegionPicker(app.root, None)
        instance.start()          # 这一步曾经因为属性覆盖方法而报 not callable
        app.root.update_idletasks()
        instance._finish(None)

    step("区域选择器（含 start 调用）", picker)

    print("\n[3] 后台流水线状态")
    step("读取流水线状态", lambda: app.pipeline.status())
    step("写入缓存与学习库", lambda: (app.pipeline.flush_cache(),
                                      app.memory.flush(force=True)))

    step("关闭主窗口", app.quit_app)

    if config_backup is not None:                  # 还原配置，自检不留痕迹
        paths_module.write_json(paths_module.CONFIG_PATH, config_backup)

    failed = [(name, exc) for name, exc in RESULTS if exc is not None]
    print("\n" + "=" * 62)
    if failed:
        print("界面自检：%d/%d 步失败" % (len(failed), len(RESULTS)))
        for name, exc in failed:
            print("  - %s: %s" % (name, exc))
    else:
        print("界面自检：%d 步全部通过" % len(RESULTS))
    print("=" * 62)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
