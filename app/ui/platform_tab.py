"""设置 → 「平台 / 插件」页。

两件事：
1. 本地翻译平台的开关（端口、每日配额、单次字符上限）—— 打开后插件/脚本才能调；
2. 插件列表：启用/停用、权限（scopes）、令牌、用量、最近错误。

插件**默认是停用的**，而且要用户点了「启用」才发令牌 —— 装了个插件不等于信任它。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import theme
from .widgets import ButtonFlow

# 权限项的中文说明放在 app/platform/auth.py 里，这里只负责画界面


class PlatformTab:
    def __init__(self, parent, config: dict, dialog) -> None:
        self.parent = parent
        self.config = config
        self.dialog = dialog                     # SettingsDialog（借它的 _check/_spin）
        self.app = dialog.app
        self.rows = []
        self.status = tk.StringVar(value="")
        self._build()

    # ------------------------------------------------------------ 平台
    @property
    def platform(self):
        return getattr(self.app, "platform", None)

    def _build(self) -> None:
        parent = self.parent
        platform = self.platform

        # ---------------- 本地服务 ----------------
        box = ttk.LabelFrame(parent, text="本地翻译平台")
        box.pack(fill="x", padx=10, pady=(8, 4))
        theme.label(box,
                    "打开后，本机的插件 / 脚本可以通过 http://127.0.0.1:端口 调用翻译。"
                    "只监听 127.0.0.1，别的机器连不上；关掉就彻底不通。",
                    muted=True, anchor="w", wraplength=520, justify="left").pack(
            fill="x", padx=8, pady=(4, 2))

        self.dialog._check(box, "platform_enabled",
                           "启用本地翻译平台（插件 / 脚本才能调用）")
        self.dialog._spin(box, "platform_port", "监听端口", 1024, 65535, width=8)
        self.dialog._spin(box, "platform_daily_quota",
                          "每个插件每天的翻译次数上限（0 = 不限）", 0, 100000, width=8)
        self.dialog._spin(box, "platform_max_chars",
                          "单个插件一次最多多少字符", 100, 4000, width=8)

        row = ttk.Frame(box)
        row.pack(fill="x", padx=8, pady=(6, 2))
        ttk.Label(row, textvariable=self.status, style="Status.TLabel").pack(side="left")

        flow = ButtonFlow(box, gap=6, row_pady=4)
        flow.pack(fill="x", padx=8, pady=(2, 8))
        flow.add(ttk.Button(flow, text="立即应用", style="Accent.TButton",
                            command=self._apply_now))
        flow.add(ttk.Button(flow, text="复制平台地址", command=self._copy_url))
        flow.add(ttk.Button(flow, text="打开接口说明", command=self._open_docs))
        flow.add(ttk.Button(flow, text="在浏览器里打开", command=self._open_browser))

        # ---------------- 插件 ----------------
        pbox = ttk.LabelFrame(parent, text="插件")
        pbox.pack(fill="both", expand=True, padx=10, pady=(4, 8))
        theme.label(pbox,
                    "插件放在 plugins\\ 目录下（每个插件一个子目录，里面有 manifest.json）。"
                    "默认全部停用；启用后才会有令牌，权限也可以单独收紧。",
                    muted=True, anchor="w", wraplength=520, justify="left").pack(
            fill="x", padx=8, pady=(4, 2))

        self.list_frame = ttk.Frame(pbox)
        self.list_frame.pack(fill="x", padx=8)
        self.empty_label = theme.label(self.list_frame, "", muted=True, anchor="w",
                                       wraplength=500, justify="left")
        self.empty_label.pack(fill="x")

        flow2 = ButtonFlow(pbox, gap=6, row_pady=4)
        flow2.pack(fill="x", padx=8, pady=(6, 8))
        flow2.add(ttk.Button(flow2, text="重新扫描", command=self.refresh))
        flow2.add(ttk.Button(flow2, text="从 zip 安装…", command=self._install_zip))
        flow2.add(ttk.Button(flow2, text="打开插件目录", command=self._open_plugin_dir))
        flow2.add(ttk.Button(flow2, text="插件开发说明", command=self._open_docs))

        self.refresh()

    # ------------------------------------------------------------ 列表
    def refresh(self) -> None:
        for row in self.rows:
            try:
                row["frame"].destroy()
            except Exception:                     # noqa: BLE001
                pass
        self.rows = []
        platform = self.platform
        if platform is None:
            self.empty_label.configure(text="平台模块没加载起来，插件功能不可用")
            return
        try:
            plugins = platform.plugins.refresh()
        except Exception as exc:                  # noqa: BLE001
            self.empty_label.configure(text="扫描插件目录出错：%s" % exc)
            return
        if not plugins:
            self.empty_label.configure(
                text="还没有插件。把插件目录（含 manifest.json）复制到 %s 下，"
                     "再点「重新扫描」。" % platform.plugins.root)
            return
        self.empty_label.configure(text="")
        for index, plugin in enumerate(plugins):
            self._add_row(plugin, index)
        self._refresh_status()

    def _add_row(self, plugin, index: int) -> None:
        platform = self.platform
        # 每个插件一个"外层"容器：行 +（可选的）说明行都在里面。
        # 刷新时只 destroy 这个外层即可 —— 说明行如果挂在 list_frame 上，刷新会越堆越多。
        outer = ttk.Frame(self.list_frame)
        outer.pack(fill="x", pady=(2 if index else 0, 2))
        frame = ttk.Frame(outer)
        frame.pack(fill="x")
        enabled_var = tk.BooleanVar(value=bool(platform.plugins.is_enabled(plugin.id)))
        check = ttk.Checkbutton(
            frame, variable=enabled_var,
            command=lambda p=plugin, v=enabled_var: self._toggle(p, v))
        check.pack(side="left")
        if plugin.error:
            check.configure(state="disabled")

        text = plugin.name
        if plugin.version:
            text += " v%s" % plugin.version
        ttk.Label(frame, text=text, width=22, anchor="w").pack(side="left", padx=(4, 0))

        snap = platform.tokens.snapshot([plugin.id]).get(plugin.id, {})
        usage = "今日 %d/%d" % (snap.get("today", 0), platform.tokens.daily_quota)
        if not platform.tokens.daily_quota:
            usage = "今日 %d（不限）" % snap.get("today", 0)
        scopes = len(snap.get("scopes") or [])
        ttk.Label(frame, text="%s · 权限 %d 项" % (usage, scopes),
                  style="Muted.TLabel").pack(side="left", padx=(6, 0))

        ttk.Button(frame, text="权限", width=5,
                   command=lambda p=plugin: self._edit_scopes(p)).pack(side="right")
        ttk.Button(frame, text="令牌", width=5,
                   command=lambda p=plugin: self._show_token(p)).pack(
            side="right", padx=(0, 4))
        ttk.Button(frame, text="卸载", width=5,
                   command=lambda p=plugin: self._uninstall(p)).pack(
            side="right", padx=(0, 4))

        detail = plugin.error or ("%s（%s）" % (plugin.description, plugin.author)
                                  if plugin.description else "")
        if plugin.error:
            detail = "⚠ %s" % plugin.error
        elif snap.get("last_error"):
            detail = "最近一次出错：%s" % snap["last_error"]
        if detail:
            theme.label(outer, "　　" + detail, muted=True, anchor="w",
                        wraplength=500, justify="left").pack(fill="x")
        self.rows.append({"frame": outer, "plugin": plugin, "enabled": enabled_var})

    def _refresh_status(self) -> None:
        platform = self.platform
        if platform is None:
            return
        status = platform.status()
        if status["running"]:
            self.status.set("运行中：%s　·　插件 %d 个（启用 %d）"
                            % (status["url"], status["plugins"], status["plugins_enabled"]))
        elif status.get("last_error"):
            self.status.set("已停止（上次启动失败：%s）" % status["last_error"])
        else:
            self.status.set("已停止　·　插件 %d 个" % status["plugins"])

    # ------------------------------------------------------------ 动作
    def _apply_now(self) -> None:
        self.dialog.save()
        self.refresh()

    def _toggle(self, plugin, var) -> None:
        platform = self.platform
        wanted = bool(var.get())
        result = platform.plugins.set_enabled(plugin.id, wanted)
        if not result.get("ok"):
            var.set(not wanted)
            messagebox.showwarning("提示", str(result.get("error")), parent=self.dialog.window)
            return
        if wanted:
            self._say("已启用 %s%s" % (plugin.name,
                                      "（默认只有翻译权限，可在「权限」里加）"))
        else:
            self._say("已停用 %s" % plugin.name)
        self.refresh()

    def _say(self, text: str) -> None:
        try:
            self.dialog.status.set(text)
        except Exception:                         # noqa: BLE001
            pass

    def _show_token(self, plugin) -> None:
        platform = self.platform
        token = platform.tokens.token_for(plugin.id)
        if not token:
            messagebox.showinfo("提示", "这个插件还没有令牌（先启用它）",
                                parent=self.dialog.window)
            return
        try:
            self.dialog.window.clipboard_clear()
            self.dialog.window.clipboard_append(token)
            copied = "（已复制到剪贴板）"
        except Exception:                         # noqa: BLE001
            copied = ""
        messagebox.showinfo(
            "插件令牌",
            "%s 的令牌%s：\n\n%s\n\n"
            "插件调用平台时放在请求头里：\nAuthorization: Bearer <令牌>\n\n"
            "怀疑插件乱来就点「重置令牌」，旧令牌立刻作废。" %
            (plugin.name, copied, token), parent=self.dialog.window)

    def _edit_scopes(self, plugin) -> None:
        platform = self.platform
        from ..platform import ALL_SCOPES, SCOPE_LABELS

        window = tk.Toplevel(self.dialog.window)
        window.title("插件权限 · %s" % plugin.name)
        theme.prepare_window(window, self.config)
        theme.frameless_dialog(window, "插件权限 · %s" % plugin.name, size=(420, 340))
        if self.config.get("always_on_top", True):
            try:
                window.attributes("-topmost", True)
            except Exception:                     # noqa: BLE001
                pass

        theme.label(window, "默认只给「翻译」——写词典、学习这类会改动本机数据的能力，"
                            "要你明确勾上才生效。", muted=True, anchor="w",
                   wraplength=380, justify="left").pack(fill="x", padx=14, pady=(12, 6))
        current = set(platform.tokens.scopes(plugin.id))
        variables = {}
        for scope in ALL_SCOPES:
            var = tk.BooleanVar(value=scope in current)
            variables[scope] = var
            ttk.Checkbutton(window, text=SCOPE_LABELS.get(scope, scope),
                            variable=var).pack(anchor="w", padx=16, pady=2)

        row = ttk.Frame(window)
        row.pack(side="bottom", fill="x", padx=14, pady=12)

        def save() -> None:
            chosen = [scope for scope, var in variables.items() if var.get()]
            platform.tokens.set_scopes(plugin.id, chosen)
            self.refresh()
            window.destroy()
            self._say("已更新 %s 的权限" % plugin.name)

        ttk.Button(row, text="取消", command=window.destroy).pack(side="right")
        ttk.Button(row, text="保存", style="Accent.TButton", command=save).pack(
            side="right", padx=(0, 6))
        theme.place_near(window)

    def _uninstall(self, plugin) -> None:
        if not messagebox.askyesno(
                "卸载插件", "把 %s 的目录删掉？\n\n%s\n\n（令牌也会一起删除）"
                % (plugin.name, plugin.directory), parent=self.dialog.window):
            return
        result = self.platform.plugins.uninstall(plugin.id)
        if not result.get("ok"):
            messagebox.showwarning("提示", str(result.get("error")),
                                   parent=self.dialog.window)
        self.refresh()

    def _install_zip(self) -> None:
        path = filedialog.askopenfilename(
            title="选择插件压缩包", parent=self.dialog.window,
            filetypes=[("插件包", "*.zip"), ("所有文件", "*.*")])
        if not path:
            return
        result = self.platform.plugins.install_zip(path)
        if not result.get("ok"):
            messagebox.showwarning("安装失败", str(result.get("error")),
                                   parent=self.dialog.window)
        else:
            messagebox.showinfo("安装完成", result.get("message", ""),
                                parent=self.dialog.window)
        self.refresh()

    # ------------------------------------------------------------ 打开
    def _copy_url(self) -> None:
        platform = self.platform
        url = platform.url() if platform else ""
        try:
            self.dialog.window.clipboard_clear()
            self.dialog.window.clipboard_append(url)
            self._say("已复制：%s" % url)
        except Exception:                         # noqa: BLE001
            self._say(url)

    def _open_browser(self) -> None:
        import webbrowser

        platform = self.platform
        if platform is None:
            return
        if not platform.running:
            messagebox.showinfo("提示", "平台还没启动（先勾上开关点「立即应用」）",
                                parent=self.dialog.window)
            return
        try:
            webbrowser.open(platform.url())
        except Exception as exc:                  # noqa: BLE001
            self._say("打不开浏览器：%s" % exc)

    def _open_plugin_dir(self) -> None:
        platform = self.platform
        if platform is None:
            return
        try:
            from ..platform.plugins import ensure_plugins_dir

            ensure_plugins_dir(platform.plugins.root)   # 顺带把示例插件铺出来
        except Exception:                         # noqa: BLE001
            pass
        self.dialog._open_path(platform.plugins.root)

    def _open_docs(self) -> None:
        from .. import paths
        from .settings import SettingsDialog

        candidates = [paths.APP_DIR / "docs" / "平台接口.md",
                      paths.BUNDLE_DIR / "docs" / "平台接口.md",
                      paths.APP_DIR / "平台接口.md"]
        for path in candidates:
            if path.is_file():
                SettingsDialog._open_path(path)
                return
        messagebox.showinfo("提示", "接口说明文档：docs\\平台接口.md\n"
                                    "（发行版里在程序目录的 docs 文件夹）",
                            parent=self.dialog.window)
