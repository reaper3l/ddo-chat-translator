"""设置窗口：翻译 / 监控 / 显示与学习。"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import colorchooser, messagebox, ttk

from .appearance_tab import AppearanceTab
from .. import channels as channels_module
from . import theme
from .widgets import ScrollableFrame

class SettingsDialog:
    # 设置中心里的分类：(标题, 一句话说明)
    CATEGORIES = (
        ("翻译", "引擎、API Key、翻译模式、上下文"),
        ("监控", "聊天区域、截图间隔、性能"),
        ("频道", "频道名、颜色、显示开关（可自定义增删）"),
        ("显示与学习", "窗口形态、显示项、学习阈值"),
        ("外观", "字体、字号、颜色、底色、边框"),
        ("关于", "版本、数据位置、快捷键"),
    )

    def __init__(self, app) -> None:
        self.app = app
        self.config = app.config
        self.queue: "queue.Queue[str]" = queue.Queue()
        self.vars = {}
        self.channel_rows = []           # 「频道」页里的行（可增删）
        self.pages = {}                  # 分类名 -> 该分类的窗口
        self.appearance = None
        self.status = tk.StringVar(value="")

        self.window = tk.Toplevel(app.root)
        self.window.title("设置")
        theme.prepare_window(self.window, self.config)
        self.window.geometry("400x330")
        self.window.minsize(360, 260)
        self.window.transient(app.root)
        theme.frameless_dialog(self.window, "设置")
        if self.config.get("always_on_top", True):
            try:
                self.window.attributes("-topmost", True)
            except Exception:
                pass
        self._build()
        self._poll()

    # ------------------------------------------------------------ 界面构建
    def _build(self) -> None:
        """设置中心：一个小窗口列出分类，点一项单独开窗编辑。"""
        theme.install(self.window, self.config)
        ttk.Label(self.window, text="每一项都在自己的窗口里编辑，内容多也不会被挤掉",
                  style="Muted.TLabel").pack(anchor="w", padx=14, pady=(10, 8))

        for title, desc in self.CATEGORIES:
            row = ttk.Frame(self.window)
            row.pack(fill="x", padx=12, pady=2)
            ttk.Button(row, text=title, width=11,
                       command=lambda t=title: self.open_category(t)).pack(side="left")
            ttk.Label(row, text=desc, style="Muted.TLabel").pack(side="left", padx=8)

        bottom = ttk.Frame(self.window)
        bottom.pack(side="bottom", fill="x", padx=12, pady=(6, 10))
        ttk.Label(bottom, textvariable=self.status, style="Status.TLabel").pack(side="left")
        ttk.Button(bottom, text="关闭", command=self.window.destroy).pack(side="right")

    # ------------------------------------------------------- 分类页（独立窗口）
    def open_category(self, title: str) -> tk.Toplevel:
        """打开某个分类的独立设置窗口（已打开就抬到前面）。"""
        existing = self.pages.get(title)
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.deiconify()
                    existing.lift()
                    existing.focus_force()
                    return existing
            except Exception:
                pass
        window = self._create_page(title)
        self.pages[title] = window
        return window

    def _create_page(self, title: str) -> tk.Toplevel:
        window = tk.Toplevel(self.window)
        window.title("设置 · %s" % title)
        theme.prepare_window(window, self.config)
        theme.frameless_dialog(window, "设置 · %s" % title)
        try:
            height = min(620, max(420, window.winfo_screenheight() - 220))
        except Exception:
            height = 540
        window.geometry("560x%d" % height)
        window.minsize(460, 340)
        if self.config.get("always_on_top", True):
            try:
                window.attributes("-topmost", True)
            except Exception:
                pass

        # 底部按钮先占位（side="bottom"），内容再高也挤不掉
        row = ttk.Frame(window)
        row.pack(side="bottom", fill="x", padx=12, pady=(0, 10))
        self.status = tk.StringVar(value="")
        ttk.Label(row, textvariable=self.status, style="Status.TLabel").pack(side="left")
        if title == "关于":
            ttk.Button(row, text="关闭", command=window.destroy).pack(side="right")
        else:
            save_button = ttk.Button(row, text="保存并关闭", style="Accent.TButton",
                                     command=lambda w=window: self.save(w))
            save_button.pack(side="right")
            ttk.Button(row, text="应用", command=self.save).pack(side="right", padx=(0, 6))
            if title == "翻译":
                self.test_button = ttk.Button(row, text="测试连接",
                                              command=self.test_connection)
                self.test_button.pack(side="right", padx=(0, 6))

        scrollable = ScrollableFrame(window)
        scrollable.pack(fill="both", expand=True, padx=10, pady=(10, 4))

        previous = getattr(self, "_page_container", None)
        self._page_container = scrollable.inner
        try:
            if title == "翻译":
                self._build_translate_tab(None)
            elif title == "监控":
                self._build_monitor_tab(None)
            elif title == "频道":
                self._build_channel_tab(None)
            elif title == "显示与学习":
                self._build_display_tab(None)
            elif title == "外观":
                self.appearance = AppearanceTab(scrollable.inner, self.config,
                                                dialog=self)
            elif title == "关于":
                self._build_about_tab(scrollable.inner)
        finally:
            self._page_container = previous
        return window

    def _tab(self, _parent, title: str) -> ttk.Frame:
        """返回当前分类页的内容容器（已经在可滚动区域里）。"""
        container = getattr(self, "_page_container", None)
        if container is None:                     # 兜底：单独调用时自建一个
            container = ScrollableFrame(self.window).inner
        return container

    def _build_about_tab(self, parent: ttk.Frame) -> None:
        """关于页：版本、引擎、数据位置、快捷键，并可直接打开对应文件。"""
        from .. import paths
        from .. import AUTHOR, HOMEPAGE, __version__

        info = ttk.LabelFrame(parent, text="关于")
        info.pack(fill="x", padx=10, pady=(8, 4))
        for line in (
            "DDO 翻译助手 v%s" % __version__,
            "作者：%s　项目主页：%s" % (AUTHOR, HOMEPAGE),
            "翻译引擎：%s" % self.app.pipeline.engine.describe(),
            "",
            "数据目录：%s" % paths.DATA_DIR,
            "　配置 config.json　学习库 memory.json　缓存 cache.json　日志 logs\\app.log",
            "",
            "快捷键：F8 开始/停止　F9 剪贴板中译英　F10 纠错　F5 测试识别",
            "术语表：assets\\glossary.json（可直接编辑，也可在「词典」里改）",
        ):
            ttk.Label(info, text=line, style="SurfaceMuted.TLabel").pack(
                anchor="w", padx=8, pady=1)

        row = ttk.Frame(parent)
        row.pack(fill="x", padx=10, pady=6)
        for text, path in (("打开数据目录", paths.DATA_DIR),
                           ("打开日志", paths.LOG_PATH),
                           ("打开术语表", paths.GLOSSARY_PATH)):
            ttk.Button(row, text=text,
                       command=lambda p=path: self._open_path(p)).pack(side="left", padx=(0, 6))

    @staticmethod
    def _open_path(path) -> None:
        """用系统默认程序打开文件或文件夹。"""
        import os
        import subprocess
        import sys

        try:
            if sys.platform == "win32":
                os.startfile(str(path))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            messagebox.showwarning("提示", "打不开：%s\n%s" % (path, exc))

    def _check(self, parent, key: str, text: str, default: bool = True):
        var = tk.BooleanVar(value=bool(self.config.get(key, default)))
        self.vars[key] = ("bool", var, None)
        ttk.Checkbutton(parent, text=text, variable=var).pack(anchor="w", pady=2)
        return var

    def _spin(self, parent, key: str, text: str, start: int, end: int,
              width: int = 8):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text=text, width=30).pack(side="left")
        var = tk.StringVar(value=str(self.config.get(key, start)))
        ttk.Spinbox(row, from_=start, to=end, textvariable=var,
                    width=width).pack(side="left")
        self.vars[key] = ("int", var, None)
        return var

    def _entry(self, parent, key: str, text: str, show: str = ""):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text=text, width=30).pack(side="left")
        var = tk.StringVar(value=str(self.config.get(key, "")))
        ttk.Entry(row, textvariable=var, width=32, show=show).pack(side="left")
        self.vars[key] = ("str", var, None)
        return var

    def _choice(self, parent, key: str, text: str, values):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text=text, width=30).pack(side="left")
        var = tk.StringVar(value=str(self.config.get(key, values[0])))
        ttk.Combobox(row, state="readonly", width=29, textvariable=var,
                     values=list(values)).pack(side="left")
        self.vars[key] = ("str", var, None)
        return var

    def _choice_labeled(self, parent, key: str, text: str, options):
        """带中文标签的下拉框（options 是 [(值, 标签), ...]）。"""
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text=text, width=30).pack(side="left")
        labels = [label for _value, label in options]
        current = str(self.config.get(key, options[0][0]))
        label = next((lb for value, lb in options if value == current), labels[0])
        var = tk.StringVar(value=label)
        ttk.Combobox(row, state="readonly", width=26, textvariable=var,
                     values=labels).pack(side="left")
        self.vars[key] = ("choice", var, options)
        return var

    def _build_translate_tab(self, notebook) -> None:
        tab = self._tab(notebook, "翻译")
        self._choice(tab, "engine", "翻译引擎",
                     ["deepseek", "mymemory", "offline"])
        ttk.Label(tab, style="Muted.TLabel", wraplength=520, justify="left",
                  text="deepseek=质量最好（要填 Key）；mymemory=免费在线、不用 Key；"
                       "offline=不联网，只把术语表里的词翻出来").pack(anchor="w", pady=(0, 6))
        self._entry(tab, "deepseek_key", "DeepSeek API Key", show="*")
        self._entry(tab, "deepseek_model", "模型名（默认 deepseek-chat）")
        self._choice(tab, "translate_mode", "翻译模式", ["quality", "fast"])
        ttk.Label(tab, style="Muted.TLabel", wraplength=520, justify="left",
                  text="quality=完整术语提示词 + 上下文（推荐）；fast=短提示词，更快更省"
                  ).pack(anchor="w", pady=(0, 6))
        self._spin(tab, "context_turns", "上下文条数（0=不带上下文）", 0, 10)
        self._spin(tab, "timeout_seconds", "单次请求超时（秒）", 5, 60)

    def _build_monitor_tab(self, notebook) -> None:
        tab = self._tab(notebook, "监控")
        row = ttk.Frame(tab)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text="聊天区域", width=30).pack(side="left")
        ttk.Button(row, text="重新框选", command=self._pick_region).pack(side="left")
        ttk.Button(row, text="测试识别", command=self.app.test_region).pack(
            side="left", padx=6)
        self.region_label = theme.label(tab, self._region_text(), muted=True)
        self.region_label.pack(anchor="w")

        self._spin(tab, "interval_ms", "截图间隔（毫秒）", 300, 5000)
        self._check(tab, "skip_identical_frame",
                    "画面没变化时跳过 OCR（省 CPU，强烈建议开）")
        self._check(tab, "band_ocr", "只识别变化的那几行（更省 CPU，推荐开）")
        self._check(tab, "low_priority", "程序用低于游戏的 CPU 优先级（推荐开，游戏更流畅）")
        self._check(tab, "idle_backoff", "长时间没新消息时自动放慢截图频率")
        self._choice_labeled(tab, "ocr_threads", "OCR 线程数（越小越不卡游戏）",
                             [("1", "1（最省 CPU）"), ("2", "2（推荐）"),
                              ("3", "3"), ("4", "4（最快，游戏可能卡）")])
        self._choice_labeled(tab, "ocr_upscale", "OCR 放大倍数（越大越准但越吃 CPU）",
                             [("auto", "自动（推荐）"), ("1", "1（最快）"),
                              ("1.25", "1.25"), ("1.5", "1.5"), ("2", "2（最准最费 CPU）")])
        self._check(tab, "merge_same_row", "同一行被切开时自动拼接")
        self._spin(tab, "dedup_ttl_seconds", "同一句多久内不重复翻译（秒）", 0, 600)
        self._check(tab, "show_system", "把系统消息也显示到主界面")
        self._check(tab, "system_whitelist",
                    "系统消息只显示有用的（组队/生死/队长提示；战利品、宝箱、任务面板不显示）",
                    True)
        theme.label(tab, "关掉上面这项会把战利品/宝箱面板也显示出来（OCR 错字版本会刷屏）。",
                    muted=True, anchor="w", wraplength=520, justify="left").pack(
            fill="x", pady=(0, 4))
        self._choice_labeled(tab, "capture_backend", "截图方式",
                             [("auto", "自动（优先只抓区域，更快；不一致自动回退）"),
                              ("pillow", "始终用系统截图（最稳，稍慢）")])

        # 频道开关不在这里再放一份了：和「频道」页的勾选框是同一个东西，
        # 两份一起写会互相覆盖（"改了保存没生效"就是这么来的）。
        theme.label(tab, "频道（名字 / 颜色 / 翻不翻 / 小灯）都在「频道」页里改：",
                    muted=True, anchor="w").pack(fill="x", pady=(8, 2))
        ttk.Button(tab, text="打开「频道」设置",
                   command=lambda: self.open_category("频道")).pack(anchor="w")

    def _build_channel_tab(self, _parent) -> None:
        """频道页：增删频道、改名、改颜色、开关显示。

        为什么要单独一页：游戏更新时频道名可能变、也可能多出新频道
        （而且游戏里其实并没有"队伍"这个频道）。列表存进配置，随时能自己调。
        """
        from .. import channels as channels_module

        tab = self._tab(None, "频道")
        theme.label(tab, "游戏里的频道（可自行增删；改了以后翻译窗口的颜色/前缀就按这里来）",
                    muted=True, anchor="w", wraplength=520, justify="left").pack(
            fill="x", pady=(0, 6))

        # 表头：两列勾选框分别管"翻不翻"和"小灯里显不显示"，先说清楚
        header = ttk.Frame(tab)
        header.pack(fill="x", pady=(0, 2))
        ttk.Label(header, text="显示", style="Muted.TLabel", width=4).pack(side="left")
        ttk.Label(header, text="小灯", style="Muted.TLabel", width=4).pack(
            side="left", padx=(6, 0))
        ttk.Label(header, text="频道名", style="Muted.TLabel", width=13).pack(
            side="left", padx=(6, 0))
        ttk.Label(header, text="颜色", style="Muted.TLabel").pack(side="left", padx=(6, 0))

        self.channel_rows_frame = ttk.Frame(tab)
        self.channel_rows_frame.pack(fill="x")
        self.channel_rows = []
        for entry in channels_module.effective(self.config):
            self._add_channel_row(entry["name"], entry["color"], entry["enabled"],
                                  bool(entry.get("strip", True)))

        row = ttk.Frame(tab)
        row.pack(fill="x", pady=(6, 0))
        ttk.Button(row, text="＋ 添加频道", command=self._add_channel_row).pack(side="left")
        ttk.Button(row, text="恢复默认频道", command=self._reset_channels).pack(
            side="left", padx=6)

        theme.label(
            tab,
            "提示：名字要和游戏里显示的一致（例如「小队」）；颜色决定翻译窗口里这个频道的"
            "前缀/正文颜色，也决定工具条收起时那盏小灯的颜色。\n"
            "「显示」关掉=这个频道不显示也不翻译（省接口调用）；"
            "「小灯」= 工具条收起时那一排小灯里要不要有它（只管显示、不影响翻译）。\n"
            "改完点「保存并关闭」。",
            muted=True, anchor="w", wraplength=520, justify="left").pack(
            fill="x", pady=(8, 0))

    def _add_channel_row(self, name: str = "", color: str = "#7ee787",
                         enabled: bool = True, strip: bool = True) -> None:
        """在频道页加一行： [显示] [小灯] 名字 [颜色][选色] [删除]"""
        row = ttk.Frame(self.channel_rows_frame)
        row.pack(fill="x", pady=2)
        enabled_var = tk.BooleanVar(value=bool(enabled))
        strip_var = tk.BooleanVar(value=bool(strip))
        name_var = tk.StringVar(value=name)
        color_var = tk.StringVar(value=color or "#7ee787")
        ttk.Checkbutton(row, variable=enabled_var).pack(side="left")
        ttk.Checkbutton(row, variable=strip_var).pack(side="left", padx=(6, 0))
        ttk.Entry(row, textvariable=name_var, width=12).pack(side="left", padx=(6, 6))
        entry = ttk.Entry(row, textvariable=color_var, width=10)
        entry.pack(side="left")
        swatch = tk.Label(row, text="　", bg=color_var.get(),
                          relief="flat", width=2, bd=1)
        swatch.pack(side="left", padx=4)
        ttk.Button(row, text="选色", width=5,
                   command=lambda v=color_var, s=swatch: self._pick_color(v, s)
                   ).pack(side="left")
        record = {"frame": row, "enabled": enabled_var, "strip": strip_var,
                  "name": name_var,
                  "color": color_var, "swatch": swatch}
        ttk.Button(row, text="删除", width=5,
                   command=lambda r=record: self._remove_channel_row(r)).pack(
            side="left", padx=(6, 0))
        color_var.trace_add("write",
                            lambda *_a, r=record: self._sync_swatch(r))
        self.channel_rows.append(record)

    @staticmethod
    def _sync_swatch(record) -> None:
        try:
            record["swatch"].configure(bg=record["color"].get() or "#7ee787")
        except Exception:
            pass

    @staticmethod
    def _pick_color(var: tk.StringVar, swatch=None) -> None:
        chosen = colorchooser.askcolor(color=var.get() or "#7ee787")
        if chosen and chosen[1]:
            var.set(chosen[1])
            if swatch is not None:
                try:
                    swatch.configure(bg=chosen[1])
                except Exception:
                    pass

    def _remove_channel_row(self, record) -> None:
        try:
            record["frame"].destroy()
        except Exception:
            pass
        self.channel_rows = [row for row in self.channel_rows if row is not record]

    def _reset_channels(self) -> None:
        from .. import channels as channels_module

        for record in list(self.channel_rows):
            self._remove_channel_row(record)
        for entry in channels_module.DEFAULT_CHANNELS:
            self._add_channel_row(entry["name"], entry["color"], entry["enabled"])

    def _build_display_tab(self, notebook) -> None:
        tab = self._tab(notebook, "显示与学习")
        self._check(tab, "use_glossary", "启用术语表（术语先换成占位符再翻）")
        self._check(tab, "use_extra_glossary", "启用扩展术语表（旧版的 1900+ 词）")
        self._check(tab, "show_original", "译文下面显示英文原文", False)
        self._check(tab, "show_timestamp", "显示时间戳", False)
        self._check(tab, "show_notes", "译文后面标注来源（记忆命中/缓存/词典直译）", False)
        self._check(tab, "always_on_top", "窗口总在最前面")
        self._check(tab, "frameless",
                    "无边框窗口（自绘标题栏：拖工具条移动、边缘和右下角缩放；"
                    "不会出现在任务栏/Alt+Tab）", False)
        self._check(tab, "toolbar_icons_only", "工具栏只显示图标（更紧凑，悬停有说明）", False)
        self._check(tab, "toolbar_collapsed", "工具条折叠（只留标题，点标题展开/收起）")
        self._check(tab, "ui_animation", "工具条收起/展开有过渡动画（嫌晃可以关掉）")
        self._check(tab, "show_status_bar", "显示底部状态栏（待译/调用等计数）")
        self._choice_labeled(tab, "transparency_mode", "背景透明",
                             [("off", "不透明"),
                              ("alpha", "整窗半透明（文字也一起变淡）"),
                              ("key", "只透明背景（文字和按钮保持清晰）")])
        self._choice(tab, "alpha", "整窗透明度（alpha 模式）",
                     ["1.0", "0.9", "0.85", "0.8", "0.7", "0.6", "0.5"])
        self._spin(tab, "learn_min_count", "同一句改几次后开始影响模型", 1, 10)
        self._spin(tab, "candidate_min_count", "陌生词出现几次进入待学习列表", 2, 20)
        self._spin(tab, "max_lines", "主界面最多保留多少行", 100, 5000, width=10)

    # ------------------------------------------------------------ 行为
    def _region_text(self) -> str:
        region = self.config.get("region")
        return "当前区域：%s" % (region,) if region else "当前区域：还没框选"

    def _pick_region(self) -> None:
        self.window.withdraw()
        self.app.select_region(after=self._after_region)

    def _after_region(self) -> None:
        try:
            self.window.deiconify()
        except Exception:
            pass
        self.region_label.config(text=self._region_text())

    def save(self, close_window: tk.Misc = None) -> bool:
        """把已打开过页面里的设置写回配置并立即生效。

        两个教训（用户反馈"改了设置点保存并关闭没生效"）：

        1. **以前只要有一个输入框填得不合法，就整单 return False** —— 其它设置
           全都没保存，而且提示写的是"最后打开的那个页面"的状态栏，你多半看不到。
           现在改成：坏值挑出来用弹窗列清楚，**其它设置照常保存生效**。
        2. **频道开关以前有两份**（监控页的勾选框 + 频道页的行），两边都往
           `channels_enabled` 写，后写的把先写的覆盖掉 → 看着就是"改完保存没生效"。
           现在只有「频道」页一份。
        """
        bad = []
        for key, (kind, var, extra) in list(self.vars.items()):
            try:
                if kind == "bool":
                    self.config[key] = bool(var.get())
                elif kind == "int":
                    self.config[key] = int(str(var.get()).strip())
                elif kind == "choice":
                    label = str(var.get())
                    self.config[key] = next(
                        (value for value, text in (extra or []) if text == label),
                        (extra or [(None, None)])[0][0])
                else:
                    self.config[key] = str(var.get()).strip()
            except Exception:
                bad.append(key)            # 这一项跳过，其它照常保存
        if self.channel_rows:              # 只有打开过「频道」页才有这些行
            items = []
            for record in self.channel_rows:
                name = str(record["name"].get()).strip()
                if not name:
                    continue
                items.append({"name": name,
                              "color": str(record["color"].get()).strip() or "#7ee787",
                              "enabled": bool(record["enabled"].get()),
                              "strip": bool(record["strip"].get())})
            if items:
                channels_module.sync(self.config, items)
        if self.appearance is not None:
            self.appearance.save()
        # 「外观」页改的东西和频道表最后并一次（频道表是颜色的唯一来源）
        channels_module.apply_legacy(self.config)
        self.app.apply_settings()
        if bad:
            names = "、".join(bad)
            self.status.set("「%s」填的值不对，这一项没保存" % names)
            # 弹窗才看得见（状态栏在另一个页面上，用户常常看不到）
            try:
                messagebox.showwarning(
                    "有项目没保存",
                    "这些项填的值不对，已经跳过：\n%s\n\n其它设置已经保存并生效。\n"
                    "改好这几项再点一次「保存并关闭」即可。" % names,
                    parent=close_window)
            except Exception:
                pass
        else:
            self.status.set("已保存并生效")
        if close_window is not None:
            try:
                close_window.destroy()
            except Exception:
                pass
        return not bad

    def test_connection(self) -> None:
        if not self.save():
            return
        self.status.set("测试中…")

        def work() -> None:
            engine = self.app.pipeline.engine
            if not engine.available():
                self.queue.put("当前引擎不可用：DeepSeek 需要填 API Key")
                return
            try:
                result = engine.translate("hello world", None, timeout=15)
                outcome = ("成功：%s" % result.text) if result.ok else ("失败：%s" % result.error)
                note = self.app.pipeline.engine_note
                self.queue.put("%s%s" % ("（%s）" % note if note else "", outcome))
            except Exception as exc:
                self.queue.put("出错：%s" % exc)

        threading.Thread(target=work, daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                self.status.set(str(self.queue.get_nowait()))
        except queue.Empty:
            pass
        try:
            self.window.after(150, self._poll)
        except tk.TclError:
            pass
