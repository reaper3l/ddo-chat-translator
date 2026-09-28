"""「发现新版本」对话框：看更新说明 → 一键下载并自动升级；也能去发行页手动下载。"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from .. import config as config_module
from .. import paths
from .. import update as update_module
from . import theme


class UpdateDialog:
    def __init__(self, app, info: update_module.UpdateInfo) -> None:
        self.app = app
        self.info = info
        self.queue: "queue.Queue[tuple]" = queue.Queue()
        self.busy = False

        self.window = tk.Toplevel(app.root)
        self.window.title("发现新版本")
        self.window.transient(app.root)
        theme.prepare_window(self.window, app.config)
        theme.frameless_dialog(self.window, "发现新版本", size=(600, 520))
        self._build()
        self._poll()

    def _build(self) -> None:
        head = ttk.Frame(self.window)
        head.pack(fill="x", padx=14, pady=(10, 4))
        theme.label(head, "发现新版本 v%s（当前 v%s）" % (self.info.version,
                                                          self.info.current)).pack(
            side="left")
        if self.info.asset_size:
            theme.label(head, "安装包 %.1f MB" % self.info.size_mb, muted=True).pack(
                side="right")

        theme.label(self.window, "更新内容：", muted=True, anchor="w").pack(
            fill="x", padx=14, pady=(6, 2))
        box = theme.text_widget(self.window, height=14, wrap="word",
                                font=(self.app.config.get("font_family",
                                                          "Microsoft YaHei"), 10))
        box.pack(fill="both", expand=True, padx=14)
        box.insert("1.0", update_module.notes_brief(self.info.notes) or "（这次没有写更新说明）")
        box.configure(state="disabled")

        bar = ttk.Frame(self.window)
        bar.pack(fill="x", padx=14, pady=(6, 0))
        self.progress = ttk.Progressbar(bar, mode="determinate", maximum=100)
        self.progress.pack(fill="x")
        self.status = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.status, style="Status.TLabel").pack(
            anchor="w", pady=(2, 0))

        row = ttk.Frame(self.window)
        row.pack(fill="x", padx=14, pady=(6, 12))
        # 先看签名：只有"公钥已配置 + 有签名"才提供自动安装；否则只引导去发行页
        self.signature = update_module.parse_signature(self.info.notes)
        self.pubkey_ready = bool(update_module.RELEASE_PUBKEY.strip())
        self.trusted = bool(self.signature and self.pubkey_ready)
        if update_module.can_self_update() and self.info.asset_url and self.trusted:
            ttk.Button(row, text="现在升级（自动下载并重启）", style="Accent.TButton",
                       command=self.start_update).pack(side="left")
        elif update_module.can_self_update() and self.info.asset_url:
            theme.label(row, "该发行版没有（或无法验证）签名 → 出于安全不自动安装",
                        muted=True).pack(side="left")
        else:
            theme.label(row, "当前是源码运行，请用下面的按钮去发行页下载（或 git pull）",
                        muted=True).pack(side="left")
        ttk.Button(row, text="打开发行页", command=self.open_page).pack(
            side="left", padx=6)
        ttk.Button(row, text="稍后再说", command=self.window.destroy).pack(side="right")
        ttk.Button(row, text="跳过这个版本", command=self.skip_version).pack(
            side="right", padx=6)

        # 没签名时给一条"我知道风险"的路（默认不勾、勾了才记得下）
        if update_module.can_self_update() and self.info.asset_url and not self.trusted  \
                and self.pubkey_ready:
            line = ttk.Frame(self.window)
            line.pack(fill="x", padx=14, pady=(0, 10))
            self.allow_unsigned = tk.BooleanVar(
                value=bool(self.app.config.get("update_allow_unsigned", False)))
            ttk.Checkbutton(line, variable=self.allow_unsigned,
                            text="我了解风险，允许自动安装未签名的包",
                            command=self._save_allow_unsigned).pack(side="left")
            ttk.Button(line, text="仍要安装", command=self.start_update).pack(
                side="left", padx=8)

    # ------------------------------------------------------------------ 操作
    def _save_allow_unsigned(self) -> None:
        self.app.config["update_allow_unsigned"] = bool(self.allow_unsigned.get())
        config_module.save_config(self.app.config)

    def skip_version(self) -> None:
        self.app.config["update_skipped"] = self.info.version
        config_module.save_config(self.app.config)
        self.window.destroy()

    def open_page(self) -> None:
        url = self.info.page_url or update_module.HOMEPAGE
        if not update_module.page_url_ok(url):      # 只有 Gitee/GitHub 才开
            url = update_module.HOMEPAGE + "/releases"
        if not update_module.open_page(url):
            messagebox.showwarning("打不开浏览器", "请手动访问：%s" % url,
                                   parent=self.window)

    def start_update(self) -> None:
        if self.busy:
            return
        if not self.info.asset_url:
            self.open_page()
            return
        if not getattr(self, "trusted", False) and \
                not bool(self.app.config.get("update_allow_unsigned", False)):
            messagebox.showwarning(
                "这个包没有可信签名",
                "出于安全，程序不会自动安装没有你签名（或签名验证不通过）的安装包。\n\n"
                "你可以：\n"
                "· 点「打开发行页」自己下载解压（适合你确认过的情况）；\n"
                "· 或者勾选下面的「我了解风险，允许自动安装未签名的包」。",
                parent=self.window)
            return
        self.busy = True
        self.status.set("开始下载…")
        target = paths.DATA_DIR / "updates" / (self.info.asset_name
                                              or ("DDO_v%s.zip" % self.info.version))
        url, version = self.info.asset_url, self.info.version

        def work() -> None:
            try:
                def progress(done: int, total: int) -> None:
                    self.queue.put(("progress", (done, total)))

                update_module.download(url, target, progress=progress)
                self.queue.put(("progress_text", "正在解压新版本…"))
                # 下载完再验一次签名（这次是对着真实文件的 sha256 验，最权威）
                ok, reason = update_module.verify_package(target, self.info)
                if not ok and not bool(self.app.config.get("update_allow_unsigned",
                                                           False)):
                    self.queue.put(("untrusted", reason))
                    return
                self.queue.put(("progress_text", "签名校验：%s" % reason))
                prepared = update_module.prepare_update(target, version)
                try:
                    target.unlink()            # 安装包解压完就没用了（省 95MB）
                except Exception:
                    pass
                self.queue.put(("ready", prepared))
            except Exception as exc:
                self.queue.put(("error", str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "progress":
                    done, total = payload
                    if total:
                        self.progress["value"] = done * 100.0 / total
                        self.status.set("下载中 %.1f / %.1f MB"
                                        % (done / 1048576.0, total / 1048576.0))
                    else:
                        self.status.set("下载中 %.1f MB" % (done / 1048576.0))
                elif kind == "progress_text":
                    self.status.set(str(payload))
                elif kind == "ready":
                    self.busy = False
                    self.progress["value"] = 100
                    self.status.set("下载完成，正在重启更新…")
                    self._finish(payload)
                    return
                elif kind == "error":
                    self.busy = False
                    self.status.set("更新失败：%s" % payload)
                    messagebox.showwarning(
                        "自动更新失败",
                        "没能完成自动更新：%s\n\n可以点「打开发行页」手动下载安装包，"
                        "解压后覆盖到程序目录即可（data 文件夹留着，配置和学习库都在里面）。"
                        % payload, parent=self.window)
                elif kind == "untrusted":
                    self.busy = False
                    self.status.set("签名校验没通过，已放弃自动安装：%s" % payload)
                    messagebox.showwarning(
                        "签名校验没通过，已放弃自动安装",
                        "%s\n\n这个包**没有**通过签名校验（可能不是你的私钥签的，"
                        "也可能下载被改过）。程序没有改动你的任何文件。\n\n"
                        "要继续的话：点「打开发行页」自己下载，或者勾选"
                        "「我了解风险，允许自动安装未签名的包」再点一次升级。\n\n"
                        "原因：%s" % (self.info.asset_name or "安装包", payload),
                        parent=self.window)
        except queue.Empty:
            pass
        try:
            self.window.after(150, self._poll)
        except tk.TclError:
            pass

    def _finish(self, prepared) -> None:
        """启动解压出来的新版本，然后退出程序（由它覆盖文件并重启）。"""
        try:
            prepared.launch()
        except Exception as exc:
            messagebox.showwarning("启动新版本失败", str(exc), parent=self.window)
            return
        try:
            self.window.destroy()
        except Exception:
            pass
        self.app.set_status("正在更新，程序会自己重启…", "info")
        self.app.root.after(600, self.app.quit_app)
