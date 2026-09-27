"""设置窗口：翻译 / 监控 / 显示与学习。"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from .appearance_tab import AppearanceTab
from . import theme
from .widgets import ScrollableFrame

CHANNELS = ["小队", "队伍", "公会", "常规", "公共", "悄悄话", "战利品"]


class SettingsDialog:
    # 设置中心里的分类：(标题, 一句话说明)
    CATEGORIES = (
        ("翻译", "引擎、API Key、翻译模式、上下文"),
        ("监控", "聊天区域、截图间隔、频道开关"),
        ("显示与学习", "窗口形态、显示项、学习阈值"),
        ("外观", "字体、字号、颜色、底色、边框"),
        ("关于", "版本、数据位置、快捷键"),
    )

    def __init__(self, app) -> None:
        self.app = app
        self.config = app.config
        self.queue: "queue.Queue[str]" = queue.Queue()
        self.vars = {}
        self.channel_vars = {}
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
            elif title == "显示与学习":
                self._build_display_tab(None)
            elif title == "外观":
                self.appearance = AppearanceTab(scrollable.inner, self.config)
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
        self._check(tab, "skip_identical_frame", "画面没变化时跳过 OCR（省 CPU）")
        self._check(tab, "merge_same_row", "同一行被切开时自动拼接")
        self._spin(tab, "dedup_ttl_seconds", "同一句多久内不重复翻译（秒）", 0, 600)
        self._check(tab, "show_system", "把系统消息也显示到主界面", False)

        theme.label(tab, "要翻译的频道：", muted=True, anchor="w").pack(
            fill="x", pady=(8, 0))
        channels = self.config.get("channels_enabled", {}) or {}
        for channel in CHANNELS:
            var = tk.BooleanVar(value=bool(channels.get(channel, True)))
            self.channel_vars[channel] = var
            ttk.Checkbutton(tab, text=channel, variable=var).pack(anchor="w")

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
        """把已打开过页面里的设置写回配置并立即生效。"""
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
                self.status.set("「%s」填的值不对" % key)
                return False
        if self.channel_vars:              # 只有打开过「监控」页才有这些勾选
            self.config["channels_enabled"] = {
                channel: bool(var.get()) for channel, var in self.channel_vars.items()
            }
        if self.appearance is not None:
            self.appearance.save()
        self.app.apply_settings()
        self.status.set("已保存并生效")
        if close_window is not None:
            try:
                close_window.destroy()
            except Exception:
                pass
        return True

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
