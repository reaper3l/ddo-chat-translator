"""主窗口。

界面只做三件事：把翻译结果显示好、把用户的操作转成命令、把学习入口摆出来。
所有后台动作都在 Pipeline 里，主线程只通过队列收结果——不会出现
后台线程直接写 Tk 控件那种随机崩溃。
"""
from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, ttk

from .. import channels
from .. import config as config_module
from .. import disclaimer
from .. import paths, textutil
from ..glossary import build_glossary
from ..pipeline import DisplayItem, Pipeline
from ..store import MemoryStore
from .agreement import AgreementDialog
from .cn2en import CnToEnDialog
from .learn import CorrectionDialog, DictionaryDialog, LearningCenterDialog
from .region import RegionPicker, show_preview
from .settings import SettingsDialog
from . import style
from . import theme
from .frameless import FramelessWindow

from .. import AUTHOR, HOMEPAGE, __version__          # noqa: E402

APP_TITLE = "DDO 翻译助手 v%s" % __version__

# 频道小灯上的短名：得能一眼区分（"公会"和"公共"不能都写"公"）
# 频道刚来消息时，小灯亮白框的持续时间（秒）
CHANNEL_PULSE_SECONDS = 2.0
# 频道小灯（Canvas 画的圆角色块）
LAMP_BAR_HEIGHT = 24
LAMP_DOT_WIDTH = 12          # 窗口很窄时只画一个圆点
LAMP_MIN_WIDTH = 26
LAMP_GAP = 2
LAMP_LEFT = 1               # 画布左边留 1px，圆角才不会被切
STRIP_PAD = 6               # 灯条左右的呼吸空间（算进容器宽度里，别让小灯被裁）
# 工具条上除灯条之外那些东西占的宽度（品牌字 + 监听按钮 + 两个窗口按钮 + 内边距）
TOOLBAR_OTHER_PADDING = 20
# 工具条那一行用的是"面板色"（Surface.TFrame = PALETTE["surface"]）。
# 灯条容器必须用同一个颜色，否则会出现一块比工具条更暗的方块（用户反馈"突界"）
TOOLBAR_BG = theme.PALETTE["surface"]
# 工具条收起/展开的过渡动画
TOOLBAR_HEIGHT = 32
ANIMATION_STEPS = 8
ANIMATION_INTERVAL_MS = 16   # 8 × 16ms ≈ 130ms

# 工具栏按钮：(图标, 文字, 方法名, 悬停说明)
ACTION_BUTTONS = (
    ("⊞", "区域", "select_region", "框选游戏聊天框；框完会自动抓一帧给你确认"),
    ("◎", "测试", "test_region", "抓一帧看看识别到什么（F5）"),
    ("⇄", "互译", "open_cn2en",
     "中英互译（自动识别方向）：中文→英文、英文→中文；F9 直接翻译剪贴板"),
    ("✎", "纠错", "fix_selected", "选中一条译文后改成正确的中文（F10）"),
    ("▤", "词典", "open_dictionary", "术语表：这里面的词会被保护，不让模型乱翻"),
    ("✦", "学习", "open_learning", "待学习词 / 已学习 / 纠错历史"),
    ("⚙", "设置", "open_settings", "引擎、区域、外观、学习阈值"),
)

# 底部状态栏右侧那排计数的悬停说明（前面会拼上"当前引擎"）
STATS_TIP = ("待译=排队中　缓存=命中本地缓存　调用=已请求接口次数\n"
             "记忆=命中你纠正过的句子　跳过=画面没变省掉的 OCR 次数\n"
             "错误=接口失败次数")


