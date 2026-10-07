"""无边框窗口（可选）。

Tk 用 overrideredirect(True) 去掉标题栏后，系统提供的拖动和缩放就没了，
所以这里自己实现：拖动标题栏区域移动、贴边 6 像素内拖动缩放、右下角有缩放角。

几处安全性考虑：
* 关闭按钮始终可用；最小化会**先恢复系统边框再 iconify**，保证任务栏里能找回来；
* 缩放有最小尺寸限制，不会把窗口拖成一团；
* 关掉无边框（设置里取消勾选）时把所有绑定解掉，回到标准窗口。

两个"用户实测踩过"的坑（都写在下面的实现里）：
* 拖动把手是几个 Frame / Label（工具条、标题、状态栏）。Tk 的绑定只认**那个控件自己**，
  按在它的子控件（按钮旁边的留白、状态栏文字上）是收不到的 —— 所以判定改成"从按到的
  控件往上找，能找到把手就算"，站在留白/文字上也能拖；
* 事件里的 x/y 是**按到那个控件**的局部坐标，不能拿去和窗口宽高比 —— 以前这么比，
  按在聊天区底部会被判成"贴着下边"，一拖就变成缩放窗口。现在统一换算成窗口坐标。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import List, Sequence, Tuple

EDGE = 6                      # 贴边多少像素内算缩放区
DEFAULT_MIN_SIZE = (420, 300)

# 这些控件自己有"点一下要干的事"（按钮、输入框、聊天区…），按在它们身上不该算拖窗口
_CLICKABLE = (tk.Button, tk.Entry, tk.Text, tk.Canvas, tk.Listbox, tk.Spinbox,
              ttk.Button, ttk.Entry, ttk.Combobox, ttk.Spinbox)

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
        # 只绑在顶层窗口上就够了：Tk 的 bindtags 里每个子控件都带着所属顶层窗口，
        # 所以按在工具条/状态栏的子控件上，这个回调一样会被叫到（再用 _is_drag_zone 判定）。

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
        try:
            self.window.configure(cursor="")
        except Exception:
            pass

    # ------------------------------------------------------------------ 事件
    def _window_coords(self, event) -> Tuple[int, int]:
        """把事件坐标换算成"相对窗口左上角"。

        事件里的 event.x / event.y 是相对**被按到的那个控件**的（子控件上就是子控件
        自己的坐标），拿它和窗口宽高比会得出莫名其妙的结论（实测：按在聊天区底部
        被当成"贴着窗口下边"，一拖就变成缩放）。这里统一减去窗口自己的屏幕位置。
        """
        try:
            left = self.window.winfo_rootx()
            top = self.window.winfo_rooty()
        except Exception:
            return event.x, event.y
        return event.x_root - left, event.y_root - top

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

    def _is_drag_zone(self, widget) -> bool:
        """从"被按到的控件"往上找拖动把手（工具条/标题/状态栏）。

        走到按钮、输入框、聊天区这种"自己有点击行为"的控件就停下（不然点按钮会变成
        拖窗口、点频道小灯会拖着窗口跑）。
        """
        handles = set(self.drag_handles)
        node = widget
        while node is not None and node not in handles:
            if isinstance(node, _CLICKABLE):
                return False
            node = getattr(node, "master", None)
        return node is not None and node in handles

    def _on_motion(self, event) -> None:
        if not self.enabled or self._mode:
            return
        x, y = self._window_coords(event)
        cursor = _CURSORS.get(self._edges_at(x, y), "")
        try:
            self.window.configure(cursor=cursor)
        except Exception:
            pass

    def _on_press(self, event) -> None:
        if not self.enabled or self._mode:
            return
        x, y = self._window_coords(event)
        edge = self._edges_at(x, y)
        if edge:
            self._begin("resize", edge, event)
            return
        if self._is_drag_zone(getattr(event, "widget", None)):
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
