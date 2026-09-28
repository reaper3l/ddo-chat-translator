"""统一主题：配色、ttk 样式、小控件工厂。

风格参考了成熟翻译工具 LunaTranslator 的暗色 UI 思路：
  深色底 + 面板色 + 悬停/按下态 + 强调色 + 灰色次要文字 + 紧凑按钮。
Tk 没有圆角和图标字体，所以用「扁平 + 内边距 + 1px 描边 + 悬停变色」达到接近的观感。
"""
from __future__ import annotations

import tkinter as tk
import sys
from tkinter import ttk
from typing import Callable, Optional

from . import style as style_module
from .frameless import FramelessWindow

# 调色板：只保留暗色一套（不再支持切换，避免切换后出现半白半黑的残留）
PALETTE = {
    "bg": "#1b1c20",          # 窗口底
    "surface": "#2a2d33",     # 面板 / 输入框（比底色亮一档）
    "surface_hi": "#383c44",  # 悬停
    "surface_press": "#434852",
    "border": "#474b54",      # 描边调亮，暗色下看得清
    "text": "#e8eaed",
    "muted": "#aab2bb",       # 次要文字调亮
    "accent": "#4c9aff",
    "accent_hi": "#6bb0ff",
    "accent_press": "#3b82f6",
    "on_accent": "#0b1220",
    "danger": "#ff7b72",
    "ok": "#5fd068",
    "warn": "#ffc14d",
}

UI_FONT_SIZE = 11

# "只透明背景"模式用的颜色键：这个颜色会被 Windows 判定为完全透明。
# 选一个 UI 里绝不会用到的极深色，避免误伤正常内容。
KEY_COLOR = "#010203"


def mix(color_a: str, color_b: str, ratio: float = 0.5) -> str:
    """把两个 #rrggbb 颜色按比例混合（ratio=0 取 color_a，1 取 color_b）。

    用来做"变暗"：频道小灯关掉时把频道色和背景色混一混，颜色还认得出，
    但一眼能看出是关着的。
    """
    def parts(color: str):
        text = str(color).strip().lstrip("#")
        if len(text) != 6:
            return None
        try:
            return [int(text[i:i + 2], 16) for i in (0, 2, 4)]
        except ValueError:
            return None

    first, second = parts(color_a), parts(color_b)
    if first is None or second is None:
        return color_a
    ratio = max(0.0, min(1.0, float(ratio)))
    mixed = [round(a + (b - a) * ratio) for a, b in zip(first, second)]
    return "#%02x%02x%02x" % tuple(mixed)


