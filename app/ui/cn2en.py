"""中译英窗口：我要说的话 → 外国玩家习惯的英文（自动复制到剪贴板）。"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk

from ..prompt import build_zh2en_system_prompt
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
    def __init__(self, app) -> None:
        self.app = app
        self.queue: "queue.Queue[tuple]" = queue.Queue()
        self.busy = False

        self.window = tk.Toplevel(app.root)
        self.window.title("中译英 —— 说给外国玩家听")
        self.window.attributes("-topmost", True)
        self.window.transient(app.root)
        theme.prepare_window(self.window, app.config)
        # autofocus：这个窗口打开就是为了打字，所以显示后直接把焦点给输入框
        theme.frameless_dialog(self.window, "中译英 —— 说给外国玩家听",
                               autofocus=True, size=(560, 560))
        self._build()
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

        theme.label(self.window, "常用语（点一下直接复制英文）", muted=True, anchor="w").pack(
            fill="x", padx=12, pady=(6, 2))
        grid = ttk.Frame(self.window)
        grid.pack(fill="x", padx=12)
        for index, (zh, en) in enumerate(QUICK_PHRASES):
            ttk.Button(grid, text=zh, width=17,
                       command=lambda value=en: self._copy(value)).grid(
                row=index // 3, column=index % 3, padx=2, pady=2, sticky="we")

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
                self.busy = False
                if kind == "ok":
                    self.output.delete("1.0", "end")
                    self.output.insert("1.0", payload)
                    self.status.set("好了")
                    if self.auto_copy.get():
                        self._copy(payload)
                else:
                    self.status.set(payload)
        except queue.Empty:
            pass
        try:
            self.window.after(120, self._poll)
        except tk.TclError:
            pass
