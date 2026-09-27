"""界面小工具。"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import theme


class ScrollableFrame(ttk.Frame):
    """内容比窗口高时可以滚动的容器（设置页用，避免控件被挤出可视区域）。

    用法：把控件放进 `scrollable.inner`。
    """

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        # 必须显式给底色，否则默认是系统浅灰，设置页看起来就不像暗色主题了
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0,
                                bg=theme.PALETTE["bg"])
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        self.inner = ttk.Frame(self.canvas)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.inner.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)

    def _on_inner_configure(self, _event=None) -> None:
        try:
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        except Exception:
            pass

    def _on_canvas_configure(self, event) -> None:
        try:
            self.canvas.itemconfigure(self._window, width=event.width)
        except Exception:
            pass

    def _on_mousewheel(self, event) -> None:
        try:
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        except Exception:
            pass
