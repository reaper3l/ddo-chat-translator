"""新手教学（首次使用引导）：逐个功能指着界面讲"在哪儿、怎么设置"。

为什么做成这样（用户目标：程序初次使用时逐个说明功能）：
  * **金框 + 说明卡片**：要讲的那个控件用金框圈出来，旁边一张卡片写"这是什么、怎么用"。
    （早先还试过"目标以外压暗"的四块半透明窗：视觉更聚焦，但**会让主窗口拖动变得很卡**
    —— 用户实测反馈"拖动起来延时很高不跟手"，而且 Tk 上"鼠标穿透"与"半透明渲染"
    没法兼得，所以去掉了。）
  * **一次只讲一件事**：每步一句话 + 上一步 / 下一步 / 完成，7 步讲完主要功能。
  * **能代劳就代劳**：要讲设置页就先把设置页打开（设置 → 翻译 / 频道），
    要讲互译就把互译窗口打开并圈出输入框，省得用户自己找。
  * **首次使用自动走一遍**：同意使用须知后 2.5 秒出现；走完或点「跳过」都记进
    `tour_done`，以后不再自动弹（右键菜单 / 设置→关于 里可随时重看）。

三条纪律（界面自检里钉着）：
  * **只画金框、不改设置**（为了指按钮临时展开的工具条，结束会还原）；
  * **不联网、不写学习库**（互译窗口用 `auto_suggest=False` 打开）；
  * 教学里打开的窗口、盖的半透明层，换步/结束/关程序时全部收掉。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import theme

SPOT_KEY = "#0b0c0e"        # 高亮框窗里当作"透明"的键色
SPOT_LINE = "#ffcc4d"       # 高亮框颜色
WRAP = 470                  # 说明文字的换行宽度
LAMP_KEYS = ("F8 开始/停止监听", "F9 剪贴板中英互译", "F10 纠错", "F5 测试识别")


def _widgets(widget, kinds):
    """递归找出某一类控件（找"要指的那个控件"用）。"""
    found = []
    for child in widget.winfo_children():
        if isinstance(child, kinds):
            found.append(child)
        found.extend(_widgets(child, kinds))
    return found


class GuidedTour:
    """分步引导。用法：`GuidedTour(app).start()`（`first_run=True` 表示首次自动弹的）。"""

    def __init__(self, app, first_run: bool = False) -> None:
        self.app = app
        self.first_run = bool(first_run)
        self.index = 0
        self.highlight = None
        self.panel = None
        self._opened = []
        self._title = None
        self._text = None
        self._counter = None
        self._prev = None
        self._next = None
        self.target_rect = None
        self._target_widget = None
        self._configure_bind = None
        self._reposition_job = None
        self._drag_binds = []
        self._dragging = False
        self._toolbar_was_collapsed = None

    # ---------------------------------------------------------------- 步骤
    def _steps(self):
        app = self.app
        return [
            dict(title="① 先框住游戏聊天框，再开监听",
                 text="点工具栏的「⊞ 区域」，在游戏里拖一个框：**框住聊天框就行**，\n"
                      "松手后会自动抓一帧给你确认框得准不准（不准就再框一次）。\n"
                      "之后按 F8（或点左边的 ▶）开始 / 停止监听。",
                 target=lambda: self._show_toolbar_and_find("区域")),
            dict(title="② 填翻译接口（第一次用必须做）",
                 text="「设置 → 翻译」里填上 DeepSeek 的 API Key，点「测试连接」通过就行。\n"
                      "Key 只存在你自己电脑上（data\\config.json），作者那边看不到。\n"
                      "嫌慢/嫌贵可以在同一页把「翻译模式」改成 fast，或者换别的模型。",
                 target=lambda: self._open_settings("翻译", "deepseek_key")),
            dict(title="③ 颜色、字体、描边",
                 text="「设置 → 频道」：每个频道一行 —— 名字、颜色（点「选色」）、显不显示、\n"
                      "小灯里有没有它。频道名要和游戏里一致（英文客户端就写 Party / Guild / Tell）。\n"
                      "「设置 → 外观」：字体、字号、粗细、底色、边框，右边有实时预览。",
                 target=lambda: self._open_settings("频道", "channel_row")),
            dict(title="④ 翻错了就纠错（F10）",
                 text="选中翻错的那一条 → 按 F10 → 把中文改成你要的 → 「保存并生效」。\n"
                      "以后遇到同样的句子直接用它（不花接口钱）。\n"
                      "只想改**一个词**：选中那个英文词和对应的中文，点「加进术语表」——\n"
                      "所有含这个词的句子都会跟着变准。",
                 target=lambda: app.text),
            dict(title="⑤ 不想看谁说话：过滤他",
                 text="对着他说的一句话点右键 →「过滤这个说话人」，以后他的话就不翻译、不显示了\n"
                      "（想过滤自己也一样）。再点一次同一个菜单项就是取消。\n"
                      "也可以到「设置 → 监控 → 过滤的说话人」里一次填好几个名字。",
                 target=lambda: app.text),
            dict(title="⑥ 中文 → 英文（跟队友交流）",
                 text="按 F9 直接翻剪贴板里的中文；或者点「⇄ 互译」打开这个窗口：\n"
                      "输入框里打中文 → 出英文（自动复制到剪贴板），游戏里粘贴就行。\n"
                      "它还会按最近几句聊天给你几句「可以这么回」的中英对照。",
                 target=lambda: self._open_cn2en()),
            dict(title="⑦ 词典与学习：越用越准",
                 text="「▤ 词典」里是你自己的术语表 —— 里面的词不会被模型乱翻；\n"
                      "「✦ 学习」里能看纠错历史、把常出现的生词收进词典。\n"
                      "程序自己也会学：你改过的句子、反复出现的高频说法都会自动记住，\n"
                      "以后这些句子本地直接翻，不花接口钱。",
                 target=lambda: self._show_toolbar_and_find("词典")),
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
        app.set_status("新手教学：跟着圈出来的地方走一遍就会了（不会改你的任何设置）", "info")
        # 窗口被拖动/缩放时，金框和说明卡片要跟着目标走（只挪位置、不重建窗口，
        # 不然拖起来会卡）。用 after 去抖，别在拖动过程中疯狂重画。
        try:
            self._configure_bind = app.root.bind("<Configure>", self._on_root_configure,
                                                 add="+")
        except Exception:                          # noqa: BLE001
            self._configure_bind = None
        # 记下"用户正在拖窗口"：拖动过程中**不要**再去 lift 金框、也不重摆卡片
        # （每次 lift 都会让窗口重新映射一次，拖动就变得很卡 —— 用户反馈"不跟手"）
        for widget in (app.root, getattr(app, "top_frame", None), app.status_bar):
            if widget is None:
                continue
            try:
                press = widget.bind("<ButtonPress-1>", self._on_drag_start, add="+")
                release = widget.bind("<ButtonRelease-1>", self._on_drag_end, add="+")
                self._drag_binds.append((widget, press, release))
            except Exception:                      # noqa: BLE001
                continue
        self._show()

    # ------------------------------------------------- 跟着窗口走（拖动/缩放时）
    def _on_root_configure(self, _event=None) -> None:
        """主窗口被拖动/缩放 → 稍后重新摆一次（去抖：拖动过程中不狂重画）。"""
        if self.panel is None:
            return
        if self._dragging:
            # 拖动期间**什么都不做**：一点点额外工作都会变成可见的顿挫
            # （实测拖动中事件耗时从 2ms 跳到 28ms）。松手后 _on_drag_end 会统一对齐。
            return
        if self._reposition_job is not None:
            try:
                self.app.root.after_cancel(self._reposition_job)
            except Exception:                      # noqa: BLE001
                pass
        try:
            # 拖动中故意等久一点：连续拖动时就**完全不重摆**（重摆 8 个窗口要二三十毫秒，
            # 那一下就会觉得"不跟手"）；停下来或者松手之后再对齐一次就够了。
            self._reposition_job = self.app.root.after(80, self._reposition)
        except Exception:                          # noqa: BLE001
            self._reposition_job = None

    def _reposition(self) -> None:
        """只改几何、不重建窗口 —— 拖动时才跟得住（重建会闪、也会卡）。"""
        self._reposition_job = None
        rect = None
        target = self._target_widget
        if target is not None:
            try:
                target.update_idletasks()
                width, height = target.winfo_width(), target.winfo_height()
                if width > 1 and height > 1:
                    rect = (target.winfo_rootx(), target.winfo_rooty(), width, height)
            except Exception:                      # noqa: BLE001
                rect = None
        if rect is None:
            rect = self.target_rect
        if rect is None:
            return
        self.target_rect = rect
        if self.highlight is not None:
            x, y, width, height = rect
            pad = 6
            try:
                if not self._dragging:
                    self.highlight.lift()
                self.highlight.geometry("%dx%d+%d+%d"
                                        % (width + pad * 2, height + pad * 2,
                                           x - pad, y - pad))
            except Exception:                      # noqa: BLE001
                pass
        self._place_panel(rect)

    def _on_drag_start(self, _event=None) -> None:
        self._dragging = True

    def _on_drag_end(self, _event=None) -> None:
        """松手：拖动结束，重新对齐一次并把金框提回最前面。"""
        self._dragging = False
        self._reposition()
        self._restack()

    def _restack(self) -> None:
        """把金框提到最前面（主窗口自己也是 topmost，不提就会被压在下面）。"""
        if self.highlight is not None:
            try:
                self.highlight.lift()
            except Exception:                      # noqa: BLE001
                pass
        # lift 会把无边框窗口的位置打回 (0,0)，所以紧接着重新摆一次
        if self.target_rect is not None:
            self._reposition()

    def close(self, finished: bool = False) -> None:
        # 自己要收掉时，把主窗口那份引用也清掉 —— 否则"走完自动关闭"之后，
        # 用户再点一次「新手教学」会变成"收掉一个已经关掉的窗口"，什么也不会发生。
        if getattr(self.app, "_tour", None) is self:
            self.app._tour = None
        if self._reposition_job is not None:
            try:
                self.app.root.after_cancel(self._reposition_job)
            except Exception:                      # noqa: BLE001
                pass
            self._reposition_job = None
        if self._configure_bind is not None:
            try:
                self.app.root.unbind("<Configure>", self._configure_bind)
            except Exception:                      # noqa: BLE001
                pass
            self._configure_bind = None
        for widget, press, release in self._drag_binds:
            for sequence, funcid in (("<ButtonPress-1>", press),
                                     ("<ButtonRelease-1>", release)):
                try:
                    widget.unbind(sequence, funcid)
                except Exception:                  # noqa: BLE001
                    pass
        self._drag_binds = []
        self._dragging = False
        self._restore_toolbar()
        self._destroy_highlight()
        self._close_opened()
        if self.panel is not None:
            try:
                self.panel.destroy()
            except Exception:                      # noqa: BLE001
                pass
            self.panel = None
        if self.first_run:                         # 走完 / 跳过都算"看过了"，不再自动弹
            self.app.config["tour_done"] = True
            try:
                from .. import config as config_module

                config_module.save_config(self.app.config)
            except Exception:                      # noqa: BLE001
                pass
        if finished:
            self.app.set_status("新手教学结束 —— 想再看一次：右键 →「新手教学」"
                                "（或 设置 → 关于）", "ok")

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

    def _point_at(self, target) -> None:
        """给要讲的那个控件套一圈金框 + 把说明卡片摆到旁边。

        早先版本还会把"目标以外"压暗（四块半透明黑窗），视觉上更聚焦，
        但那几块盖在屏幕上的窗**会明显拖慢主窗口的拖动**（用户实测"拖动起来延时很高"），
        而且 Tk 上"鼠标穿透"和"半透明渲染"没法兼得。现在**只留金框 + 说明卡片**，
        指引一样清楚，拖动和平时一样跟手。
        """
        self._target_widget = target
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
        self.target_rect = rect
        self._draw_highlight(rect)
        self._place_panel(rect)
        # 说明卡片要压在最上面：先 lift 再重新摆一次位置
        # （无边框窗口 lift 之后位置会被系统打回 (0,0)，实测踩过）
        if self.panel is not None:
            try:
                self.panel.lift()
            except Exception:                      # noqa: BLE001
                pass
            self._place_panel(rect)
            try:
                self.panel.after(40, lambda r=rect: self._place_panel(r))
            except Exception:                      # noqa: BLE001
                pass
        # 金框在"改样式 → 设位置"之后再兜一次：某些系统上仍会被打回 (0,0)
        if self.highlight is not None:
            try:
                self.app.root.after(60, self._reposition)
            except Exception:                      # noqa: BLE001
                pass

    def _draw_highlight(self, rect) -> None:
        self._destroy_highlight()
        if rect is None:
            return
        x, y, width, height = rect
        pad = 6
        try:
            window = tk.Toplevel(self.app.root)
            window.withdraw()
            window.overrideredirect(True)
            window.attributes("-topmost", True)
            window.configure(bg=SPOT_KEY)
            # 金框要"只显示、不吃鼠标"（它盖在目标上，不能挡住用户点那个按钮）；
            # 内部抠透明也要在窗口还没显示的时候设好
            theme.make_click_through(window)
            try:
                # 内部抠成透明，只留一圈金框（和"只透明背景"用的是同一个招）
                window.attributes("-transparentcolor", SPOT_KEY)
            except Exception:                      # noqa: BLE001
                pass
            canvas = tk.Canvas(window, bg=SPOT_KEY, highlightthickness=0)
            canvas.pack(fill="both", expand=True)
            canvas.create_rectangle(3, 3, width + pad * 2 - 3, height + pad * 2 - 3,
                                    outline=SPOT_LINE, width=4)
            window.geometry("%dx%d+%d+%d"
                            % (width + pad * 2, height + pad * 2, x - pad, y - pad))
            window.deiconify()
            window.lift()
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
        head.pack(fill="x", padx=14, pady=(12, 2))
        tk.Label(head, text="新手教学", bg=theme.PALETTE["surface"],
                 fg=theme.PALETTE["muted"], font=("Microsoft YaHei", 10)).pack(side="left")
        self._counter = tk.Label(head, text="", bg=theme.PALETTE["surface"],
                                 fg=theme.PALETTE["accent"],
                                 font=("Microsoft YaHei", 10, "bold"))
        self._counter.pack(side="right")

        self._title = tk.Label(card, text="", bg=theme.PALETTE["surface"],
                               fg=theme.PALETTE["text"], justify="left",
                               anchor="w", font=("Microsoft YaHei", 13, "bold"))
        self._title.pack(fill="x", padx=14, pady=(6, 6))
        self._text = tk.Label(card, text="", bg=theme.PALETTE["surface"],
                              fg=theme.PALETTE["text"], justify="left", anchor="w",
                              wraplength=WRAP, font=("Microsoft YaHei", 11))
        self._text.pack(fill="x", padx=14)

        row = tk.Frame(card, bg=theme.PALETTE["surface"])
        row.pack(fill="x", padx=14, pady=(14, 12))
        self._prev = ttk.Button(row, text="上一步", width=8,
                                command=self._go_prev, state="disabled")
        self._prev.pack(side="left")
        self._next = ttk.Button(row, text="下一步", width=10, style="Accent.TButton",
                                command=self._go_next)
        self._next.pack(side="left", padx=8)
        skip_text = "跳过教学（以后不再自动弹）" if self.first_run else "结束"
        ttk.Button(row, text=skip_text,
                   command=lambda: self.close(finished=True)).pack(side="right")
        self.panel = panel

    def _place_panel(self, rect) -> None:
        """说明卡片摆在目标旁边：优先右边，放不下换左边，绝不超出屏幕。"""
        panel = self.panel
        if panel is None:
            return
        try:
            panel.update_idletasks()
            width = max(340, panel.winfo_reqwidth())
            height = max(180, panel.winfo_reqheight())
            screen_w = panel.winfo_screenwidth()
            screen_h = panel.winfo_screenheight()
            if rect:
                x, y, target_w, _target_h = rect
            else:
                x, y, target_w = 200, 200, 0
            px = x + target_w + 20
            if px + width > screen_w - 8:
                px = x - width - 20
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
        """要指工具栏按钮时先把收起的工具条展开 —— 不然指着一个看不见的按钮，
        用户只会一脸问号（他的设置是"默认收起"）。教学结束时再还原回去。
        """
        app = self.app
        if self._toolbar_was_collapsed is None:
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
        for widget in _widgets(root, (ttk.Entry, tk.Entry, ttk.Combobox, ttk.Spinbox)):
            try:
                if str(widget.cget("textvariable")) == wanted:
                    return widget
            except Exception:                      # noqa: BLE001
                continue
        return None

    # ---------------------------------------------------------------- 收尾
    def _restore_toolbar(self) -> None:
        """把"为了教学展开的工具条"还原成用户原来的样子（不改他的设置）。"""
        collapsed = self._toolbar_was_collapsed
        self._toolbar_was_collapsed = None
        if not collapsed:
            return
        try:
            self.app.config["toolbar_collapsed"] = True
            self.app._apply_toolbar_collapsed(animate=False)
        except Exception:                          # noqa: BLE001
            pass

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
