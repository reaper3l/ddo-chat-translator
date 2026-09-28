"""中译英窗口：我要说的话 → 外国玩家习惯的英文（自动复制到剪贴板）。

还会读**最近采集到的聊天上下文**，自动推荐几条"我可能想说的话"（中文 + 可直接粘贴的
游戏英文），点一行就复制英文、双击把中文放进输入框 —— 不会英文也能接着聊。
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from .. import config as config_module
from ..prompt import (build_reply_messages, build_reply_system_prompt,
                      build_zh2en_system_prompt)
from ..replies import parse_suggestions
from . import theme

QUICK_PHRASES = [
    ("马上到", "omw"),
    ("马上回来", "brb"),
    ("稍等", "sec"),
    ("谢谢", "ty"),
    ("不客气", "np"),
    ("抱歉", "my bad"),
    ("缺人，有人来吗", "LFM"),
    ("找队伍", "LFG"),
    ("祝好运", "gl"),
    ("玩得开心", "hf"),
    ("打得好", "gg"),
    ("没蓝了", "oom"),
    ("复活我", "rez plz"),
    ("能共享任务给我吗", "can you share the quest?"),
    ("我要去做任务了", "gtg quest"),
    ("我先下线了", "cya"),
]


class CnToEnDialog:
    def __init__(self, app, auto_suggest=None) -> None:
        self.app = app
        self.queue: "queue.Queue[tuple]" = queue.Queue()
        self.busy = False
        self.suggest_busy = False
        self.suggestions = []          # [(中文, 英文), ...]
        if auto_suggest is None:
            auto_suggest = bool(self.app.config.get("cn2en_auto_suggest", True))
        self.auto_suggest = auto_suggest

        self.window = tk.Toplevel(app.root)
        self.window.title("中译英 —— 说给外国玩家听")
        self.window.attributes("-topmost", True)
        self.window.transient(app.root)
        theme.prepare_window(self.window, app.config)
        # autofocus：这个窗口打开就是为了打字，所以显示后直接把焦点给输入框
        theme.frameless_dialog(self.window, "中译英 —— 说给外国玩家听",
                               autofocus=True, size=(620, 720))
        self._build()
        if self.auto_suggest:
            self.window.after(200, self.suggest_replies)   # 打开就自动给一批回复
        self._poll()

    def _build(self) -> None:
        theme.label(self.window, "我要说（中文）", anchor="w").pack(
            fill="x", padx=12, pady=(10, 2))
        self.input = theme.text_widget(self.window, height=3, wrap="word",
                                       font=(self.app.config.get("font_family",
                                                                 "Microsoft YaHei"), 11))
        self.input.pack(fill="x", padx=12)
        # 告诉主题"输入框是这一个"，它会在窗口显示后把焦点放上来
        theme.set_dialog_input(self.window, self.input)
        self.input.focus_set()
        self.input.bind("<Control-Return>", self._on_enter)
        self.input.bind("<Return>", self._on_enter)

        row = ttk.Frame(self.window)
        row.pack(fill="x", padx=12, pady=6)
        ttk.Button(row, text="翻译（Enter）", command=self.translate).pack(side="left")
        self.auto_copy = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="自动复制结果", variable=self.auto_copy).pack(
            side="left", padx=8)
        self.status = tk.StringVar(value="")
        ttk.Label(row, textvariable=self.status, foreground="#080").pack(side="left")

        # ---------------- 根据聊天内容推荐回复（中英对照） ----------------
        head = ttk.Frame(self.window)
        head.pack(fill="x", padx=12, pady=(4, 2))
        theme.label(head, "根据聊天内容推荐回复（点一行复制英文，双击把中文填进上面）",
                    muted=True).pack(side="left")
        ttk.Button(head, text="重新生成", width=9,
                   command=self.suggest_replies).pack(side="right")
        self.auto_suggest_var = tk.BooleanVar(
            value=bool(self.app.config.get("cn2en_auto_suggest", True)))
        ttk.Checkbutton(head, text="打开时自动推荐", variable=self.auto_suggest_var,
                        command=self._save_auto_suggest).pack(side="right", padx=6)

        list_frame = ttk.Frame(self.window)
        list_frame.pack(fill="both", expand=False, padx=12)
        self.suggest_list = ttk.Treeview(list_frame, columns=("zh", "en"),
                                         show="headings", height=6, selectmode="browse")
        self.suggest_list.heading("zh", text="中文（我要表达的意思）")
        self.suggest_list.heading("en", text="英文（可直接粘贴进游戏）")
        self.suggest_list.column("zh", width=250, anchor="w")
        self.suggest_list.column("en", width=320, anchor="w")
        self.suggest_list.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(list_frame, orient="vertical",
                               command=self.suggest_list.yview)
        self.suggest_list.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.suggest_list.bind("<ButtonRelease-1>", self._on_suggest_click)
        self.suggest_list.bind("<Double-Button-1>", self._on_suggest_double)
        self.suggest_hint = theme.label(
            self.window,
            "打开后会自动读最近 8 条聊天，生成 4 条回复建议（每次生成消耗一次接口调用）。",
            muted=True)
        self.suggest_hint.pack(fill="x", padx=12, pady=(2, 2))

        theme.label(self.window, "常用语（点一下直接复制英文）", muted=True, anchor="w").pack(
            fill="x", padx=12, pady=(4, 2))
        grid = ttk.Frame(self.window)
        grid.pack(fill="x", padx=12)
        for index, (zh, en) in enumerate(QUICK_PHRASES):
            ttk.Button(grid, text=zh, width=14,
                       command=lambda value=en: self._copy(value)).grid(
                row=index // 4, column=index % 4, padx=2, pady=2, sticky="we")

        theme.label(self.window, "英文（可直接框选复制）", muted=True, anchor="w").pack(
            fill="x", padx=12, pady=(10, 2))
        self.output = theme.text_widget(self.window, height=5, wrap="word",
                                        font=("Consolas", 12),
                                        fg=theme.PALETTE["ok"])
        self.output.pack(fill="both", expand=True, padx=12, pady=(0, 10))

    def _on_enter(self, _event=None):
        self.translate()
        return "break"

    def _copy(self, text: str) -> None:
        self.app.root.clipboard_clear()
        self.app.root.clipboard_append(text)
        self.status.set("已复制：%s" % text)

    # ------------------------------------------------- 根据上下文推荐回复
    def _save_auto_suggest(self) -> None:
        self.app.config["cn2en_auto_suggest"] = bool(self.auto_suggest_var.get())
        config_module.save_config(self.app.config)

    def _on_suggest_click(self, _event=None) -> None:
        """点一行 → 复制它的英文（能直接粘进游戏）。"""
        item = self.suggest_list.focus()
        values = self.suggest_list.item(item, "values") if item else ()
        if len(values) >= 2:
            self._copy(values[1])

    def _on_suggest_double(self, _event=None) -> str:
        """双击一行 → 把中文放进输入框（想改一改再说）。"""
        item = self.suggest_list.focus()
        values = self.suggest_list.item(item, "values") if item else ()
        if len(values) >= 1:
            self.input.delete("1.0", "end")
            self.input.insert("1.0", values[0])
            try:
                self.input.focus_set()
            except Exception:
                pass
        return "break"

    def suggest_replies(self) -> None:
        """读最近聊天上下文，让模型给几条"我可能想说的话"（中英对照）。"""
        if self.suggest_busy:
            return
        engine = self.app.pipeline.engine
        if not engine.available():
            self.suggest_hint.configure(text="当前引擎不可用（DeepSeek 需要填 API Key）")
            return
        if not getattr(engine, "supports_chat", False):
            self.suggest_hint.configure(
                text="当前引擎（%s）不支持按上下文生成回复，请把引擎换成 DeepSeek。"
                % engine.describe())
            return
        self.suggest_busy = True
        self.suggest_hint.configure(text="正在根据聊天内容想几句…")
        context = self.app.pipeline.recent_context(limit=8)
        events = self.app.pipeline.recent_system_events(limit=4)
        draft = ""
        try:
            draft = self.input.get("1.0", "end").strip()
        except Exception:
            pass
        memory = self.app.memory

        def work() -> None:
            try:
                messages = build_reply_messages(
                    build_reply_system_prompt(memory), context, events, draft)
                result = engine.translate("", messages, timeout=30)
                if result.ok:
                    self.queue.put(("suggest", parse_suggestions(result.text)))
                else:
                    self.queue.put(("suggest_error", result.error or "生成失败"))
            except Exception as exc:
                self.queue.put(("suggest_error", "出错：%s" % exc))

        threading.Thread(target=work, daemon=True).start()

    def translate(self) -> None:
        text = self.input.get("1.0", "end").strip()
        if not text or self.busy:
            return
        self.busy = True
        self.status.set("翻译中…")
        engine = self.app.pipeline.engine
        memory = self.app.memory

        def work() -> None:
            if not engine.available():
                self.queue.put(("error", "当前引擎不可用（DeepSeek 需要填 API Key）"))
                return
            try:
                messages = [
                    {"role": "system", "content": build_zh2en_system_prompt(memory)},
                    {"role": "user", "content": text},
                ]
                result = engine.translate(text, messages, timeout=20)
                if result.ok:
                    self.queue.put(("ok", result.text))
                else:
                    self.queue.put(("error", result.error or "翻译失败"))
            except Exception as exc:
                self.queue.put(("error", "出错：%s" % exc))

        threading.Thread(target=work, daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "ok":
                    self.busy = False
                    self.output.delete("1.0", "end")
                    self.output.insert("1.0", payload)
                    self.status.set("好了")
                    if self.auto_copy.get():
                        self._copy(payload)
                elif kind == "suggest":
                    self.suggest_busy = False
                    self._show_suggestions(payload)
                elif kind == "suggest_error":
                    self.suggest_busy = False
                    self.suggest_hint.configure(text="没能生成建议：%s" % payload)
                else:
                    self.busy = False
                    self.status.set(payload)
        except queue.Empty:
            pass
        try:
            self.window.after(120, self._poll)
        except tk.TclError:
            pass

    def _show_suggestions(self, pairs) -> None:
        """把建议填进列表（中英对照）。"""
        for row in self.suggest_list.get_children():
            self.suggest_list.delete(row)
        if not pairs:
            self.suggest_hint.configure(
                text="没生成出建议（可能上下文太少或接口返回格式不对），点「重新生成」再试。"
                "也可以直接在下面输入中文翻译。")
            return
        for zh, en in pairs:
            self.suggest_list.insert("", "end", values=(zh, en))
        self.suggestions = list(pairs)
        self.suggest_hint.configure(
            text="点一行复制英文；双击把中文填进输入框。每次生成消耗一次接口调用。")