class MainWindow:
    def __init__(self) -> None:
        paths.ensure_dirs()
        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self._setup_scaling()

        self.config = config_module.load_config()
        self.memory = MemoryStore()
        self.glossary = build_glossary(self.config, self.memory)
        self.ui_queue: "queue.Queue[dict]" = queue.Queue()
        self.pipeline = Pipeline(self.config, self.memory, self.glossary, self.ui_queue)

        self.records = []          # 已渲染的消息，用于纠错定位
        self._region_callback = None
        self._last_flush = time.time()
        self._dialogs = {}
        self._first_notice_shown = False
        self._agreement_dialog = None

        self._build_ui()
        self._bind_keys()
        self._apply_visual_config()

        size = self.config.get("window_size") or self._default_window_size()
        self.root.geometry("%dx%d" % (int(size[0]), int(size[1])))
        position = self.config.get("window_pos")
        if position and len(position) == 2:
            self.root.geometry("+%d+%d" % (int(position[0]), int(position[1])))

        self.root.protocol("WM_DELETE_WINDOW", self.quit_app)
        self._publish_screen_size()
        self.root.after(100, self._poll)
        # 程序一开始先过"使用须知"这一关：同意了才提示怎么用、才去查更新
        self.root.after(200, self.startup_gate)

    def _publish_screen_size(self) -> None:
        """把 Tk 的屏幕尺寸告知流水线（用于截图坐标换算）。"""
        try:
            self.pipeline.set_screen_size(self.root.winfo_screenwidth(),
                                          self.root.winfo_screenheight())
        except Exception:
            pass

    # ------------------------------------------------------------------ DPI
    def _setup_scaling(self) -> None:
        """界面缩放。

        默认按 96 DPI 的经典比例（也就是不跟着显示器 DPI 放大），这样窗口、
        按钮、字号都小巧，占游戏画面的地方少。想放大/缩小改 设置 → 外观 → 界面缩放。
        """
        try:
            scale = float(self.config.get("ui_scale", 1.0) or 1.0)
        except Exception:
            scale = 1.0
        scale = max(0.75, min(1.6, scale))
        self.scale = scale
        try:
            self.root.tk.call("tk", "scaling", (96.0 / 72.0) * scale)
        except Exception:
            pass
        try:
            self.dpi = int(round(self.root.winfo_fpixels("1i")))
        except Exception:
            self.dpi = 96

    def _default_window_size(self):
        scale = min(max(getattr(self, "scale", 1.0), 0.75), 1.6)
        return [int(440 * scale), int(540 * scale)]

    def dpi_info(self) -> str:
        """一句给用户看的坐标信息，出问题时把它发我就能定位。"""
        try:
            scaling = float(self.root.tk.call("tk", "scaling"))
        except Exception:
            scaling = 96.0 / 72.0
        try:
            width = self.root.winfo_screenwidth()
            height = self.root.winfo_screenheight()
            screen = "%dx%d" % (width, height)
        except Exception:
            width = height = 0
            screen = "?"
        from .. import capture

        space = capture.capture_space()
        scale = capture.active_scale((width, height)) if width else (1.0, 1.0)
        return ("屏幕 %s · 显示器 DPI %d · 界面缩放 %.0f%% · 截图空间 %s · 换算 x%.3f/y%.3f"
                % (screen, getattr(self, "dpi", 96), 100 * getattr(self, "scale", 1.0),
                   ("%dx%d" % space) if space else "?",
                   scale[0], scale[1]))

    # ------------------------------------------------------------------ 界面
    def _build_ui(self) -> None:
        theme.install(self.root, self.config)

        # 程序图标（源码运行 / 打包后都找得到）
        try:
            icon = paths.icon_path()
            if icon.exists():
                self.root.iconbitmap(default=str(icon))
        except Exception:
            pass

        # ---------------- 顶部工具条 ----------------
        top = ttk.Frame(self.root, style="Surface.TFrame")
        top.pack(side="top", fill="x")

        self.brand = ttk.Label(top, text="DDO 聊天翻译", style="SurfaceMuted.TLabel")
        self.brand.pack(side="left", padx=(8, 8), pady=7)
        self.brand.bind("<Button-1>", lambda _e: self._toggle_toolbar())
        # 折叠开关就做在标题上（不再单独放一个小三角按钮，省地方）
        self.brand_tooltip = theme.Tooltip(self.brand, "点「DDO」展开工具按钮")
        try:
            self.brand.configure(cursor="hand2")
        except Exception:
            pass

        self.monitor_button = ttk.Button(top, text="▶", width=3,
                                         style="IconAccent.TButton",
                                         command=self.toggle_monitor)
        self.monitor_button.pack(side="left", pady=4)
        self.monitor_tooltip = theme.Tooltip(
            self.monitor_button, "开始监听（F8）\n点一下开始持续截图识别游戏聊天框")

        self.separator = ttk.Separator(top, orient="vertical")
        self.separator.pack(side="left", fill="y", padx=4, pady=6)

        # 功能按钮放在一个"宽度可变"的容器里：收起/展开时按帧改宽度，就有了滑动动画
        actions_holder = tk.Frame(top, bg=TOOLBAR_BG,
                                  width=0, height=TOOLBAR_HEIGHT)
        actions_holder.pack(side="left")
        actions_holder.pack_propagate(False)
        self.actions_holder = actions_holder
        actions = ttk.Frame(actions_holder, style="Surface.TFrame")
        actions.pack(side="left", fill="y")
        self.actions = actions
        self._build_actions()

        # 窗口按钮：**关闭在最右、最小化在它左边**（和 Windows 一致）。
        # 侧边 pack 的顺序决定位置：先 pack 的在最右边，所以先 pack 关闭键。
        window_buttons = ttk.Frame(top, style="Surface.TFrame")
        window_buttons.pack(side="right", padx=(4, 6))
        self.quit_button = ttk.Button(window_buttons, text="✕", width=2,
                                      style="WindowClose.TButton",
                                      command=self.quit_app)
        self.quit_button.pack(side="right")
        theme.Tooltip(self.quit_button, "退出程序")
        self.min_button = ttk.Button(window_buttons, text="—", width=2,
                                     style="Window.TButton", command=self._minimize)
        theme.Tooltip(self.min_button, "最小化（无边框模式下会先恢复系统边框，方便从任务栏找回）")
        self.window_buttons = window_buttons

        # 灯条容器放在最后 pack：右边那两个窗口按钮要先占住地方，
        # 免得窗口很窄时灯条把它们挤出去（✕ 都点不到就麻烦了）
        strip_holder = tk.Frame(top, bg=TOOLBAR_BG,
                                width=0, height=TOOLBAR_HEIGHT)
        strip_holder.pack(side="left")
        strip_holder.pack_propagate(False)
        self.strip_holder = strip_holder
        self._build_channel_strip(strip_holder, top)

        # ---------------- 底部状态栏（先占位，免得被显示区挤掉） ----------------
        status_bar = ttk.Frame(self.root)
        status_bar.pack(side="bottom", fill="x", padx=10, pady=(2, 6))
        self.state_dot = tk.Label(status_bar, text="○", bg=theme.PALETTE["bg"],
                                  fg=theme.PALETTE["muted"])
        self.state_dot.pack(side="left")
        self.status_var = tk.StringVar(value="准备就绪")
        self.status_label = ttk.Label(status_bar, textvariable=self.status_var,
                                      style="Status.TLabel")
        self.status_label.pack(side="left", padx=(5, 0))
        self.stats_var = tk.StringVar(value="")
        self.stats_label = ttk.Label(status_bar, textvariable=self.stats_var,
                                     style="Status.TLabel")
        self.stats_label.pack(side="right")
        self.grip = tk.Label(status_bar, text="◢", bg=theme.PALETTE["bg"],
                             fg=theme.PALETTE["muted"], cursor="size_nw_se")
        self.grip.bind("<ButtonPress-1>",
                       lambda event: self.frameless.start_resize("se", event))
        self.grip.bind("<B1-Motion>", lambda event: self.frameless.drag(event))
        self.grip.bind("<ButtonRelease-1>", lambda event: self.frameless.release(event))
        theme.Tooltip(self.grip, "拖动这里可以缩放窗口")
        # 悬停提示里带上"当前引擎"——状态栏本身放不下（并且用户反馈太占地方）
        self.stats_tooltip = theme.Tooltip(self.stats_label, STATS_TIP)

        # ---------------- 中间：聊天卡片 ----------------
        card = ttk.Frame(self.root, style="Card.TFrame")
        card.pack(fill="both", expand=True, padx=10, pady=(8, 2))
        self.card = card
        self.scrollbar = ttk.Scrollbar(card, orient="vertical")
        self.scrollbar.pack(side="right", fill="y")
        self.text = tk.Text(card, wrap="word", undo=False, bd=0, highlightthickness=0,
                            yscrollcommand=self.scrollbar.set, padx=10, pady=8,
                            insertbackground="#ffffff")
        self.text.pack(side="left", fill="both", expand=True, padx=1, pady=1)
        self.scrollbar.config(command=self.text.yview)
        self.text.configure(state="disabled")

        self.menu = tk.Menu(self.root, tearoff=0,
                            bg=theme.PALETTE["surface"], fg=theme.PALETTE["text"],
                            activebackground=theme.PALETTE["accent"],
                            activeforeground=theme.PALETTE["on_accent"],
                            bd=0, activeborderwidth=0)
        self.menu.add_command(label="纠正这条翻译 (F10)", command=self.fix_selected)
        self.menu.add_command(label="复制选中内容", command=self.copy_selection)
        self.menu.add_separator()
        self.menu.add_command(label="中英互译 (Ctrl+Enter 发送)", command=self.open_cn2en)
        self.menu.add_command(label="显示/隐藏英文原文", command=self.toggle_original)
        self.menu.add_separator()
        self.menu.add_command(label="词典", command=self.open_dictionary)
        self.menu.add_command(label="学习中心", command=self.open_learning)
        self.menu.add_command(label="设置", command=self.open_settings)
        self.menu.add_separator()
        self.menu.add_command(label="反馈问题（自动带上日志/翻译记录）",
                              command=self.open_bug_report)
        self.menu.add_command(label="清空显示区", command=self.clear_display)
        self.menu.add_command(label="退出", command=self.quit_app)
        self.text.bind("<Button-3>", self._show_menu)

        self._configure_tags()
        # 无边框窗口：拖动标题栏/状态栏移动，边缘和右下角缩放
        self.frameless = FramelessWindow(
            self.root, drag_handles=[top, self.brand, status_bar])
        self.top_frame = top
        self.status_bar = status_bar
        self._status_styles = self._status_style_map(False)
        theme.install_overlay_styles(self.root)
        self._apply_toolbar_collapsed()
        self._apply_status_bar()
        self._apply_frameless()
        self._apply_transparency()

    def _build_actions(self) -> None:
        """按当前设置重建工具栏按钮（图标 + 文字，或只显示图标）。"""
        for child in self.actions.winfo_children():
            child.destroy()
        icons_only = bool(self.config.get("toolbar_icons_only", False))
        for icon, label, method, tip in ACTION_BUTTONS:
            command = getattr(self, method, None)
            if command is None:
                continue
            text = icon if icons_only else "%s %s" % (icon, label)
            button = ttk.Button(self.actions, text=text,
                                style="Icon.TButton" if icons_only else "Compact.TButton",
                                command=command)
            if icons_only:
                button.configure(width=2)
            button.pack(side="left", padx=0)
            theme.Tooltip(button, "%s：%s" % (label, tip) if icons_only else tip)
        # 图标/文字模式切换会影响监听按钮的宽度和样式，这里同步一次
        if hasattr(self, "stats_var"):
            try:
                self._update_stats()
            except Exception:
                pass

    # ------------------------------------------------- 工具条收起后的那块留白
    # 收起工具条后，▶ 到 ✕ 之间会空出一大片。那里放"频道小灯"：
    #   颜色 = 该频道的颜色（和游戏里一致）；亮=显示中，暗=已隐藏；
    #   刚来消息的那盏会亮一圈白框；**点一下就能开关这个频道**（和设置里的开关同一个）。
    # 用 Canvas 画圆角色块（Label 只能画方块，不好看），鼠标悬停/点击按坐标命中。
    def _build_channel_strip(self, holder, toolbar) -> None:
        """holder：宽度可变的容器（动画用）；toolbar：整条工具条（量可用宽度用）。"""
        parent = holder
        strip = tk.Frame(parent, bg=TOOLBAR_BG)
        self._top_container = toolbar
        self.channel_strip = strip
        self._channel_activity = {}
        self._lamp_boxes = []              # [(x1, y1, x2, y2, 频道名), ...]
        self._lamp_hover = ""
        self._strip_refreshed = 0.0
        family = self.config.get("font_family", "Microsoft YaHei")
        self._lamp_font = tkfont.Font(family=family, size=8)
        self._lamp_canvas = tk.Canvas(strip, height=LAMP_BAR_HEIGHT, bd=0,
                                      highlightthickness=0,
                                      bg=TOOLBAR_BG, cursor="hand2")
        self._lamp_canvas.pack(side="left",
                               pady=max(0, (TOOLBAR_HEIGHT - LAMP_BAR_HEIGHT) // 2))
        # 一个提示窗给所有小灯共用：悬停时换文字（每个灯单独建提示太浪费）
        self._lamp_tooltip = theme.Tooltip(self._lamp_canvas, "")
        self._lamp_canvas.bind("<Motion>", self._on_lamp_motion)
        self._lamp_canvas.bind("<Leave>",
                               lambda _e: self._set_lamp_hover(""))
        self._lamp_canvas.bind("<Button-1>", self._on_lamp_click)

        self._strip_stage = None
        self._lamp_compact = False
        self._refresh_channel_strip()
        # 窗口变窄时逐级"瘦身"：带名字 → 只留开着的 → 纯色点 → 整条藏起来
        toolbar.bind("<Configure>", lambda _e: self._fit_channel_strip(), add="+")

    # ------------------------------------------------------------ 小灯的绘制
    def _channel_items(self):
        """当前生效的频道列表（可自定义，见 app/channels.py）。"""
        return channels.effective(self.config)

    def _strip_items(self):
        """要显示在小灯里的频道：用户可以在 设置 → 频道 里逐项勾选（strip 字段）。"""
        return [entry for entry in self._channel_items()
                if bool(entry.get("strip", True))]

    @staticmethod
    def _round_rect(canvas, x1, y1, x2, y2, radius, **kwargs):
        """Canvas 没有圆角矩形，用平滑多边形凑一个（看着比方块舒服）。"""
        points = [x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
                  x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
                  x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1]
        return canvas.create_polygon(points, smooth=True, **kwargs)

    def _lamp_width(self, label: str, compact: bool) -> int:
        """一个小灯需要多宽（自己算，不依赖 Tk 的布局回合 —— 那个会慢一拍）。"""
        if compact:
            return LAMP_DOT_WIDTH
        text_width = self._lamp_font.measure(label) if label else 0
        return max(LAMP_MIN_WIDTH, text_width + 7)

    # 档位：从"信息最全"到"最能省地方"，按顺序试，第一个放得下就用它
    STRIP_STAGES = ("all_names", "on_names", "dots", "on_dots", "hidden")

    def _stage_width(self, stage: str) -> int:
        """某一档需要多宽（纯计算，不改界面 —— 免得试档位时把画面弄乱）。

        宽度 = 左右呼吸空间 + 画布起点 + 每个小灯（含间隙）。
        必须把这些都算上：容器窄 1px，最后一个小灯的圆角就会被切掉。
        """
        if stage == "hidden":
            return 0
        compact = stage in ("dots", "on_dots", "hidden")
        only_on = stage in ("on_names", "on_dots")
        width = STRIP_PAD + LAMP_LEFT
        for entry in self._strip_items():
            if only_on and not bool(entry["enabled"]):
                continue
            label = "" if compact else channels.short_name(str(entry["name"]))
            width += self._lamp_width(label, compact) + LAMP_GAP
        return max(1, width)

    def _draw_lamps(self) -> None:
        """把频道小灯画到 Canvas 上（状态/悬停/刚说话都在这里体现）。"""
        canvas = self._lamp_canvas
        canvas.delete("all")
        self._lamp_boxes = []
        base = TOOLBAR_BG
        now = time.time()
        compact = self._lamp_compact
        x = LAMP_LEFT
        for entry in self._strip_items():
            name = str(entry["name"])
            color = str(entry["color"])
            is_on = bool(entry["enabled"])
            if getattr(self, "_lamp_only_on", False) and not is_on:
                continue                     # 地方不够时只画开着的频道
            pulsing = (now - self._channel_activity.get(name, 0.0)) < CHANNEL_PULSE_SECONDS
            hovered = (name == self._lamp_hover)
            label = "" if compact else channels.short_name(name)
            width = self._lamp_width(label, compact)
            x1, y1 = x, 4
            x2, y2 = x + width, LAMP_BAR_HEIGHT - 4

            if is_on:
                fill = color
                text_color = theme.text_on(color)
            else:
                fill = theme.mix(base, color, 0.18)
                text_color = theme.mix(color, base, 0.35)
            if hovered:
                fill = theme.mix(fill, "#ffffff", 0.18)
            outline = ""
            outline_width = 0
            if pulsing and is_on:
                outline = theme.mix(color, "#ffffff", 0.55)
                outline_width = 2
            elif not is_on:
                outline = theme.mix(base, color, 0.45)
                outline_width = 1
            self._round_rect(canvas, x1, y1, x2, y2, 5, fill=fill,
                             outline=outline, width=outline_width)
            if label:
                canvas.create_text((x1 + x2) / 2, (y1 + y2) / 2, text=label,
                                   fill=text_color, font=self._lamp_font)
            self._lamp_boxes.append((x1, y1, x2, y2, name))
            x = x2 + LAMP_GAP
        canvas.configure(width=max(1, x))

    def _lamp_at(self, event) -> str:
        for x1, y1, x2, y2, name in self._lamp_boxes:
            if x1 <= event.x <= x2 and y1 <= event.y <= y2:
                return name
        return ""

    def _set_lamp_hover(self, name: str) -> None:
        if name == self._lamp_hover:
            return
        self._lamp_hover = name
        self._draw_lamps()

    def _on_lamp_motion(self, event) -> None:
        name = self._lamp_at(event)
        self._set_lamp_hover(name)
        if name:
            enabled = channels.enabled_map(self.config)
            is_on = bool(enabled.get(name, True))
            self._lamp_tooltip.text = "%s：%s\n点一下%s这个频道" % (
                name, "显示中" if is_on else "已隐藏（不显示、不翻译）",
                "隐藏" if is_on else "显示")

    def _on_lamp_click(self, event) -> None:
        name = self._lamp_at(event)
        if name:
            self._toggle_channel(name)

    def _fit_channel_strip(self) -> None:
        """按当前窗口宽度给留白里的东西分级显示（窄窗口不能被撑爆）。"""
        try:
            if getattr(self, "_animating", False):
                return
            # 注意：这里**不能**因为"灯条当前没被布局"就 return ——
            # 窄到一定程度时灯条会整条收起来（档位 hidden），窗口再拖宽就再也回不来了
            # （用户实测：小圆点在缩放窗口时消失，得点一下 DDO 收放工具条才恢复）。
            # 展开状态下本来就不该显示灯条，那个判断在下面。
            if not bool(self.config.get("toolbar_collapsed", True)):
                return                       # 展开时灯条让位给功能按钮
            top_width = self._top_container.winfo_width()
            if top_width <= 1:
                # 窗口还没真正布局好（刚创建或还 withdraw 着）：过一会儿再量一次，
                # 试几次还不行就别再排计时器了，免得白转。
                if getattr(self, "_fit_retries", 0) < 10:
                    self._fit_retries = getattr(self, "_fit_retries", 0) + 1
                    self._top_container.after(60, self._fit_channel_strip)
                return
            self._fit_retries = 0
            # 算剩余空间时，"窗口按钮（— ✕）"要用**它需要的宽度**（reqwidth）：
            # 用实际宽度会死锁 —— 按钮被灯条挤扁以后实际宽度变小，于是算出来的
            # 空间反而更大，灯条继续占着地方，按钮永远回不来（用户实测：
            # 调小窗口后那个"缩小版小按钮"有时会消失，得点一下 DDO 才恢复）。
            used = 0
            for widget, padding in ((self.brand, 16), (self.monitor_button, 2)):
                width = widget.winfo_width()
                if width <= 1:
                    width = widget.winfo_reqwidth()
                used += width + padding
            used += self.window_buttons.winfo_reqwidth() + 8
            space = max(0, top_width - used - TOOLBAR_OTHER_PADDING)
            # 先算宽度（不动界面），选好档位再一次性套用
            chosen = None
            for stage in self.STRIP_STAGES:
                if self._stage_width(stage) <= space or stage == "hidden":
                    chosen = stage
                    break
            if chosen == self._strip_stage:
                self._sync_strip_width()
                return
            self._strip_stage = chosen
            self._apply_strip_stage(chosen)
            self._sync_strip_width()
            # 排完再确认一次：窗口按钮绝不能被灯条挤掉
            self.channel_strip.after_idle(self._ensure_window_buttons)
        except Exception:
            pass

    def _ensure_window_buttons(self) -> None:
        """安全网：最小化/关闭按钮必须一直在（被挤掉就把灯条再让一档）。

        灯条宽度是按"当前窗口宽度 - 其它控件"算的，正常不会挤到窗口按钮；
        但拖动缩放时 Tk 的布局是分几步收敛的，偶尔会先挤掉按钮 —— 这里兜一下，
        免得用户看到按钮莫名其妙消失、还得点 DDO 才能恢复。
        """
        try:
            if getattr(self, "_animating", False):
                return
            if not self._top_container.winfo_ismapped():
                return                      # 窗口还没显示（自检里是 withdraw 状态）
            if not bool(self.config.get("frameless", False)):
                return                      # 标准窗口有系统标题栏，不需要这两个按钮
            if self.min_button.winfo_ismapped() and self.quit_button.winfo_ismapped():
                return
            order = list(self.STRIP_STAGES)
            stage = self._strip_stage if self._strip_stage in order else order[0]
            index = order.index(stage)
            if index + 1 < len(order):
                self._strip_stage = order[index + 1]
                self._apply_strip_stage(self._strip_stage)
                self._sync_strip_width()
                self.channel_strip.after_idle(self._ensure_window_buttons)
        except Exception:
            pass

    def _sync_strip_width(self) -> None:
        """把灯条容器调到当前档位需要的宽度（动画进行中不插手）。"""
        if getattr(self, "_animating", False):
            return
        width = getattr(self, "_strip_width", 0)
        if width:
            try:
                self.strip_holder.configure(width=max(1, int(width)))
            except Exception:
                pass

    def _apply_strip_stage(self, stage: str) -> int:
        """套用某一级布局，返回它需要多宽（像素）。

        五级"瘦身"（窗口越窄越往后）：
            all_names 全部频道（带名字）
            on_names  只画开着的频道（带名字）—— 地方刚够时最有用的形态
            dots      全部频道（纯色圆点，鼠标悬停看名字）
            on_dots   只画开着的频道（纯色圆点）
            hidden    整条藏起来（窗口小到放不下时）
        """
        if stage not in self.STRIP_STAGES:       # 兜底：不认识的档位按"全部带名字"
            stage = "all_names"
        compact = stage in ("dots", "on_dots", "hidden")
        show_only_on = stage in ("on_names", "on_dots")

        if stage == "hidden":
            self.channel_strip.pack_forget()
            return 0
        if not self.channel_strip.winfo_manager():
            # 注意：这里**不能**给灯条加 padx —— 容器宽度是照着"灯需要的宽度"设的，
            # 再扣掉内边距就会把最后一个小灯的圆角切掉（用户反馈的"裁切"）
            self.channel_strip.pack(side="left", fill="both")

        self._lamp_compact = compact
        self._lamp_only_on = show_only_on
        self._draw_lamps()
        self._strip_width = self._stage_width(stage)
        # 顺手把容器宽度也对齐：任何调用方（含自检脚本）单独套档位时都不会出现
        # "容器比画布窄 → 最后一个小灯被裁"的情况
        self._sync_strip_width()
        return self._strip_width

    def _refresh_channel_strip(self) -> None:
        """重画频道小灯（颜色、开关状态、刚说话的白色脉冲框）。"""
        try:
            self._draw_lamps()
        except Exception:
            pass

    def _toggle_channel(self, channel: str) -> None:
        """点频道小灯 = 开关这个频道（和 设置 → 监控 里的勾选同一个项）。"""
        items = self._channel_items()
        target = None
        for entry in items:
            if str(entry["name"]) == channel:
                entry["enabled"] = not bool(entry["enabled"])
                target = entry
                break
        if target is None:
            return
        channels.sync(self.config, items)
        config_module.save_config(self.config)
        self._refresh_channel_strip()
        self._strip_stage = None            # 开关变了 → 重新分级（可能能多显示一点）
        self._fit_channel_strip()
        self.set_status("%s：%s" % (channel, "显示" if target["enabled"] else "已隐藏"),
                        "info")

    def _note_channel_activity(self, channel: str) -> None:
        """记下"这个频道刚说话"，让小灯闪一下白框。"""
        if channel:
            self._channel_activity[channel] = time.time()

    def _apply_frameless(self) -> None:
        """按设置切换窗口边框；无边框时显示最小化按钮和右下角缩放角。"""
        enabled = bool(self.config.get("frameless", False))
        try:
            self.frameless.set_enabled(enabled)
        except Exception:
            pass
        self._apply_frameless_buttons(enabled)

    def _apply_frameless_buttons(self, enabled: bool) -> None:
        """无边框时才需要"最小化"按钮和右下角缩放角（标准窗口有系统标题栏）。

        注意这段以前被写在 _apply_transparency 里、用的还是没定义过的变量，
        被 `except Exception: pass` 吞掉之后，最小化按钮就再也没出现过。
        """
        try:
            if enabled:
                # 注意：这里**不能**用 before=quit —— 侧边 pack 里"先 pack 的在最右边"，
                # 插到 quit 前面会把最小化挤到最右，就变成"✕ —"了（用户反馈要调换）。
                self.min_button.pack(side="right")
                self.grip.pack(side="right", before=self.stats_label)
            else:
                self.min_button.pack_forget()
                self.grip.pack_forget()
        except Exception as exc:               # 不能让界面悄悄失灵
            self.set_status("切换无边框按钮失败：%s" % exc, "warn")

    # ---------------------------------------------------- 工具条折叠 / 透明
    def _toggle_toolbar(self) -> None:
        """点标题或 ▸ 展开/收起工具按钮。"""
        self.config["toolbar_collapsed"] = not bool(
            self.config.get("toolbar_collapsed", True))
        config_module.save_config(self.config)
        self._apply_toolbar_collapsed(animate=True)
        self.set_status("工具按钮已%s" % ("收起" if self.config["toolbar_collapsed"] else "展开"),
                        "info")

    def _apply_toolbar_collapsed(self, animate: bool = False) -> None:
        collapsed = bool(self.config.get("toolbar_collapsed", True))
        try:
            # 标题本身就是开关：折叠时带个小箭头提示"这里还能展开"
            self.brand.configure(
                text=("DDO ▸" if collapsed
                      else ("DDO" if self.config.get("toolbar_icons_only", False)
                            else "DDO 聊天翻译")))
            try:
                self.brand_tooltip.text = ("点「DDO」展开工具按钮" if collapsed
                                           else "点「DDO」收起工具按钮")
            except Exception:
                pass
            # 折叠后整个窗口可以收得更小（无边框下我们自己限制缩放，标准窗口用 minsize）
            # 数值贴着实际内容来：折叠时工具条只要 ~180px，展开时 ~320px
            minimum = (200, 160) if collapsed else (340, 240)
            try:
                self.frameless.min_w, self.frameless.min_h = minimum
            except Exception:
                pass
            try:
                self.root.minsize(*minimum)
            except Exception:
                pass
            # 动画：功能按钮的容器和小灯条的容器"此消彼长"，看起来就是滑进滑出。
            # 窗口没显示出来（比如自检里 withdraw 了）或用户关掉了动画，就直接摆好。
            if animate and self.config.get("ui_animation", True) \
                    and self.root.winfo_ismapped():
                self._animate_toolbar(not collapsed)
            else:
                self._snap_toolbar(not collapsed)
        except Exception as exc:
            self.set_status("切换工具条失败：%s" % exc, "warn")

    def _snap_toolbar(self, expanded: bool) -> None:
        """不做动画，直接把工具条摆成最终状态。"""
        self._animating = False
        if expanded:
            self.strip_holder.configure(width=0)
            self.channel_strip.pack_forget()      # 灯条彻底撤掉，别占地方
            if not self.actions.winfo_manager():
                self.actions.pack(side="left", fill="y")
            self.actions_holder.configure(width=max(1, self.actions.winfo_reqwidth()))
            if not self.separator.winfo_manager():
                self.separator.pack(side="left", fill="y", padx=4, pady=6,
                                    before=self.actions_holder)
        else:
            self.separator.pack_forget()
            self.actions_holder.configure(width=0)
            self.actions.pack_forget()            # 功能按钮撤掉（容器留着给动画用）
            if not self.channel_strip.winfo_manager():
                self.channel_strip.pack(side="left", fill="both")
            self._strip_stage = None
            self._fit_channel_strip()
            if not getattr(self, "_strip_width", 0):
                # 还没量出档位（窗口刚建好、还没布局）——先给个自然宽度，等布局好再收
                self._strip_width = self.channel_strip.winfo_reqwidth()
            self._sync_strip_width()
            self.channel_strip.after_idle(self._ensure_window_buttons)

    def _animate_toolbar(self, expanded: bool) -> None:
        """工具条收起/展开的过渡动画（两个容器宽度此消彼长，约 130ms）。"""
        self._animating = False
        # 动画期间两边都要"在场"，才能看到滑动过程
        if not self.actions.winfo_manager():
            self.actions.pack(side="left", fill="y")
        if not self.channel_strip.winfo_manager():
            self.channel_strip.pack(side="left", fill="both")
        self._strip_stage = None
        self._fit_channel_strip()
        self._draw_lamps()
        self.root.update_idletasks()               # 让 reqwidth 反映最新分级
        actions_width = max(1, self.actions.winfo_reqwidth())
        strip_width = max(1, self._strip_width or self.channel_strip.winfo_reqwidth())
        self._animating = True

        def set_width(ratio: float) -> None:
            if expanded:
                actions_part, strip_part = actions_width * ratio, strip_width * (1 - ratio)
            else:
                actions_part, strip_part = actions_width * (1 - ratio), strip_width * ratio
            self.actions_holder.configure(width=int(actions_part))
            self.strip_holder.configure(width=int(strip_part))
            if actions_part > 8:
                if not self.separator.winfo_manager():
                    self.separator.pack(side="left", fill="y", padx=4, pady=6,
                                        before=self.actions_holder)
            else:
                self.separator.pack_forget()

        def step(index: int) -> None:
            ratio = index / float(ANIMATION_STEPS)
            set_width(1 - (1 - ratio) ** 3)        # ease-out：收尾更柔和
            if index < ANIMATION_STEPS:
                self.root.after(ANIMATION_INTERVAL_MS, step, index + 1)
            else:
                self._snap_toolbar(expanded)

        step(0)

    def _apply_status_bar(self) -> None:
        show = bool(self.config.get("show_status_bar", True))
        try:
            if show:
                self.status_bar.pack(side="bottom", fill="x", padx=10, pady=(2, 6))
            else:
                self.status_bar.pack_forget()
        except Exception:
            pass

    @staticmethod
    def _status_style_map(key_mode: bool) -> dict:
        if key_mode:
            return {"info": "KeyStatus.TLabel", "ok": "KeyOk.TLabel",
                    "warn": "KeyWarn.TLabel", "error": "KeyError.TLabel",
                    "muted": "KeyMuted.TLabel"}
        return {"info": "Status.TLabel", "ok": "Ok.TLabel", "warn": "Warn.TLabel",
                "error": "Error.TLabel", "muted": "Muted.TLabel"}

    def _set_surface_color(self, key_color) -> None:
        """把主窗口几个大面的底色切到颜色键（None = 正常主题色）。"""
        frame_style = "Key.TFrame" if key_color else "Surface.TFrame"
        card_style = "Key.TFrame" if key_color else "Card.TFrame"
        base = key_color or theme.PALETTE["bg"]
        scroll_style = ("Key.Vertical.TScrollbar" if key_color
                        else "Vertical.TScrollbar")
        self._status_styles = self._status_style_map(bool(key_color))
        for widget, style_name in (
            (self.top_frame, frame_style),
            (self.card, card_style),
            (self.scrollbar, scroll_style),
            (self.brand, self._status_styles["muted"]),
            (self.status_label, self._status_styles["info"]),
            (self.stats_label, self._status_styles["info"]),
        ):
            try:
                widget.configure(style=style_name)
            except Exception:
                pass
        for widget in (self.state_dot, self.grip):
            try:
                widget.configure(bg=base)
            except Exception:
                pass
        try:
            self.root.configure(bg=base)
        except Exception:
            pass

    def _apply_transparency(self) -> None:
        """背景透明：alpha=整窗半透明；key=只透明背景（文字和按钮保持清晰）。"""
        mode = str(self.config.get("transparency_mode", "off") or "off").lower()
        try:
            self.root.attributes("-alpha", 1.0)
        except Exception:
            pass
        try:
            self.root.attributes("-transparentcolor", "")
        except Exception:
            pass

        if mode == "alpha":
            try:
                value = float(str(self.config.get("alpha", "0.85")) or 0.85)
            except Exception:
                value = 0.85
            try:
                self.root.attributes("-alpha", max(0.3, min(1.0, value)))
            except Exception:
                pass
            self._set_surface_color(None)
        elif mode == "key":
            try:
                self.root.attributes("-transparentcolor", theme.KEY_COLOR)
            except Exception:
                self.set_status("当前系统不支持「只透明背景」，已按不透明处理", "warn")
            self._set_surface_color(theme.KEY_COLOR)
        else:
            self._set_surface_color(None)
        self._configure_tags()

    def _minimize(self) -> None:
        self.frameless.minimize()

    def _configure_tags(self) -> None:
        # 样式统一在 app/ui/style.py 里定义，主窗口和"设置 → 外观"的预览共用，
        # 保证所见即所得（逐项字体/字号/粗细/颜色/底色/边框）。
        config = self.config
        if str(self.config.get("transparency_mode", "off")).lower() == "key":
            # 聊天区底色也用颜色键 → 背景透明，只剩文字
            config = theme.key_config(self.config)
        theme.apply_chat_theme(self.text, config)

    def _bind_keys(self) -> None:
        self.root.bind("<F8>", lambda _e: self.toggle_monitor())
        self.root.bind("<F9>", lambda _e: self.clipboard_to_cn2en())
        self.root.bind("<F10>", lambda _e: self.fix_selected())
        self.root.bind("<F5>", lambda _e: self.test_region())
        self.root.bind("<Escape>", lambda _e: None)

    def _show_menu(self, event) -> None:
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    # ------------------------------------------------------------------ 轮询
    def _poll(self) -> None:
        processed = 0
        while processed < 60:
            try:
                event = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            processed += 1
            kind = event.get("type")
            if kind == "message":
                self._render(event["item"])
            elif kind == "status":
                self.set_status(event.get("text", ""), event.get("level", "info"))
            elif kind == "update":
                self._on_update_result(event.get("info"), bool(event.get("manual")),
                                       bool(event.get("skipped")))
            elif kind == "public_dict":
                self._on_public_dict(event)

        self._update_stats()
        self.memory.flush()
        if time.time() - self._last_flush > 10:
            self._last_flush = time.time()
            self.pipeline.flush_cache()
            self.memory.flush(force=True)
        try:
            self.root.after(90, self._poll)
        except tk.TclError:
            pass          # 窗口已经销毁，收工

    def _update_stats(self) -> None:
        status = self.pipeline.status()
        stats = status["stats"]
        running = bool(status["running"])
        try:
            engine = str(status.get("engine") or "")
            note = str(status.get("engine_note") or "")
            self.stats_tooltip.text = ("当前引擎：%s%s\n" % (
                engine, "（%s）" % note if note else "")) + STATS_TIP
        except Exception:                          # noqa: BLE001
            pass
        icons_only = bool(self.config.get("toolbar_icons_only", False))
        if icons_only:
            # 图标模式：只占 3 个字符宽，不再白占 13 个字符
            self.monitor_button.configure(
                text="■" if running else "▶", width=3,
                style="IconDanger.TButton" if running else "IconAccent.TButton")
        else:
            self.monitor_button.configure(
                text="■ 停止监听" if running else "▶ 开始监听", width=13,
                style="CompactDanger.TButton" if running else "CompactAccent.TButton")
        try:
            self.monitor_tooltip.text = (
                "停止监听（F8）\n当前正在持续截图识别" if running
                else "开始监听（F8）\n点一下开始持续截图识别游戏聊天框")
        except Exception:
            pass
        try:
            self.state_dot.configure(
                text="●" if running else "○",
                fg=theme.PALETTE["ok"] if running else theme.PALETTE["muted"])
        except Exception:
            pass
        self.stats_var.set(
            "待译 %d · 缓存 %d · 调用 %d · 记忆 %d · 过滤 %d · 跳过 %d · 错误 %d"
            % (status["pending"], status["cache_size"], stats.get("api_calls", 0),
               stats.get("memory_hits", 0), stats.get("filtered", 0),
               stats.get("skipped_frame", 0),
               stats.get("api_errors", 0))
        )
        # 收起时那条留白只有频道小灯（用户明确说不要"译/滤"计数；跑没跑看左边的
        # 监听按钮、计数看底部状态栏）。这里只负责让小灯"刚说话闪一下"。
        now = time.time()
        if hasattr(self, "channel_strip") and now - self._strip_refreshed > 0.25:
            self._strip_refreshed = now
            if self.channel_strip.winfo_manager():
                self._refresh_channel_strip()

    # ------------------------------------------------------------------ 渲染
    def _render(self, item: DisplayItem) -> None:
        self.text.configure(state="normal")
        self._note_channel_activity(item.channel)
        if self.config.get("show_timestamp", False):
            self.text.insert("end", time.strftime("%H:%M:%S "), "meta")

        translated = textutil.normalize(item.translated).replace("\n", " ")
        prefix = item.prefix or ("(%s): " % (item.channel or "聊天"))
        channel_tag = "channel_" + item.channel if item.channel else "meta"
        # 正文/系统消息可以"跟随频道颜色"（游戏里整行同色），也可以各自固定颜色
        body_tag = style.element_tag(self.config, "body", item.channel)
        system_tag = style.element_tag(self.config, "system", item.channel)
        original_tag = style.element_tag(self.config, "original", item.channel)

        if item.kind == "system":
            # 系统消息和游戏里一样：原样前缀 + 原文（本来就是中文，不翻译）
            self.text.insert("end", prefix, channel_tag)
            self.text.insert("end", translated + "\n", system_tag)
            record = {"seq": item.seq, "source": item.source,
                      "translated": translated, "kind": "system"}
        else:
            self.text.insert("end", prefix, channel_tag)
            if item.speaker:
                self.text.insert("end", "%s: " % item.speaker, "name")
            self.text.insert("end", translated + "\n", body_tag)
            if item.error:
                self.text.insert("end", "    ⚠ %s\n" % item.error, "warn")
                self.text.insert("end", "    %s\n" % item.source, original_tag)
            elif item.note and self.config.get("show_notes", False):
                self.text.insert("end", "    · %s\n" % item.note, "meta")
            if self.config.get("show_original", False):
                self.text.insert("end", "    %s\n" % item.source, original_tag)
            record = {"seq": item.seq, "source": item.source,
                      "translated": translated, "kind": "chat"}

        if item.kind != "system" and item.error:
            self.set_status(item.error, "error")
        self.records.append(record)
        self._trim()
        self.text.see("end")
        self.text.configure(state="disabled")

    def _trim(self) -> None:
        limit = int(self.config.get("max_lines", 600))
        lines = int(self.text.index("end-1c").split(".")[0])
        if lines <= limit:
            return
        excess = lines - limit
        self.text.delete("1.0", "%d.0" % (excess + 1))
        self.records = self.records[-max(50, limit // 4):]

    # ------------------------------------------------------------------ 操作
    def set_status(self, text: str, level: str = "info") -> None:
        prefix = {"ok": "✓ ", "error": "✗ ", "warn": "! "}.get(level, "")
        self.status_var.set(prefix + str(text))
        styles = getattr(self, "_status_styles", None) or self._status_style_map(False)
        try:
            self.status_label.configure(style=styles.get(level, styles["info"]))
        except Exception:
            pass

    def toggle_monitor(self) -> None:
        if self.pipeline.running:
            self.pipeline.stop()
            return
        if not self.config.get("region"):
            self.set_status("先点「区域」框选游戏聊天框", "warn")
            self.select_region()
            return
        self.pipeline.apply_config()
        self.pipeline.start()

    def select_region(self, after=None) -> None:
        self._region_callback = after
        self.root.withdraw()
        self.root.after(120, self._start_picker)

    def _start_picker(self) -> None:
        picker = RegionPicker(self.root, self._on_region_picked)
        picker.start()

    def _on_region_picked(self, region) -> None:
        self.root.deiconify()
        callback, self._region_callback = self._region_callback, None
        if region:
            self.config["region"] = [int(v) for v in region]
            config_module.save_config(self.config)
            self._publish_screen_size()
            self.set_status("已选择区域 %s" % (self.config["region"],), "ok")
            self.root.after(200, self.test_region)
        else:
            self.set_status("已取消框选", "warn")
        if callback:
            callback()

    def test_region(self) -> None:
        region = self.config.get("region")
        if not region:
            messagebox.showinfo("提示", "先点「区域」框选游戏里的聊天框")
            return
        self.set_status("正在抓取并识别（第一次要加载模型，稍等）…")
        self.root.update_idletasks()
        image, lines = self.pipeline.test_capture()
        if image is None:
            messagebox.showerror("截图失败", "没能截到画面，检查区域坐标是否在屏幕范围内")
            return
        self.set_status("识别到 %d 行" % len(lines), "ok")
        show_preview(self.root, region, image, lines, on_retry=self.select_region,
                     info=self.dpi_info())

    def toggle_original(self) -> None:
        self.config["show_original"] = not self.config.get("show_original", False)
        config_module.save_config(self.config)
        self.set_status("英文原文：%s" % ("显示" if self.config["show_original"] else "隐藏"))

    def copy_selection(self) -> None:
        try:
            selection = self.text.get("sel.first", "sel.last")
        except tk.TclError:
            selection = ""
        if not selection:
            self.set_status("没有选中内容", "warn")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(selection)
        self.set_status("已复制选中内容", "ok")

    def clear_display(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")
        self.records = []

    def _selection_text(self) -> str:
        try:
            return self.text.get("sel.first", "sel.last").strip()
        except tk.TclError:
            return ""

    def _match_record(self, selection: str):
        if not selection:
            return None
        for record in reversed(self.records):
            translated = record.get("translated") or ""
            if not translated:
                continue
            if translated in selection or selection in translated:
                return record
        return None

    def fix_selected(self) -> None:
        selection = self._selection_text()
        record = self._match_record(selection)
        if record is None:
            if not self.records:
                messagebox.showinfo("提示", "还没有可纠正的翻译")
                return
            record = self.records[-1]
        CorrectionDialog(self, record["source"], record["translated"],
                         on_saved=lambda after, result: self._apply_correction(record, after))

    def _apply_correction(self, record, after: str) -> None:
        old = record.get("translated") or ""
        if old and old != after:
            index = None
            try:
                # 注意别再传一次 stopindex：第三个位置参数就是 stopindex
                index = self.text.search(old, "1.0", "end")
            except tk.TclError:
                index = None
            if index:
                self.text.configure(state="normal")
                self.text.delete(index, "%s+%dc" % (index, len(old)))
                self.text.insert(index, after)
                self.text.configure(state="disabled")
        record["translated"] = after

    def clipboard_to_cn2en(self) -> None:
        dialog = CnToEnDialog(self)
        try:
            clipboard = self.root.clipboard_get()
        except tk.TclError:
            clipboard = ""
        if clipboard:
            dialog.input.delete("1.0", "end")
            dialog.input.insert("1.0", clipboard.strip())
            dialog.translate()

    def open_cn2en(self) -> None:
        CnToEnDialog(self)

    def open_settings(self) -> None:
        SettingsDialog(self)

    def open_bug_report(self) -> None:
        """「反馈问题」：自动收好日志/配置/翻译记录（API Key 自动隐藏）再打包。"""
        from .report import BugReportDialog

        return BugReportDialog(self)

    def open_learning(self) -> None:
        LearningCenterDialog(self)

    def open_contribution(self) -> None:
        """「参与改进」：把用户自己确认过的术语/纠错匿名贡献出去（默认关）。"""
        from .contribute import ContributionDialog

        return ContributionDialog(self)

    def open_dictionary(self) -> None:
        DictionaryDialog(self)

    # ------------------------------------------------------------------ 配置生效
    def apply_settings(self) -> None:
        config_module.save_config(self.config)
        theme.install(self.root, self.config)      # 主题（暗色/浅色）也跟着生效
        self._setup_scaling()          # 界面缩放在保存后立即生效
        self.pipeline.apply_config()
        self.rebuild_glossary()
        self._build_actions()
        self._apply_frameless()
        self._apply_toolbar_collapsed()
        self._apply_status_bar()
        self._apply_transparency()
        self._configure_tags()
        self._apply_visual_config()
        self._refresh_channel_strip()
        self.set_status("设置已生效", "ok")

    def _apply_visual_config(self) -> None:
        try:
            self.root.attributes("-topmost", bool(self.config.get("always_on_top", True)))
        except Exception:
            pass

    def rebuild_glossary(self) -> None:
        self.glossary = build_glossary(self.config, self.memory)
        self.pipeline.reload_glossary(self.glossary)

    # ------------------------------------------------------------------ 生命周期
    def _first_run_hint(self) -> None:
        notes = []
        if not self.config.get("region"):
            notes.append("① 点「区域」框选游戏里的聊天框")
        if self.config.get("engine") == "deepseek" and not self.config.get("deepseek_key"):
            notes.append("② 点「设置」填 DeepSeek API Key（或把引擎改成 mymemory 免费试用）")
        notes.append("③ 点「▶ 监听」开始实时翻译；翻译不对就选中它按 F10 纠正")
        self.set_status("  ".join(notes))

    # ------------------------------------------------------- 使用须知 / 免责声明
    def startup_gate(self) -> None:
        """第一次打开（或条款改版后）先让用户确认使用须知；不同意就退出程序。

        放在启动流程最前面：**同意之前不提示使用步骤、不查更新、不截图**。
        """
        if not disclaimer.needs_agreement(self.config):
            self._after_agreement()
            return
        self._agreement_dialog = AgreementDialog(self, on_result=self._on_agreement)

    def _on_agreement(self, accepted: bool) -> None:
        self._agreement_dialog = None
        if not accepted:
            self.set_status("你没有同意使用须知，程序退出", "warn")
            self.root.after(150, self.quit_app)
            return
        self._after_agreement()

    def _after_agreement(self) -> None:
        """同意之后才做的事：使用提示 + 后台查一次更新（一天最多一次）。"""
        self.root.after(300, self._first_run_hint)
        self.root.after(3800, self.maybe_check_update)
        # 公共词典：启动几秒后在后台拉一次（只下载、不上传任何东西）
        self.root.after(6000, self._public_dict_tick)

    # ------------------------------------------------------------ 公共词典
    PUBLIC_DICT_TICK = 30 * 60          # 每半小时看一次"该不该更新了"

    def _public_dict_tick(self) -> None:
        """定期检查公共词典（真正的间隔判断在 public_dict.needs_sync 里）。"""
        self.maybe_sync_public_dict()
        try:
            self.root.after(self.PUBLIC_DICT_TICK * 1000, self._public_dict_tick)
        except tk.TclError:
            pass          # 窗口销毁了，收工

    def maybe_sync_public_dict(self, manual: bool = False) -> None:
        """后台更新公共词典；失败就静默退回缓存/内置表，绝不挡界面。"""
        from .. import public_dict as public_dict_module

        if not self.config.get("public_dict_enabled", True):
            if manual:
                self.set_status("公共词典已在设置里关掉", "warn")
            return
        if not manual and not public_dict_module.needs_sync(self.config):
            return
        if getattr(self, "_public_dict_busy", False):
            return
        self._public_dict_busy = True
        if manual:
            self.set_status("正在更新公共词典…", "info")

        def work() -> None:
            try:
                result = public_dict_module.sync(self.config, force=manual)
            except Exception as exc:                 # noqa: BLE001
                result = {"ok": False, "updated": False, "terms": 0,
                          "reason": "更新出错：%s" % exc}
            self.ui_queue.put({"type": "public_dict", "result": result,
                               "manual": manual})

        threading.Thread(target=work, name="public-dict", daemon=True).start()

    def _on_public_dict(self, event) -> None:
        self._public_dict_busy = False
        result = event.get("result") or {}
        manual = bool(event.get("manual"))
        if result.get("updated"):
            self.rebuild_glossary()
            names = [row.get("name") for row in (result.get("sources") or [])
                     if row.get("updated")]
            detail = ("、".join(names[:3]) if names
                      else "版本 %s" % (result.get("version") or "?"))
            self.set_status("词典已更新：%s（合计 %d 条）"
                            % (detail, result.get("terms", 0)), "ok")
        elif result.get("ok"):
            if manual:
                self.set_status("词典源已经是最新的（合计 %d 条）"
                                % result.get("terms", 0), "ok")
        elif manual:
            self.set_status("词典源更新失败：%s" % (result.get("reason") or "未知原因"),
                            "warn")

    def open_agreement(self) -> None:
        """从「设置 → 关于」回看使用须知（只看，不改同意状态）。"""
        return AgreementDialog(self, readonly=True)

    def quit_app(self) -> None:
        try:
            self.config["window_pos"] = [self.root.winfo_x(), self.root.winfo_y()]
            self.config["window_size"] = [self.root.winfo_width(),
                                          self.root.winfo_height()]
            config_module.save_config(self.config)
        except Exception:
            pass
        try:
            self.pipeline.shutdown()
        except Exception:
            pass
        try:
            self.memory.flush(force=True)
        except Exception:
            pass
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()

    # ------------------------------------------------------------------ 更新检查
    UPDATE_CHECK_INTERVAL = 24 * 3600      # 自动检查的间隔（秒）：一天一次够了

    def check_update(self, manual: bool = False) -> None:
        """查有没有新版本（后台线程，不挡界面）；有新版就弹更新窗口。"""
        if getattr(self, "_update_busy", False):
            return
        self._update_busy = True
        if manual:
            self.set_status("正在检查更新…", "info")
        skipped = str(self.config.get("update_skipped") or "")

        def work() -> None:
            from .. import update as update_module

            try:
                info = update_module.check()
            except Exception:
                info = None
            try:
                self.config["update_checked_at"] = time.time()
                config_module.save_config(self.config)
            except Exception:
                pass
            if info is None or info.version == skipped:
                self.ui_queue.put({"type": "update", "info": None,
                                   "manual": manual,
                                   "skipped": bool(info and info.version == skipped)})
            else:
                self.ui_queue.put({"type": "update", "info": info, "manual": manual})

        threading.Thread(target=work, name="update-check", daemon=True).start()

    def maybe_check_update(self) -> None:
        """启动后按间隔自动查一次（设置里可以关）。"""
        if not self.config.get("check_update", True):
            return
        try:
            last = float(self.config.get("update_checked_at") or 0)
        except Exception:
            last = 0.0
        if time.time() - last < self.UPDATE_CHECK_INTERVAL:
            return
        self.check_update()

    def _on_update_result(self, info, manual: bool, skipped: bool = False) -> None:
        self._update_busy = False
        if info is None:
            if manual:
                if skipped:
                    self.set_status("这个版本（v%s）被你跳过了，可在「关于」里点"
                                    "「立即检查更新」重新查看"
                                    % self.config.get("update_skipped"), "info")
                else:
                    self.set_status("已经是最新版 v%s" % __version__, "ok")
            return
        self.set_status("发现新版本 v%s" % info.version, "ok")
        from .update_dialog import UpdateDialog

        UpdateDialog(self, info)

