"""区域框选 + 框选结果预览（看看框得准不准）。"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import theme


class RegionPicker:
    """全屏半透明窗口，拖拽选择区域。"""

    def __init__(self, parent: tk.Misc, on_done) -> None:
        self.parent = parent
        self.on_done = on_done
        self.window = None
        self.canvas = None
        self.rect = None
        # 注意：这里不能叫 self.start —— 会覆盖下面的 start() 方法
        self._drag_origin = None

    def start(self) -> None:
        self.window = tk.Toplevel(self.parent)
        self.window.attributes("-fullscreen", True)
        self.window.attributes("-alpha", 0.30)
        self.window.attributes("-topmost", True)
        self.window.configure(bg="black")
        self.window.bind("<Escape>", lambda _event: self._finish(None))

        self.canvas = tk.Canvas(self.window, bg="black", cursor="cross",
                                highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_text(
            self.window.winfo_screenwidth() // 2, 48,
            text="拖拽框选游戏聊天框区域（按 Esc 取消）",
            fill="#ffe066", font=("Microsoft YaHei", 16),
        )

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.window.grab_set()
        self.window.focus_force()

    def _on_press(self, event) -> None:
        self._drag_origin = (event.x_root, event.y_root)
        if self.rect:
            self.canvas.delete(self.rect)
        self.rect = self.canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline="#ff5555", width=2)

    def _on_drag(self, event) -> None:
        if not self.rect or not self._drag_origin:
            return
        x0 = self._drag_origin[0] - self.window.winfo_rootx()
        y0 = self._drag_origin[1] - self.window.winfo_rooty()
        x1 = event.x_root - self.window.winfo_rootx()
        y1 = event.y_root - self.window.winfo_rooty()
        self.canvas.coords(self.rect, x0, y0, x1, y1)

    def _on_release(self, event) -> None:
        if not self._drag_origin:
            self._finish(None)
            return
        x1 = min(self._drag_origin[0], event.x_root)
        y1 = min(self._drag_origin[1], event.y_root)
        x2 = max(self._drag_origin[0], event.x_root)
        y2 = max(self._drag_origin[1], event.y_root)
        if x2 - x1 < 20 or y2 - y1 < 20:
            self._finish(None)
        else:
            self._finish((x1, y1, x2, y2))

    def _finish(self, region) -> None:
        try:
            self.window.grab_release()
            self.window.destroy()
        except Exception:
            pass
        if self.on_done:
            self.on_done(region)


def show_preview(parent: tk.Misc, region, image, lines, on_retry=None, info="") -> None:
    """显示抓到的画面 + 识别出来的行，用来确认框选和 OCR 是否正常。"""
    window = tk.Toplevel(parent)
    window.title("识别测试 / 框选预览")
    window.attributes("-topmost", True)
    window.geometry("760x580")
    theme.prepare_window(window)
    theme.frameless_dialog(window, "识别测试 / 框选预览")

    theme.label(window, "区域：%s" % (region,),
                font=("Microsoft YaHei", 10)).pack(anchor="w", padx=10, pady=(8, 2))
    if info:
        theme.label(window, info, muted=True).pack(anchor="w", padx=10)
    theme.label(window, "画面预览（应该正好是游戏聊天框）", muted=True).pack(
        anchor="w", padx=10)

    photo = None
    if image is not None:
        try:
            from PIL import ImageTk

            preview = image
            max_width = 720
            max_height = 340
            if preview.width > max_width:
                ratio = max_width / float(preview.width)
                preview = preview.resize((max_width, max(1, int(preview.height * ratio))))
            if preview.height > max_height:
                ratio = max_height / float(preview.height)
                preview = preview.resize((max(1, int(preview.width * ratio)), max_height))
            photo = ImageTk.PhotoImage(preview)
            label = tk.Label(window, image=photo, borderwidth=1, relief="solid",
                             bg=theme.PALETTE["bg"])
            label.image = photo          # 防止被回收
            label.pack(padx=10, pady=6)
        except Exception:
            photo = None

    if photo is None:
        theme.label(window, "（无法显示预览图，缺少 Pillow 的 ImageTk）").pack(
            padx=10, pady=6)

    theme.label(window, "OCR 识别到的内容（共 %d 行）" % len(lines), muted=True).pack(
        anchor="w", padx=10)
    box = theme.text_widget(window, height=10, wrap="none")
    box.pack(fill="both", expand=True, padx=10, pady=(2, 8))
    for line in lines:
        box.insert("end", line + "\n")
    box.configure(state="disabled")

    row = ttk.Frame(window)
    row.pack(fill="x", padx=10, pady=(0, 10))

    def retry() -> None:
        window.destroy()
        if on_retry:
            on_retry()

    ttk.Button(row, text="重新框选", command=retry).pack(side="left")
    ttk.Button(row, text="关闭", command=window.destroy).pack(side="right")
