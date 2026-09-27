"""主窗口。

界面只做三件事：把翻译结果显示好、把用户的操作转成命令、把学习入口摆出来。
所有后台动作都在 Pipeline 里，主线程只通过队列收结果——不会出现
后台线程直接写 Tk 控件那种随机崩溃。
"""
from __future__ import annotations

import queue
import time
import tkinter as tk
from tkinter import messagebox, ttk

from .. import config as config_module
from .. import paths, textutil
from ..glossary import build_glossary
from ..pipeline import DisplayItem, Pipeline
from ..store import MemoryStore
from .cn2en import CnToEnDialog
from .learn import CorrectionDialog, DictionaryDialog, LearningCenterDialog
from .region import RegionPicker, show_preview
from .settings import SettingsDialog
from . import style
from . import theme
from .frameless import FramelessWindow

from .. import AUTHOR, HOMEPAGE, __version__          # noqa: E402

APP_TITLE = "DDO 翻译助手 v%s" % __version__

# 工具栏按钮：(图标, 文字, 方法名, 悬停说明)
ACTION_BUTTONS = (
    ("⊞", "区域", "select_region", "框选游戏聊天框；框完会自动抓一帧给你确认"),
    ("◎", "测试", "test_region", "抓一帧看看识别到什么（F5）"),
    ("⇄", "中译英", "open_cn2en", "把中文翻成老外习惯的英文；F9 直接翻译剪贴板"),
    ("✎", "纠错", "fix_selected", "选中一条译文后改成正确的中文（F10）"),
    ("▤", "词典", "open_dictionary", "术语表：这里面的词会被保护，不让模型乱翻"),
    ("✦", "学习", "open_learning", "待学习词 / 已学习 / 纠错历史"),
    ("⚙", "设置", "open_settings", "引擎、区域、外观、学习阈值"),
)


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
        self.root.after(400, self._first_run_hint)

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

        actions = ttk.Frame(top, style="Surface.TFrame")
        actions.pack(side="left")
        self.actions = actions
        self._build_actions()

        window_buttons = ttk.Frame(top, style="Surface.TFrame")
        window_buttons.pack(side="right", padx=(2, 6))
        self.quit_button = ttk.Button(window_buttons, text="✕", width=2,
                                      style="Icon.TButton", command=self.quit_app)
        self.quit_button.pack(side="right")
        theme.Tooltip(self.quit_button, "退出程序")
        self.min_button = ttk.Button(window_buttons, text="—", width=2,
                                     style="Icon.TButton", command=self._minimize)
        theme.Tooltip(self.min_button, "最小化（无边框模式下会先恢复系统边框，方便从任务栏找回）")
        self.window_buttons = window_buttons

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
        theme.Tooltip(self.stats_label,
                      "待译=排队中　缓存=命中本地缓存　调用=已请求接口次数\n"
                      "记忆=命中你纠正过的句子　跳过=画面没变省掉的 OCR 次数\n"
                      "错误=接口失败次数")

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
        self.menu.add_command(label="中译英 (Ctrl+Enter 发送)", command=self.open_cn2en)
        self.menu.add_command(label="显示/隐藏英文原文", command=self.toggle_original)
        self.menu.add_separator()
        self.menu.add_command(label="词典", command=self.open_dictionary)
        self.menu.add_command(label="学习中心", command=self.open_learning)
        self.menu.add_command(label="设置", command=self.open_settings)
        self.menu.add_separator()
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
                self.min_button.pack(side="right", before=self.quit_button)
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
        self._apply_toolbar_collapsed()
        self.set_status("工具按钮已%s" % ("收起" if self.config["toolbar_collapsed"] else "展开"),
                        "info")

    def _apply_toolbar_collapsed(self) -> None:
        collapsed = bool(self.config.get("toolbar_collapsed", True))
        try:
            if collapsed:
                self.actions.pack_forget()
                self.separator.pack_forget()
            else:
                # 顺序很重要：先把 actions 交给 pack 管理，再用 before= 插分隔线。
                # 反过来写会报 "window isn't packed"，整条工具条就永远显示不出来。
                self.actions.pack(side="left")
                try:
                    self.separator.pack(side="left", fill="y", padx=4, pady=6,
                                        before=self.actions)
                except Exception:
                    self.separator.pack(side="left", fill="y", padx=4, pady=6)
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
        except Exception:
            pass

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

    # ------------------------------------------------------------------ 渲染
    def _render(self, item: DisplayItem) -> None:
        self.text.configure(state="normal")
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

    def open_learning(self) -> None:
        LearningCenterDialog(self)

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
