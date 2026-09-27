"""无边框窗口（可选）。

Tk 用 overrideredirect(True) 去掉标题栏后，系统提供的拖动和缩放就没了，
所以这里自己实现：拖动标题栏区域移动、贴边 6 像素内拖动缩放、右下角有缩放角。

几处安全性考虑：
* 关闭按钮始终可用；最小化会**先恢复系统边框再 iconify**，保证任务栏里能找回来；
* 缩放有最小尺寸限制，不会把窗口拖成一团；
* 关掉无边框（设置里取消勾选）时把所有绑定解掉，回到标准窗口。
"""
from __future__ import annotations

import tkinter as tk
from typing import List, Sequence, Tuple

EDGE = 6                      # 贴边多少像素内算缩放区
DEFAULT_MIN_SIZE = (420, 300)

_CURSORS = {
    "n": "size_ns", "s": "size_ns", "e": "size_we", "w": "size_we",
    "ne": "size_ne_sw", "sw": "size_ne_sw", "nw": "size_nw_se", "se": "size_nw_se",
}


class FramelessWindow:
    def __init__(self, window: tk.Tk, drag_handles: Sequence[tk.Misc] = (),
                 min_size: Tuple[int, int] = DEFAULT_MIN_SIZE) -> None:
        self.window = window
        self.drag_handles: List[tk.Misc] = list(drag_handles)
        self.min_w, self.min_h = min_size
        self.enabled = False
        self._mode = ""              # "" | "move" | "resize"
        self._edge = ""
        self._start = (0, 0)
        self._geometry = (0, 0, 0, 0)

    # ------------------------------------------------------------------ 开关
    def set_enabled(self, enabled: bool) -> None:
        if enabled and not self.enabled:
            self.enable()
        elif not enabled and self.enabled:
            self.disable()

    def enable(self) -> None:
        if self.enabled:
            return
        self.enabled = True
        try:
            self.window.overrideredirect(True)
        except Exception:
            pass
        self.window.bind("<Motion>", self._on_motion, add="+")
        self.window.bind("<ButtonPress-1>", self._on_press, add="+")
        self.window.bind("<B1-Motion>", self._on_drag, add="+")
        self.window.bind("<ButtonRelease-1>", self._on_release, add="+")
        self.window.bind("<Map>", self._on_map, add="+")
        for handle in self.drag_handles:
            handle.bind("<ButtonPress-1>", self._on_handle_press, add="+")
            handle.bind("<B1-Motion>", self._on_drag, add="+")
            handle.bind("<ButtonRelease-1>", self._on_release, add="+")

    def disable(self) -> None:
        if not self.enabled:
            return
        self.enabled = False
        self._mode = ""
        try:
            self.window.overrideredirect(False)
        except Exception:
            pass
        for sequence, callback in (("<Motion>", self._on_motion),
                                   ("<ButtonPress-1>", self._on_press),
                                   ("<B1-Motion>", self._on_drag),
                                   ("<ButtonRelease-1>", self._on_release),
                                   ("<Map>", self._on_map)):
            try:
                self.window.unbind(sequence, callback)
            except Exception:
                pass
        for handle in self.drag_handles:
            for sequence, callback in (("<ButtonPress-1>", self._on_handle_press),
                                       ("<B1-Motion>", self._on_drag),
                                       ("<ButtonRelease-1>", self._on_release)):
                try:
                    handle.unbind(sequence, callback)
                except Exception:
                    pass
        try:
            self.window.configure(cursor="")
        except Exception:
            pass

    # ------------------------------------------------------------------ 事件
    def _edges_at(self, x: int, y: int) -> str:
        try:
            width = self.window.winfo_width()
            height = self.window.winfo_height()
        except Exception:
            return ""
        edge = ""
        if x <= EDGE:
            edge += "w"
        elif x >= width - EDGE:
            edge += "e"
        if y <= EDGE:
            edge += "n"
        elif y >= height - EDGE:
            edge += "s"
        return edge

    def _on_motion(self, event) -> None:
        if not self.enabled or self._mode:
            return
        cursor = _CURSORS.get(self._edges_at(event.x, event.y), "")
        try:
            self.window.configure(cursor=cursor)
        except Exception:
            pass

    def _on_press(self, event) -> None:
        if not self.enabled:
            return
        edge = self._edges_at(event.x, event.y)
        if not edge:
            return
        self._begin("resize", edge, event)

    def _on_handle_press(self, event) -> None:
        """拖动标题栏区域移动窗口（按钮上的点击不会触发这里）。"""
        if not self.enabled:
            return
        self._begin("move", "", event)

    def start_resize(self, edge: str, event) -> None:
        """给右下角的缩放角用。"""
        if self.enabled:
            self._begin("resize", edge, event)

    def drag(self, event) -> None:
        """给缩放角绑 <B1-Motion> 用（公开包装，避免外部调用私有方法）。"""
        self._on_drag(event)

    def release(self, event=None) -> None:
        self._on_release(event)

    def _begin(self, mode: str, edge: str, event) -> None:
        self._mode = mode
        self._edge = edge
        self._start = (event.x_root, event.y_root)
        try:
            self._geometry = (self.window.winfo_x(), self.window.winfo_y(),
                              self.window.winfo_width(), self.window.winfo_height())
        except Exception:
            self._geometry = (0, 0, self.min_w, self.min_h)

    def _on_drag(self, event) -> None:
        if not self.enabled or not self._mode:
            return
        dx = event.x_root - self._start[0]
        dy = event.y_root - self._start[1]
        x, y, width, height = self._geometry
        try:
            if self._mode == "move":
                self.window.geometry("+%d+%d" % (x + dx, y + dy))
                return
            new_x, new_y, new_w, new_h = x, y, width, height
            if "e" in self._edge:
                new_w = max(self.min_w, width + dx)
            if "s" in self._edge:
                new_h = max(self.min_h, height + dy)
            if "w" in self._edge:
                new_w = max(self.min_w, width - dx)
                new_x = x + (width - new_w)
            if "n" in self._edge:
                new_h = max(self.min_h, height - dy)
                new_y = y + (height - new_h)
            self.window.geometry("%dx%d+%d+%d" % (new_w, new_h, new_x, new_y))
        except Exception:
            pass

    def _on_release(self, _event=None) -> None:
        self._mode = ""
        self._edge = ""

    # ------------------------------------------------------------ 最小化
    def minimize(self) -> None:
        """安全最小化：先恢复系统边框，保证任务栏里能找回来。"""
        if not self.enabled:
            try:
                self.window.iconify()
            except Exception:
                pass
            return
        try:
            self.window.overrideredirect(False)
        except Exception:
            pass
        try:
            self.window.iconify()
        except Exception:
            pass

    def _on_map(self, _event=None) -> None:
        """从最小化恢复时重新变回无边框。"""
        if not self.enabled:
            return
        try:
            self.window.after(10, lambda: self.window.overrideredirect(True))
        except Exception:
            pass
