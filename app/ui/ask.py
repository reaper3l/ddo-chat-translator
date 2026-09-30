"""应用自己的"小输入框"（1~2 个字段 + 确定/取消）。

为什么不用 `tkinter.simpledialog`：本程序的窗口全都是**无边框 + 永远置顶**的，
simpledialog 建出来的是普通窗口，会被挡在后面（用户看不见），可它又是**模态**的 ——
于是表现就是"点了按钮程序像卡死"。（实测反馈：词典里点「新增/覆盖」卡死，就是这个。）

这里用和其它对话框完全一样的做法：自绘深色标题栏 + `-topmost` + 主题里的焦点处理。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, List, Sequence, Tuple

from . import theme


class AskDialog:
    """带主题的输入框。

    `fields` 是 [(标签, 初始值), ...]（1~2 项就够用了）；
    点「确定」且每项都非空时，回调 `on_submit([值1, 值2])` 并关窗。
    """

    def __init__(self, app, title: str, fields: Sequence[Tuple[str, str]],
                 on_submit: Callable[[List[str]], None], hint: str = "",
                 submit_text: str = "确定", size=None) -> None:
        self.app = app
        self.fields = list(fields)
        self.on_submit = on_submit
        self.entries: List[tk.Widget] = []

        self.window = tk.Toplevel(app.root)
        self.window.title(title)
        self.window.transient(app.root)
        try:
            self.window.attributes("-topmost", True)
        except Exception:
            pass
        theme.prepare_window(self.window, app.config)
        # 不写死宽高：让窗口按内容自然大小（字段数不一样，算死高度会把按钮挤出去），
        # 只把位置交给 place_near 去挑（父窗口旁边）
        theme.frameless_dialog(self.window, title, on_close=self.window.destroy,
                               autofocus=True, size=size)
        self._build(hint, submit_text)
        try:
            self.window.minsize(360, 120)
        except Exception:
            pass

    def _build(self, hint: str, submit_text: str) -> None:
        if hint:
            theme.label(self.window, hint, muted=True, anchor="w").pack(
                fill="x", padx=12, pady=(10, 0))
        font = (self.app.config.get("font_family", "Microsoft YaHei"), 11)
        for label, initial in self.fields:
            theme.label(self.window, label, anchor="w").pack(
                fill="x", padx=12, pady=(8, 2))
            entry = ttk.Entry(self.window, font=font)
            entry.pack(fill="x", padx=12)
            if initial:
                entry.insert(0, initial)
            entry.bind("<Return>", lambda _event: self.submit())
            self.entries.append(entry)

        row = ttk.Frame(self.window)
        row.pack(fill="x", padx=12, pady=(10, 12))
        ttk.Button(row, text=submit_text, style="Accent.TButton",
                   command=self.submit).pack(side="left")
        ttk.Button(row, text="取消", command=self.window.destroy).pack(
            side="left", padx=6)

        if self.entries:
            theme.set_dialog_input(self.window, self.entries[0])
            try:
                self.entries[0].focus_set()
            except Exception:
                pass
        try:
            self.window.grab_set()          # 模态：别让用户跑到后面去点
        except Exception:
            pass

    def submit(self) -> None:
        values = [str(entry.get()).strip() for entry in self.entries]
        if not values or not all(values):
            messagebox.showinfo("还差一点", "每一项都要填上才能保存", parent=self.window)
            return
        self.window.destroy()
        self.on_submit(values)


def ask_two(app, title: str, first_label: str, second_label: str,
            on_submit: Callable[[str, str], None], hint: str = "",
            first: str = "", second: str = "") -> AskDialog:
    """两个字段的快捷写法（术语 / 翻译这种）。"""
    return AskDialog(
        app, title, [(first_label, first), (second_label, second)],
        on_submit=lambda values: on_submit(values[0], values[1]), hint=hint)


def ask_one(app, title: str, label: str,
            on_submit: Callable[[str], None], hint: str = "",
            initial: str = "") -> AskDialog:
    """一个字段的快捷写法。"""
    return AskDialog(
        app, title, [(label, initial)],
        on_submit=lambda values: on_submit(values[0]), hint=hint)