def text_on(color: str) -> str:
    """在某个底色的色块上，文字该用深色还是浅色（按亮度算）。

    频道颜色是用户自己设的，可能很深（深蓝、深紫）。固定用深色字会出现
    "黑字压黑底"看不清的情况，所以这里按**相对亮度**挑（标准做法：先做
    sRGB 线性化再按 0.2126/0.7152/0.0722 加权）。
    """
    text = str(color).strip().lstrip("#")
    if len(text) != 6:
        return "#101218"
    try:
        red, green, blue = (int(text[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return "#101218"

    def linear(channel: float) -> float:
        return channel / 12.92 if channel <= 0.03928 \
            else ((channel + 0.055) / 1.055) ** 2.4

    luminance = (0.2126 * linear(red) + 0.7152 * linear(green)
                 + 0.0722 * linear(blue))
    return "#101218" if luminance > 0.35 else "#f2f5f8"


def install(root: tk.Misc, config: Optional[dict] = None) -> ttk.Style:
    """给整个窗口装暗色主题（只有这一套）。"""
    config = config or {}
    family = (config.get("font_family") or "Microsoft YaHei").strip() or "Microsoft YaHei"
    try:
        size = int(config.get("ui_font_size", UI_FONT_SIZE) or UI_FONT_SIZE)
    except Exception:
        size = UI_FONT_SIZE
    size = max(9, min(20, size))
    ui_font = (family, size)

    style = ttk.Style(root)
    for theme in ("clam", "alt", "default"):
        try:
            style.theme_use(theme)
            break
        except Exception:
            continue

    p = PALETTE
    try:
        root.configure(bg=p["bg"])
    except Exception:
        pass

    # 核心选项（所有主题都支持）
    style.configure(".", background=p["bg"], foreground=p["text"],
                    fieldbackground=p["surface"], font=ui_font)
    # clam 专有选项：不同 Tk 版本/主题可能不认，认不出就跳过（样式降级而不是报错）
    for options in (
        {"bordercolor": p["border"]},
        {"lightcolor": p["surface"], "darkcolor": p["surface"]},
        {"focuscolor": p["accent"]},
        {"arrowcolor": p["muted"]},
    ):
        try:
            style.configure(".", **options)
        except Exception:
            pass
    style.configure("TFrame", background=p["bg"])
    style.configure("Surface.TFrame", background=p["surface"])
    style.configure("Card.TFrame", background=p["surface"],
                    relief="solid", borderwidth=1)
    style.configure("TLabel", background=p["bg"], foreground=p["text"])
    style.configure("Surface.TLabel", background=p["surface"], foreground=p["text"])
    style.configure("Muted.TLabel", background=p["bg"], foreground=p["muted"])
    style.configure("SurfaceMuted.TLabel", background=p["surface"], foreground=p["muted"])
    style.configure("Title.TLabel", background=p["bg"], foreground=p["text"],
                    font=(family, size + 2, "bold"))
    style.configure("Status.TLabel", background=p["bg"], foreground=p["muted"],
                    font=(family, max(8, size - 1)))
    for key, color in (("Ok", p["ok"]), ("Warn", p["warn"]), ("Error", p["danger"])):
        style.configure("%s.TLabel" % key, background=p["bg"], foreground=color,
                        font=(family, max(8, size - 1)))

    # 按钮：扁平 + 悬停 + 按下
    style.configure("TButton", background=p["surface"], foreground=p["text"],
                    borderwidth=0, focusthickness=0, padding=(8, 4),
                    relief="flat", anchor="center")
    # 工具栏用的紧凑按钮（无边框小窗口里要尽量省地方）
    style.configure("Compact.TButton", background=p["bg"], foreground=p["text"],
                    padding=(4, 2), borderwidth=0, focusthickness=0)
    style.map("Compact.TButton",
              background=[("pressed", p["surface_press"]), ("active", p["surface_hi"])])
    style.configure("CompactAccent.TButton", background=p["accent"],
                    foreground=p["on_accent"], padding=(7, 2),
                    borderwidth=0, focusthickness=0)
    style.map("CompactAccent.TButton",
              background=[("pressed", p["accent_press"]), ("active", p["accent_hi"])])
    style.configure("CompactDanger.TButton", background=p["danger"],
                    foreground="#ffffff", padding=(7, 2),
                    borderwidth=0, focusthickness=0)
    style.map("CompactDanger.TButton",
              background=[("pressed", "#d32f2f"), ("active", "#ff6b68")])
    # 图标按钮：字形比正文大 2 号，才看得清；宽度只占 2 个字符
    icon_font = (family, size + 2)
    style.configure("Icon.TButton", background=p["bg"], foreground=p["text"],
                    padding=(3, 1), borderwidth=0, focusthickness=0, font=icon_font)
    style.map("Icon.TButton",
              background=[("pressed", p["surface_press"]), ("active", p["surface_hi"])])
    style.configure("IconAccent.TButton", background=p["accent"],
                    foreground=p["on_accent"], padding=(7, 1), borderwidth=0,
                    focusthickness=0, font=icon_font)
    style.map("IconAccent.TButton",
              background=[("pressed", p["accent_press"]), ("active", p["accent_hi"])])
    style.configure("IconDanger.TButton", background=p["danger"], foreground="#ffffff",
                    padding=(7, 1), borderwidth=0, focusthickness=0, font=icon_font)
    style.map("IconDanger.TButton",
              background=[("pressed", "#d32f2f"), ("active", "#ff6b68")])
    # 窗口按钮（— 最小化 / ✕ 关闭）：平时和工具条同底色、低调；悬停才亮起来，
    # 关闭键悬停变红 —— 这是大家都习惯的暗示，也和暗色主题搭。
    style.configure("Window.TButton", background=p["surface"], foreground=p["muted"],
                    padding=(7, 2), borderwidth=0, focusthickness=0, font=icon_font)
    style.map("Window.TButton",
              background=[("pressed", p["surface_press"]), ("active", p["surface_hi"])],
              foreground=[("pressed", p["text"]), ("active", p["text"])])
    style.configure("WindowClose.TButton", background=p["surface"],
                    foreground=p["muted"], padding=(7, 2), borderwidth=0,
                    focusthickness=0, font=icon_font)
    style.map("WindowClose.TButton",
              background=[("pressed", "#d32f2f"), ("active", p["danger"])],
              foreground=[("pressed", "#ffffff"), ("active", "#ffffff")])
    style.map("TButton",
              background=[("disabled", p["surface"]), ("pressed", p["surface_press"]),
                          ("active", p["surface_hi"])],
              foreground=[("disabled", p["muted"])])
    style.configure("Accent.TButton", background=p["accent"], foreground=p["on_accent"])
    style.map("Accent.TButton",
              background=[("pressed", p["accent_press"]), ("active", p["accent_hi"])],
              foreground=[("disabled", p["muted"])])
    style.configure("Danger.TButton", background=p["danger"], foreground="#ffffff")
    style.map("Danger.TButton",
              background=[("pressed", "#d32f2f"), ("active", "#ff6b68")])
    style.configure("Ghost.TButton", background=p["bg"], foreground=p["muted"],
                    padding=(8, 4))
    style.map("Ghost.TButton",
              background=[("active", p["surface"])],
              foreground=[("active", p["text"])])

    # 输入类控件
    for name in ("TEntry", "TSpinbox", "TCombobox"):
        style.configure(name, fieldbackground=p["surface"], background=p["surface"],
                        foreground=p["text"], padding=4)
        for options in ({"bordercolor": p["border"]}, {"arrowcolor": p["muted"]},
                        {"insertcolor": p["text"]}):
            try:
                style.configure(name, **options)
            except Exception:
                pass
        style.map(name, fieldbackground=[("readonly", p["surface"])],
                  foreground=[("disabled", p["muted"])])
    try:
        root.option_add("*TCombobox*Listbox.background", p["surface"])
        root.option_add("*TCombobox*Listbox.foreground", p["text"])
        root.option_add("*TCombobox*Listbox.selectBackground", p["accent"])
        root.option_add("*TCombobox*Listbox.selectForeground", p["on_accent"])
    except Exception:
        pass

    style.configure("TCheckbutton", background=p["bg"], foreground=p["text"],
                    focuscolor=p["bg"], padding=2)
    style.map("TCheckbutton",
              background=[("active", p["bg"])],
              foreground=[("disabled", p["muted"])])
    style.configure("Surface.TCheckbutton", background=p["surface"], foreground=p["text"],
                    focuscolor=p["surface"])

    style.configure("TLabelframe", background=p["bg"], relief="solid", borderwidth=1)
    try:
        style.configure("TLabelframe", bordercolor=p["border"])
    except Exception:
        pass
    style.configure("TLabelframe.Label", background=p["bg"], foreground=p["muted"],
                    font=(family, max(8, size - 1)))
    style.configure("TSeparator", background=p["border"])

    style.configure("TNotebook", background=p["bg"], borderwidth=0)
    style.configure("TNotebook.Tab", background=p["bg"], foreground=p["muted"],
                    padding=(12, 6), borderwidth=0)
    style.map("TNotebook.Tab",
              background=[("selected", p["surface"])],
              foreground=[("selected", p["text"])])

    style.configure("Treeview", background=p["surface"], fieldbackground=p["surface"],
                    foreground=p["text"], borderwidth=0, rowheight=size + 14)
    style.map("Treeview",
              background=[("selected", p["accent"])],
              foreground=[("selected", p["on_accent"])])
    style.configure("Treeview.Heading", background=p["bg"], foreground=p["muted"],
                    relief="flat", padding=(6, 4))
    style.map("Treeview.Heading", background=[("active", p["surface"])])

    style.configure("Vertical.TScrollbar", background=p["surface_hi"],
                    troughcolor=p["bg"], borderwidth=0, width=10)
    for options in ({"bordercolor": p["bg"]}, {"arrowcolor": p["muted"]}):
        try:
            style.configure("Vertical.TScrollbar", **options)
        except Exception:
            pass
    style.map("Vertical.TScrollbar", background=[("active", p["accent"])])
    style.configure("Horizontal.TScrollbar", background=p["surface_hi"],
                    troughcolor=p["bg"], borderwidth=0)
    style.configure("TSeparator", background=p["border"])
    return style


class Tooltip:
    """鼠标悬停提示（LunaTranslator 的按钮都带提示，我们照做）。"""

    def __init__(self, widget: tk.Misc, text: str, delay: int = 350) -> None:
        self.widget = widget
        self.text = text
        self.delay = delay
        self._tip = None
        self._job = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None) -> None:
        self._cancel()
        try:
            self._job = self.widget.after(self.delay, self._show)
        except Exception:
            self._job = None

    def _cancel(self) -> None:
        if self._job is not None:
            try:
                self.widget.after_cancel(self._job)
            except Exception:
                pass
            self._job = None

    def _show(self) -> None:
        if self._tip is not None or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + 6
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
            tip = tk.Toplevel(self.widget)
            tip.wm_overrideredirect(True)
            # 主窗口默认是 topmost 的；提示窗不设 topmost 就会被主窗口挡在后面，
            # 看起来就是"提示被框体遮住"。这里必须一起置顶。
            try:
                tip.attributes("-topmost", True)
            except Exception:
                pass
            tip.wm_geometry("+%d+%d" % (x, y))
            tk.Label(tip, text=self.text, justify="left",
                     background=PALETTE["surface"], foreground=PALETTE["text"],
                     relief="solid", borderwidth=1, padx=8, pady=4,
                     font=("Microsoft YaHei", 9)).pack()
            # 靠近屏幕底部时改放到控件上方，避免提示被屏幕边缘截断
            try:
                tip.update_idletasks()
                height = tip.winfo_height()
                if y + height > self.widget.winfo_screenheight() - 4:
                    tip.wm_geometry("+%d+%d" % (x, max(0, self.widget.winfo_rooty()
                                                       - height - 4)))
            except Exception:
                pass
            self._tip = tip
        except Exception:
            self._tip = None

    def _hide(self, _event=None) -> None:
        self._cancel()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None


def tool_button(parent: tk.Misc, text: str, command: Callable, tip: str = "",
                style: str = "Ghost.TButton", width: Optional[int] = None) -> ttk.Button:
    button = ttk.Button(parent, text=text, command=command, style=style)
    if width:
        button.configure(width=width)
    button.pack(side="left", padx=2)
    if tip:
        Tooltip(button, tip)
    return button


def label(parent: tk.Misc, text: str = "", muted: bool = False,
          surface: bool = False, **kwargs) -> tk.Label:
    """统一配色的普通标签（tk.Label 不吃 ttk 主题，得手动上色）。"""
    kwargs.setdefault("bg", PALETTE["surface"] if surface else PALETTE["bg"])
    kwargs.setdefault("fg", PALETTE["muted"] if muted else PALETTE["text"])
    return tk.Label(parent, text=text, **kwargs)


def text_widget(parent: tk.Misc, **kwargs) -> tk.Text:
    """统一配色的文本框（输入框/结果框）。"""
    kwargs.setdefault("bg", PALETTE["surface"])
    kwargs.setdefault("fg", PALETTE["text"])
    kwargs.setdefault("insertbackground", PALETTE["text"])
    kwargs.setdefault("bd", 0)
    kwargs.setdefault("highlightthickness", 1)
    kwargs.setdefault("highlightbackground", PALETTE["border"])
    kwargs.setdefault("highlightcolor", PALETTE["accent"])
    return tk.Text(parent, **kwargs)


def prepare_window(window: tk.Misc, config: Optional[dict] = None) -> None:
    """给对话框装主题（所有对话框都调一次，保证和主界面一致）。"""
    install(window, config or {})
    try:
        window.configure(bg=PALETTE["bg"])
    except Exception:
        pass
    apply_dark_titlebar(window)


def apply_dark_titlebar(window: tk.Misc) -> None:
    """让 Windows 原生标题栏也变成深色（没启用无边框时用得到）。

    属性号 20 是 Win10 20H1+ 的 DWMWA_USE_IMMERSIVE_DARK_MODE，19 是更早版本的。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id()) or window.winfo_id()
        value = ctypes.c_int(1)
        for attribute in (20, 19):
            try:
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, attribute, ctypes.byref(value),
                        ctypes.sizeof(value)) == 0:
                    break
            except Exception:
                continue
    except Exception:
        pass


def find_first_input(root: tk.Misc):
    """找一个对话框里最像"输入框"的控件（自动聚焦用）。

    优先真正的输入框（Entry/Text），实在没有才退回下拉框 —— 免得一打开设置
    就把焦点放在下拉框上，用户按空格/方向键会误改选项。
    """

    def scan(widget, kinds):
        for child in widget.winfo_children():
            if isinstance(child, kinds):
                return child
            found = scan(child, kinds)
            if found is not None:
                return found
        return None

    return scan(root, (tk.Text, tk.Entry, ttk.Entry)) or scan(root, (ttk.Combobox,))


def find_dialog_input(window: tk.Misc):
    """这个对话框的"输入框"：优先用调用方显式指定的那个，否则猜第一个。"""
    target = getattr(window, "_dialog_input", None)
    if target is not None:
        try:
            if target.winfo_exists():
                return target
        except Exception:
            pass
    return find_first_input(window)


def set_dialog_input(window: tk.Misc, widget: tk.Misc) -> None:
    """告诉主题"这个窗口的输入框是哪个控件"。

    必须由对话框**在创建完控件之后**调用：像"纠错窗口"这种，窗口里第一个
    Text 是只读的原文框，光靠"猜第一个"会猜错，所以让调用方说清楚。
    """
    try:
        window._dialog_input = widget
    except Exception:
        return
    if getattr(window, "_dialog_autofocus", False):
        try:
            widget.focus_force()
        except Exception:
            pass


def _focus_is_outside(window: tk.Misc) -> bool:
    """本对话框现在没有键盘焦点（焦点在别的窗口或别的程序里）？"""
    try:
        current = window.focus_get()
    except Exception:
        current = None
    if current is None:
        return True
    return not str(current).startswith(str(window))


def install_dialog_focus(window: tk.Misc, autofocus: bool = False) -> None:
    """让无边框对话框"点一下就能打字"。

    背景（用户实测反馈）：中译英窗口有时候点回去打不了字。
    原因是两层：
      1. `focus_set()` 在窗口还没映射（还没显示出来）时是**静默无效**的，
         所以"打开时自动聚焦输入框"这行代码经常根本没生效；
      2. 对话框是 overrideredirect（无边框）窗口，Windows 不保证点它就给它键盘焦点。
    所以这里几处一起兜：
      * 点对话框里任何地方时，用 focus_force 把窗口激活，并把焦点给到点的地方
        （点输出框就交给输出框，方便框选复制；点按钮/空白就给输入框）；
      * autofocus=True 的对话框（中译英、纠错这种"打开就是要打字"的），
        显示后和重新获得焦点时自动聚焦到输入框。
    """

    def focus_input(*_args) -> None:
        widget = find_dialog_input(window) or window
        try:
            widget.focus_force()
        except Exception:
            pass

    if autofocus:
        try:
            window._dialog_autofocus = True
        except Exception:
            pass
        try:
            window.after(80, focus_input)
            window.after(400, focus_input)   # 映射慢的机器上再来一次
        except Exception:
            pass

    def on_focus_in(event) -> None:
        # 只有焦点原本不在本对话框里时才动手，免得抢走用户在输出框里选好的文字
        if autofocus and event.widget is window and _focus_is_outside(window):
            focus_input()

    def on_click(event) -> None:
        widget = event.widget
        if isinstance(widget, (tk.Text, tk.Entry, ttk.Entry, ttk.Combobox)):
            target = widget                     # 点在输入/输出框上，尊重用户的选择
        else:
            target = find_dialog_input(window) or window
        try:
            target.focus_force()                # 顺手把窗口激活（无边框窗口必须显式要焦点）
        except Exception:
            pass

    try:
        window.bind("<FocusIn>", on_focus_in, add="+")
        window.bind("<Button-1>", on_click, add="+")
    except Exception:
        pass


def frameless_dialog(window: tk.Misc, title: str, topmost: bool = True,
                     on_close=None, autofocus: bool = False):
    """把对话框变成"无边框 + 自绘深色标题栏"。

    返回 (标题栏控件, FramelessWindow 实例)；标题栏本身就是拖动区域。
    原来的白色系统标题栏会和暗色主题打架，所以对话框统一走这里。
    """
    try:
        window.overrideredirect(True)
    except Exception:
        pass
    if topmost:
        try:
            window.attributes("-topmost", True)
        except Exception:
            pass

    header = ttk.Frame(window, style="Surface.TFrame")
    header.pack(side="top", fill="x")
    label = ttk.Label(header, text=title, style="SurfaceMuted.TLabel")
    label.pack(side="left", padx=(10, 8), pady=6)
    close_button = ttk.Button(header, text="✕", width=2, style="Icon.TButton",
                              command=on_close or window.destroy)
    close_button.pack(side="right", padx=(0, 6), pady=3)
    Tooltip(close_button, "关闭")
    try:
        label.configure(cursor="fleur")
    except Exception:
        pass

    helper = FramelessWindow(window, drag_handles=[header, label],
                             min_size=(360, 240))
    helper.set_enabled(True)
    install_dialog_focus(window, autofocus=autofocus)
    return header, helper


def install_overlay_styles(root: tk.Misc, key_color: str = KEY_COLOR) -> None:
    """为"只透明背景"模式准备一套以颜色键为底色的样式。

    这些样式只用在主窗口的那几个大面（工具条/卡片/状态栏），
    这样它们的空白区域会变成透明，而按钮和文字照常显示。
    """
    style = ttk.Style(root)
    p = PALETTE
    for name, options in (
        ("Key.TFrame", {"background": key_color}),
        ("Key.TLabel", {"background": key_color, "foreground": p["text"]}),
        ("KeyMuted.TLabel", {"background": key_color, "foreground": p["muted"]}),
        ("KeyStatus.TLabel", {"background": key_color, "foreground": p["muted"]}),
        ("KeyOk.TLabel", {"background": key_color, "foreground": p["ok"]}),
        ("KeyWarn.TLabel", {"background": key_color, "foreground": p["warn"]}),
        ("KeyError.TLabel", {"background": key_color, "foreground": p["danger"]}),
        ("Key.TSeparator", {"background": key_color}),
        ("Key.Vertical.TScrollbar", {"background": p["surface_hi"],
                                     "troughcolor": key_color, "borderwidth": 0}),
        ("Key.Horizontal.TScrollbar", {"background": p["surface_hi"],
                                       "troughcolor": key_color, "borderwidth": 0}),
    ):
        try:
            style.configure(name, **options)
        except Exception:
            pass


def key_config(config: dict, key_color: str = KEY_COLOR) -> dict:
    """返回一份把显示底色换成颜色键的配置副本（给聊天区用）。"""
    copied = dict(config)
    copied["bg_color"] = key_color
    return copied


def apply_chat_theme(text_widget: tk.Text, config: dict) -> None:
    """聊天显示区的样式（沿用 app.ui.style 里的逐项设置，只补边框和内边距）。"""
    style_module.apply_widget_style(text_widget, config)
    style_module.apply_tags(text_widget, config)
