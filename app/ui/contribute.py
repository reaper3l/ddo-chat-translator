"""「参与改进」对话框：把用户**自己确认过的**术语/纠错匿名贡献出去。

设计原则（用户反馈过：不想被强制、也不想被盯着看）：

* **默认关闭**：不打开开关就什么都不发；
* **能预览**：点一下就看到"到底会发什么"，一目了然；
* **只发用户确认过的**：F10 改过的句子 + 他自己加的术语，不碰原始聊天流；
* **本地先脱敏**：链接、邮箱、长数字、带玩家名的聊天行，在离开这台机器之前就被丢掉；
* 没有接收地址时也能用：**复制贡献码**发给作者即可（贡献码是一行可压缩文本）。
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk

from .. import config as config_module
from .. import contribute
from .. import __version__
from . import theme


class ContributionDialog:
    def __init__(self, app) -> None:
        self.app = app
        self.window = tk.Toplevel(app.root)
        self.window.title("参与改进（匿名贡献术语）")
        self.window.transient(app.root)
        theme.prepare_window(self.window, app.config)
        theme.frameless_dialog(self.window, "参与改进（匿名贡献术语）", size=(620, 470))
        self.items = {"terms": [], "phrases": [], "skipped": {}}
        self.queue: "queue.Queue[tuple]" = queue.Queue()
        self._build()
        self.refresh()
        self._poll()

    # ------------------------------------------------------------------ 构建
    def _build(self) -> None:
        box = ttk.Frame(self.window)
        box.pack(fill="x", padx=12, pady=(10, 4))
        theme.label(
            box,
            "这个功能是自愿的：打开之后，程序才会把你**自己确认过**的内容匿名发给作者，\n"
            "用来改进内置术语表。默认关闭，随时可以关掉。",
            muted=True, justify="left").pack(anchor="w")

        self.enabled = tk.BooleanVar(
            value=bool(self.app.config.get("contribute_enabled", False)))
        ttk.Checkbutton(box, text="参与改进（匿名贡献我确认过的纠错和术语）",
                        variable=self.enabled,
                        command=self._save_switch).pack(anchor="w", pady=(6, 2))

        self.auto_send = tk.BooleanVar(
            value=bool(self.app.config.get("contribute_auto_send", True)))
        ttk.Checkbutton(
            box, text="关闭程序时自动上传（不勾 = 只在我点「直接上传」时发）",
            variable=self.auto_send, command=self._save_switch).pack(anchor="w")

        theme.label(
            box,
            "只发送：你按 F10 改过的句子、以及你自己在词典里加的术语。\n"
            "发送前会在本机去掉链接、邮箱、长数字和带玩家名的聊天行；原始聊天不会上传。",
            muted=True, justify="left").pack(anchor="w")

        self.count_label = theme.label(self.window, "", anchor="w")
        self.count_label.pack(fill="x", padx=12, pady=(8, 2))

        row = ttk.Frame(self.window)
        row.pack(fill="x", padx=12, pady=4)
        ttk.Button(row, text="预览要发送的内容",
                   command=self.preview).pack(side="left")
        ttk.Button(row, text="复制贡献码",
                   command=self.copy_code).pack(side="left", padx=6)
        self.send_button = ttk.Button(row, text="直接上传", command=self.send)
        self.send_button.pack(side="left", padx=6)
        ttk.Button(row, text="关闭", command=self.window.destroy).pack(side="right")

        self.status = tk.StringVar(value="")
        theme.label(self.window, "", muted=True).pack(fill="x", padx=12)
        ttk.Label(self.window, textvariable=self.status,
                  foreground=theme.PALETTE["ok"]).pack(fill="x", padx=12, pady=(2, 8))

        theme.label(
            self.window,
            "没有配置接收地址（或者你想手动转发）时，用「复制贡献码」——\n"
            "它会复制一行文本，粘到反馈 issue / 群里发给作者就行，作者那边可以自动解析。",
            muted=True, justify="left").pack(anchor="w", padx=12)

    # ------------------------------------------------------------------ 行为
    def _save_switch(self) -> None:
        self.app.config["contribute_enabled"] = bool(self.enabled.get())
        self.app.config["contribute_auto_send"] = bool(self.auto_send.get())
        config_module.save_config(self.app.config)
        self.refresh()
        if not self.enabled.get():
            self.status.set("已关闭参与改进（什么都不发）")
        elif self.auto_send.get():
            self.status.set("已开启：关程序时会自动上传新确认的内容；也可以随时手动发")
        else:
            self.status.set("已开启，但只在你点「直接上传」时发送")

    def refresh(self) -> None:
        self.items = contribute.collect_items(self.app.memory)
        terms = len(self.items["terms"])
        phrases = len(self.items["phrases"])
        skipped = self.items.get("skipped", {})
        if not self.enabled.get():
            self.count_label.configure(
                text="当前未开启：程序不会发送任何东西。可贡献 %d 条（术语 %d、整句 %d）。"
                     % (terms + phrases, terms, phrases))
        else:
            self.count_label.configure(
                text="本次可贡献：%d 条（术语 %d、整句 %d）；已贡献过 %d 条不再重复。"
                     % (terms + phrases, terms, phrases, int(skipped.get("sent", 0))))
        url = str(self.app.config.get("contribute_url") or "")
        self.send_button.configure(state="normal" if url else "disabled",
                                   text="直接上传" if url else "直接上传（未配置）")

    def _payload(self) -> dict:
        if not (self.items["terms"] or self.items["phrases"]):
            return None
        from .. import __version__

        return contribute.build_payload(self.items, version=__version__)

    def preview(self) -> None:
        payload = self._payload()
        if payload is None:
            self.status.set("没有新的内容可贡献（已经贡献过的不会重复发送）")
            return
        text = contribute.preview_text(payload)
        window = tk.Toplevel(self.window)
        window.title("将要发送的内容")
        theme.prepare_window(window, self.app.config)
        theme.frameless_dialog(window, "将要发送的内容", size=(560, 480))
        box = theme.text_widget(window, wrap="word")
        box.pack(fill="both", expand=True, padx=12, pady=10)
        box.insert("1.0", text)
        box.configure(state="disabled")
        ttk.Button(window, text="关闭", command=window.destroy).pack(pady=(0, 10))

    def copy_code(self) -> None:
        payload = self._payload()
        if payload is None:
            self.status.set("没有新的内容可贡献（已经贡献过的不会重复发送）")
            return
        code = contribute.encode_code(payload)
        self.app.root.clipboard_clear()
        self.app.root.clipboard_append(code)
        self.status.set("贡献码已复制（%d 字符），粘到反馈 issue 或发群里即可" % len(code))
        contribute.mark_sent(self.items["terms"] + self.items["phrases"])
        self.refresh()

    def send(self) -> None:
        url = str(self.app.config.get("contribute_url") or "")
        payload = self._payload()
        if payload is None:
            self.status.set("没有新的内容可贡献（已经贡献过的不会重复发送）")
            return
        self.status.set("正在发送…")
        self.send_button.configure(state="disabled")
        items = self.items["terms"] + self.items["phrases"]

        def work() -> None:
            ok, reason = contribute.send(payload, url)
            if ok:
                contribute.mark_sent(items)
            self.queue.put(("done", ok, reason))

        threading.Thread(target=work, name="contribute-send", daemon=True).start()

    def _poll(self) -> None:
        """事件循环里取结果（Tk 只能主线程碰，所以走队列）。"""
        try:
            while True:
                _kind, ok, reason = self.queue.get_nowait()
                self.status.set("已发送，谢谢！" if ok
                                else "没能发送：%s（可以改用「复制贡献码」）" % reason)
                self.refresh()
        except queue.Empty:
            pass
        try:
            self.window.after(150, self._poll)
        except tk.TclError:
            pass


class ContributionInviteDialog:
    """启动时的一次邀请：要不要参与改进（贡献术语）。

    为什么要有它：这个功能藏在「设置 → 关于」里，绝大多数人根本不会点进去
    （作者反馈：入口太隐蔽）。所以在启动后主动问一次。

    三条底线不动摇：
    * 不偷偷开：点"愿意"才打开开关，而且立刻打开贡献窗口，让用户先看到会发什么；
    * 不烦人：每版只问一次，点"不再提醒"以后永不出现；
    * 说清楚：只发用户自己确认过的句子/术语，原始聊天不上传，随时能关。
    """

    def __init__(self, app, on_accept=None) -> None:
        self.app = app
        self.on_accept = on_accept
        self.window = tk.Toplevel(app.root)
        self.window.title("参与改进")
        self.window.transient(app.root)
        theme.prepare_window(self.window, app.config)
        theme.frameless_dialog(self.window, "愿意帮忙改进翻译吗？", size=(580, 340))
        self._build()

    def _build(self) -> None:
        box = ttk.Frame(self.window)
        box.pack(fill="both", expand=True, padx=14, pady=(12, 4))
        theme.label(box, "这个翻译器靠大家的纠错慢慢变准。", anchor="w").pack(fill="x")
        theme.label(
            box,
            "愿意的话，程序会把你「自己确认过」的内容匿名发给作者：\n"
            "· 你按 F10 改过的句子；\n"
            "· 你自己在词典里加的术语。\n\n"
            "原始聊天内容不会上传；发送前会在本机去掉链接、邮箱、连续数字，\n"
            "并跳过带玩家名的聊天行。发之前可以先预览，随时能关掉。\n\n"
            "同意之后：关闭程序时会自动把这些内容发出去（不用每次手动点），\n"
            "也可以在那个窗口里取消「自动上传」，改成只手动发。\n\n"
            "现在是关着的：不点下面的按钮，什么都不会发。",
            muted=True, justify="left").pack(anchor="w", pady=(8, 0))

        row = ttk.Frame(self.window)
        row.pack(fill="x", padx=14, pady=(6, 12))
        ttk.Button(row, text="愿意，按这个来", style="Accent.TButton",
                   command=self.accept).pack(side="left")
        ttk.Button(row, text="以后再说", command=self.later).pack(side="left", padx=8)
        ttk.Button(row, text="不再提醒", command=self.never).pack(side="left")
        ttk.Button(row, text="关闭", command=self.window.destroy).pack(side="right")

    # ------------------------------------------------------------------ 动作
    def accept(self) -> None:
        """用户点了"愿意"：这时才打开开关（含关闭时自动上传），并打开贡献窗口。"""
        self.app.config["contribute_enabled"] = True
        self.app.config["contribute_auto_send"] = True
        self._mark(version=__version__)
        self.window.destroy()
        callback = self.on_accept or self.app.open_contribution
        try:
            callback()
        except Exception:                          # noqa: BLE001
            pass
        self.app.set_status("已打开「参与改进」：就是下面这个窗口，先预览再决定发不发",
                            "info")

    def later(self) -> None:
        """点"以后再说"：这一版先不问了，下个版本再提一次。"""
        self._mark(version=__version__)
        self.window.destroy()

    def never(self) -> None:
        """点"不再提醒"：以后永不出现（想参与可以去设置 → 关于）。"""
        self._mark(version=__version__, done=True)
        self.window.destroy()
        self.app.set_status("好的，以后不再提醒（想参与随时可在 设置 → 关于 里打开）",
                            "info")

    def _mark(self, version: str, done: bool = False) -> None:
        self.app.config["contribute_invite_version"] = version
        if done:
            self.app.config["contribute_invite_done"] = True
        config_module.save_config(self.app.config)
