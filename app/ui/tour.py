"""新手教学：一步一步指着界面告诉你"这个功能在哪儿、怎么设置"。

和「演示一下」的分工：
  * 「演示一下」= 看我翻得怎么样（一段示例聊天，译文一条条出来）；
  * 「新手教学」= 这些功能在哪儿、怎么设（聚光灯式高亮 + 一句说明 + 上/下一步）。

三条纪律（界面自检里钉着）：
  * **只高亮、不改设置**：绝不替用户改配置；
  * **不写学习库、不调翻译接口** —— 打开中英互译窗口时用 `auto_suggest=False`，
    免得为了教学偷偷花一次接口钱；
  * 教学里打开的窗口（设置页、互译窗口）在换步/结束/关程序时都收掉。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import theme

SPOT_KEY = "#0b0c0e"        # 聚光灯窗里当作"透明"的键色（跟主题色错开）
SPOT_LINE = "#ffcc4d"       # 高亮框颜色（一眼看到指哪儿）
WRAP = 470                  # 说明文字的换行宽度


def _widgets(widget, kinds):
    """递归找出某一类控件（找目标控件用）。"""
    found = []
    for child in widget.winfo_children():
        if isinstance(child, kinds):
            found.append(child)
        found.extend(_widgets(child, kinds))
    return found


class GuidedTour:
    """分步教学窗口。用法：`GuidedTour(app).start()`。"""

    def __init__(self, app) -> None:
        self.app = app
        self.index = 0
        self.highlight = None
        self.panel = None
        self._opened = []
        self._title = None
        self._text = None
        self._counter = None
        self._prev = None
        self._next = None

    # ---------------------------------------------------------------- 步骤
    def _steps(self):
        app = self.app
        return [
            dict(title="① 框选聊天框 → 开始监听",
                 text="点工具栏的「⊞ 区域」框住游戏里的聊天框（框完会自动抓一帧给你确认）；\n"
                      "以后按 F8（或点左边的 ▶）就能开始 / 停止监听。\n"
                      "框得准不准，随时点「◎ 测试」看一眼识别结果。",
                 target=lambda: self._show_toolbar_and_find("区域")),
            dict(title="② 填翻译接口（第一次用必须做）",
                 text="「⚙ 设置 → 翻译」里填上 DeepSeek 的 API Key，点「测试连接」通过就行。\n"
                      "Key 只存在你自己电脑上（data\\config.json），作者那边看不到。\n"
                      "嫌模型慢/贵可以在同一页换「快速」模式或换模型。",
                 target=lambda: self._open_settings("翻译", "deepseek_key")),
            dict(title="③ 颜色 / 字体 / 描边",
                 text="「设置 → 频道」：每个频道一行 —— 名字、颜色（点「选色」）、显不显示、\n"
                      "小灯里有没有它。频道名要和游戏里一致（英文客户端就写 Party / Guild / Tell）。\n"
                      "「设置 → 外观」：字体、字号、粗细、斜体、底色、边框（描边），右边有实时预览。",
                 target=lambda: self._open_settings("频道", "channel_row")),
            dict(title="④ 翻错了就纠错（F10）",
                 text="在主窗口里选中翻错的那一条 → 按 F10 → 把中文改成你要的 → 「保存并生效」。\n"
                      "以后遇到同样的句子直接用它（不花接口钱）。\n"
                      "只想改**一个词**：选中那个英文词和对应的中文，点「加进术语表」——\n"
                      "所有含这个词的句子都会跟着变准。",
                 target=lambda: app.text),
            dict(title="⑤ 不想看某个人说话：过滤他",
                 text="对着他说的一句话点右键 →「过滤这个说话人」，以后他的话就不翻译、不显示了\n"
                      "（想过滤自己也一样）。再点一次同一个菜单项就是取消。\n"
                      "也可以到「设置 → 监控 → 过滤的说话人」里一次填好几个名字。",
                 target=lambda: app.text),
            dict(title="⑥ 中文 → 英文（跟队友交流）",
                 text="按 F9 直接翻剪贴板里的中文；或者点「⇄ 互译」打开这个窗口：\n"
                      "输入框里打中文 → 出英文（自动复制到剪贴板），游戏里粘贴就行。\n"
                      "它还会按最近几句聊天给你几句「可以这么回」的中英对照。",
                 target=lambda: self._open_cn2en()),
        ]

    # ---------------------------------------------------------------- 生命周期
    def start(self) -> None:
        app = self.app
        if app.pipeline.running:
            app.set_status("先点左边的 ■ 停止监听，再看新手教学", "warn")
            return
        app.stop_demo()
        self.index = 0
        # ④⑤ 两步要指着"某条消息"：窗口空着就先放两条示例（只画，不联网、不写学习库）
        if not app.records:
            app.insert_demo_lines(2)
        app.set_status("新手教学：下面这个小窗口会一步一步指着界面说明（不会改你的任何设置）",
                       "info")
        self._show()

    def close(self, finished: bool = False) -> None:
        # 自己要收掉时，把主窗口那份引用也清掉 —— 否则"走完自动化关闭"之后，
        # 用户再点一次「新手教学」会变成"收掉一个已经关掉的窗口"，什么也不会发生。
        if getattr(self.app, "_tour", None) is self:
            self.app._tour = None
        self._restore_toolbar()
        self._destroy_highlight()
        self._close_opened()
        if self.panel is not None:
            try:
                self.panel.destroy()
            except Exception:                      # noqa: BLE001
                pass
            self.panel = None
        if finished:
            self.app.set_status("新手教学结束 —— 想再看一次：右键 →「新手教学」"
                                "（或 设置 → 关于）", "ok")

    def _restore_toolbar(self) -> None:
        """把"为了教学展开的工具条"还原成用户原来的样子（不改他的设置）。"""
        collapsed = getattr(self, "_toolbar_was_collapsed", None)
        if collapsed is None:
            return
        self._toolbar_was_collapsed = None
        if not collapsed:
            return
        try:
            self.app.config["toolbar_collapsed"] = True
            self.app._apply_toolbar_collapsed(animate=False)
        except Exception:                          # noqa: BLE001
            pass

    # ---------------------------------------------------------------- 每步渲染
    def _show(self) -> None:
        steps = self._steps()
        if self.index >= len(steps):
            self.close(finished=True)
            return
        step = steps[self.index]
        self._close_opened()                       # 上一步开的窗口先收掉，别越开越多
        target = None
        try:
            target = step["target"]()
        except Exception:                          # noqa: BLE001
            target = None
        if self.panel is None:
            self._build_panel()
        self._title.configure(text=step["title"])
        self._text.configure(text=step["text"])
        self._counter.configure(text="第 %d / %d 步" % (self.index + 1, len(steps)))
        self._next.configure(text="完成" if self.index >= len(steps) - 1 else "下一步")
        self._prev.configure(state="normal" if self.index else "disabled")
        self._point_at(target)
        try:
            # 注意：**不要调 lift()** —— 实测无边框（overrideredirect）窗口调过 lift() 之后，
            # 位置会被系统打回 (0,0)，说明卡片就跑到屏幕左上角去了。
            # 想让它留在最前面，重新确认一次 topmost 就够了（不会动位置）。
            self.panel.attributes("-topmost", True)
        except Exception:                          # noqa: BLE001
            pass

    def _point_at(self, target) -> None:
        """把高亮框套在目标控件上，并把说明卡片摆到它旁边。"""
        self._destroy_highlight()
        rect = None
        if target is not None:
            try:
                target.update_idletasks()
                width, height = target.winfo_width(), target.winfo_height()
                if width > 1 and height > 1:
                    rect = (target.winfo_rootx(), target.winfo_rooty(), width, height)
            except Exception:                      # noqa: BLE001
                rect = None
        if rect is None:                           # 目标这会儿没显示 → 就框住主窗口
            try:
                rect = (self.app.root.winfo_rootx(), self.app.root.winfo_rooty(),
                        self.app.root.winfo_width(), self.app.root.winfo_height())
            except Exception:                      # noqa: BLE001
                rect = None
        if rect is not None:
            self._draw_highlight(rect)
        self._place_panel(rect)
        # 关键一步：窗口"刚建好那一下"设的位置，系统在上屏时会按回 (0,0)
        # （实测：设完读回来是 +618+40，过一次 update 就变成 +0+0）。
        # 所以空闲时再摆一次 —— 这一次才是真正留下的位置。
        panel = self.panel
        if panel is not None:
            try:
                panel.after(40, lambda r=rect: self._place_panel(r))
            except Exception:                      # noqa: BLE001
                pass

    def _draw_highlight(self, rect) -> None:
        x, y, width, height = rect
        pad = 6
        try:
            window = tk.Toplevel(self.app.root)
            window.overrideredirect(True)
            window.attributes("-topmost", True)
            window.configure(bg=SPOT_KEY)
            try:
                # 内部抠成透明，只留一圈高亮框（和"只透明背景"用的是同一个招）
                window.attributes("-transparentcolor", SPOT_KEY)
            except Exception:                      # noqa: BLE001
                pass
            canvas = tk.Canvas(window, bg=SPOT_KEY, highlightthickness=0)
            canvas.pack(fill="both", expand=True)
            canvas.create_rectangle(2, 2, width + pad * 2 - 4, height + pad * 2 - 4,
                                    outline=SPOT_LINE, width=3)
            window.geometry("%dx%d+%d+%d"
                            % (width + pad * 2, height + pad * 2, x - pad, y - pad))
            self.highlight = window
        except Exception:                          # noqa: BLE001
            self.highlight = None

    def _build_panel(self) -> None:
        panel = tk.Toplevel(self.app.root)
        panel.overrideredirect(True)
        panel.attributes("-topmost", True)
        theme.prepare_window(panel, self.app.config)
        card = tk.Frame(panel, bg=theme.PALETTE["surface"], highlightthickness=2,
                        highlightbackground=SPOT_LINE)
        card.pack(fill="both", expand=True)

        head = tk.Frame(card, bg=theme.PALETTE["surface"])
        head.pack(fill="x", padx=12, pady=(10, 2))
        theme.label(head, "新手教学", muted=True, surface=True).pack(side="left")
        self._counter = theme.label(head, "", muted=True, surface=True)
        self._counter.pack(side="right")

        self._title = tk.Label(card, text="", bg=theme.PALETTE["surface"],
                               fg=theme.PALETTE["text"], justify="left",
                               anchor="w", font=("Microsoft YaHei", 11, "bold"))
        self._title.pack(fill="x", padx=12, pady=(4, 4))
        self._text = tk.Label(card, text="", bg=theme.PALETTE["surface"],
                              fg=theme.PALETTE["text"], justify="left", anchor="w",
                              wraplength=WRAP, font=("Microsoft YaHei", 10))
        self._text.pack(fill="x", padx=12)

        row = tk.Frame(card, bg=theme.PALETTE["surface"])
        row.pack(fill="x", padx=12, pady=(10, 10))
        self._prev = ttk.Button(row, text="上一步", width=8,
                                command=self._go_prev, state="disabled")
        self._prev.pack(side="left")
        self._next = ttk.Button(row, text="下一步", width=10, style="Accent.TButton",
                                command=self._go_next)
        self._next.pack(side="left", padx=8)
        ttk.Button(row, text="结束", width=6,
                   command=lambda: self.close(finished=True)).pack(side="right")
        self.panel = panel

    def _place_panel(self, rect) -> None:
        """说明卡片摆在目标旁边：优先右边，放不下换左边/下边，绝不超出屏幕。"""
        panel = self.panel
        if panel is None:
            return
        try:
            panel.update_idletasks()
            width = max(300, panel.winfo_reqwidth())
            height = max(160, panel.winfo_reqheight())
            screen_w = panel.winfo_screenwidth()
            screen_h = panel.winfo_screenheight()
            if rect:
                x, y, target_w, _target_h = rect
            else:
                x, y, target_w = 200, 200, 0
            px = x + target_w + 18
            if px + width > screen_w - 8:
                px = x - width - 18
            px = max(8, min(px, screen_w - width - 8))
            py = max(8, min(y, screen_h - height - 8))
            panel.geometry("%dx%d+%d+%d" % (width, height, px, py))
        except Exception:                          # noqa: BLE001
            pass

    # ---------------------------------------------------------------- 动作
    def _go_next(self) -> None:
        self.index += 1
        self._show()

    def _go_prev(self) -> None:
        self.index = max(0, self.index - 1)
        self._show()

    # ---------------------------------------------------------------- 打开目标
    def _open_settings(self, title: str, highlight: str = ""):
        """打开某个设置页；尽量返回"要指给用户看的那个控件"。"""
        from .settings import SettingsDialog

        dialog = SettingsDialog(self.app)
        page = dialog.open_category(title)
        self._opened.extend([dialog.window, page])
        page.update_idletasks()
        if highlight == "deepseek_key":
            found = self._find_by_var(page, dialog.vars.get("deepseek_key"))
            return found or page
        if highlight == "channel_row":
            rows = getattr(dialog, "channel_rows", None) or []
            if rows:
                return rows[0].get("frame") or page
        return page

    def _show_toolbar_and_find(self, label: str):
        """教学要指工具栏按钮时，先把收起的工具条展开 —— 不然指着一个看不见的按钮，
        用户只会一脸问号（他的设置是"默认收起"）。教学结束时再还原回去。
        """
        app = self.app
        if getattr(self, "_toolbar_was_collapsed", None) is None:
            self._toolbar_was_collapsed = bool(app.config.get("toolbar_collapsed", True))
        if self._toolbar_was_collapsed:
            try:
                app.config["toolbar_collapsed"] = False
                app._apply_toolbar_collapsed(animate=False)
                app.root.update_idletasks()
            except Exception:                      # noqa: BLE001
                pass
        return (getattr(app, "_action_buttons", None) or {}).get(label)

    def _open_cn2en(self):
        from .cn2en import CnToEnDialog

        # auto_suggest=False：教学里不许偷偷调接口（推荐回复要靠接口生成）
        dialog = CnToEnDialog(self.app, auto_suggest=False)
        self._opened.append(dialog.window)
        dialog.window.update_idletasks()
        return dialog.input

    @staticmethod
    def _find_by_var(root, record):
        """按 textvariable 找到那个输入框（比如设置页里的 API Key 输入框）。"""
        try:
            wanted = str(record[1])
        except Exception:                          # noqa: BLE001
            return None
        for widget in _widgets(root, (ttk.Entry, tk.Entry, ttk.Combobox,
                                      ttk.Spinbox)):
            try:
                if str(widget.cget("textvariable")) == wanted:
                    return widget
            except Exception:                      # noqa: BLE001
                continue
        return None

    # ---------------------------------------------------------------- 收尾
    def _destroy_highlight(self) -> None:
        if self.highlight is not None:
            try:
                self.highlight.destroy()
            except Exception:                      # noqa: BLE001
                pass
            self.highlight = None

    def _close_opened(self) -> None:
        for window in self._opened:
            try:
                window.destroy()
            except Exception:                      # noqa: BLE001
                pass
        self._opened = []
