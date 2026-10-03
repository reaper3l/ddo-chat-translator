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


class ButtonFlow(ttk.Frame):
    """一排按钮：窗口被拉窄时自动折到下一行，不会被窗口边缘裁掉。

    Tk 没有现成的流式布局：`pack(side="left")` 排出来的一排按钮，窗口一窄就会被
    窗口右边缘裁掉 —— 用户实测（设置 → 关于 那一排）"按钮被遮挡、点不到"。
    这里改成 grid，并在窗口尺寸变化时重新算一行放得下几个。

    用法：按钮必须是这个 Frame 的子控件，然后 `flow.add(button)`（不要再自己 pack）。
    """

    def __init__(self, parent: tk.Misc, gap: int = 6,
                 row_pady: int = 2, **kwargs) -> None:
        super().__init__(parent, **kwargs)
        self.gap = max(0, int(gap))
        self.row_pady = max(0, int(row_pady))
        self._buttons: list = []
        self._columns = 0
        self._busy = False
        self.bind("<Configure>", self._on_configure)

    def add(self, button: tk.Misc) -> tk.Misc:
        self._buttons.append(button)
        self._layout(force=True)
        return button

    def _on_configure(self, _event=None) -> None:
        self._layout()

    def _layout(self, force: bool = False) -> None:
        if self._busy or not self._buttons:
            return
        width = self.winfo_width()
        if width <= 1:                       # 还没布局完，等下一次 Configure
            return
        self._busy = True
        try:
            columns = self._best_columns(width)
            if not force and columns == self._columns:
                return
            self._columns = columns
            for index, button in enumerate(self._buttons):
                button.grid_configure(row=index // columns,
                                      column=index % columns, sticky="w",
                                      padx=(0, self.gap), pady=(0, self.row_pady))
        finally:
            self._busy = False

    def _best_columns(self, width: int) -> int:
        """这一行能放几个按钮：从"全放一行"开始往下试，放得下就用。"""
        widths = [max(1, button.winfo_reqwidth()) for button in self._buttons]
        count = len(widths)
        for columns in range(count, 0, -1):
            fits = True
            for start in range(0, count, columns):
                chunk = widths[start:start + columns]
                used = sum(chunk) + self.gap * (len(chunk) - 1)
                if used > width:
                    fits = False
                    break
            if fits:
                return columns
        return 1
