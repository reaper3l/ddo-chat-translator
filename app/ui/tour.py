"""新手教学（首次使用引导）：逐个功能说明"在哪儿、怎么设置、怎么用"。

**为什么改成"主窗口里翻一页"，不再用浮在主窗口上的小窗**（用户反馈"还是影响拖动，
换一种方式呈现"）：
  * 早先的做法是"金框（独立置顶窗）+ 说明卡片（另一个置顶窗）+ 目标以外压暗"，
    好几个置顶窗压在屏幕上，用户拖窗口时鼠标会落到这些窗上 —— 拖动被抢、卡顿、
    金框还会跟不上（拖动时不重摆就落后，重摆又卡）。试过"鼠标穿透"“拖动中不重摆"
    等一堆补丁，都不干净：只要还有浮窗，就会跟拖动/置顶/合成器纠缠。
  * 现在教学就是**主窗口内部的一页**：占住显示区，显示区先收起来，讲完原样还回去。
    整页都在主窗口里，**没有任何浮窗、没有压暗层、不碰置顶、不碰鼠标**，
    拖窗口、点按钮和平时完全一样。
  * 要指着某个功能时，在**主窗口内部**给那个按钮套一圈金框（一个 Frame + place，
    并且 `lower()` 到目标下面，只露出外面那一圈）—— 不吃鼠标，也不会有第二个窗口。

三条纪律（界面自检里钉着）：
  * **只讲不改**：为了讲工具条按钮临时展开的工具条，结束会还原；改配置一个字都不行；
  * **不联网、不写学习库**：教学自己从不调接口，需要联网的窗口一律由用户点按钮才开；
  * 教学里由用户点开的窗口，换步/结束/关程序时全部收掉。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from tkinter import font as tkfont

from . import theme

SPOT_LINE = "#ffcc4d"        # 高亮圈的颜色
RING = 2                     # 高亮圈比目标大多少（每边）
WRAP_MIN = 180               # 说明文字的最小换行宽度


class GuidedTour:
    """分步引导。用法：`GuidedTour(app).start()`（`first_run=True` 表示首次自动弹的）。"""

    def __init__(self, app, first_run: bool = False) -> None:
        self.app = app
        self.first_run = bool(first_run)
        self.index = 0
        self.panel = None                 # 教学页（主窗口内部的一个 Frame）
        self._title = None
        self._text = None
        self._counter = None
        self._prev = None
        self._next = None
        self._action_button = None
        self._hidden = []                 # 为了教学让位的控件（显示区 / 滚动条）
        self._ring = None                 # 给目标按钮套的那圈金框
        self._ring_job = None
        self._ring_bind = None
        self._target = None
        self._opened = []                 # 用户点开的窗口（换步/结束要一起收掉）
        self._toolbar_was_collapsed = None
        self._last_action = None
        self._raw_text = ""               # 当前这一步的原文（换行是自己算的）
        self._text_job = None
        family = str(getattr(app, "config", {}).get("font_family", "Microsoft YaHei"))
        self._font = tkfont.Font(family=family, size=10)

    # ---------------------------------------------------------------- 步骤
    def _steps(self):
        app = self.app
        return [
            dict(title="① 先框住游戏里的聊天框",
                 text="点顶部的「⊞区域」，到游戏里拖一个框、把聊天框圈进去"
                      "（松手会自动抓一帧给你确认框得准不准）。\n"
                      "之后按 F8，或点左边的「▶」开始 / 停止监听。",
                 target=lambda: self._toolbar_button("区域"),
                 action=("现在就框选", app.select_region)),
            dict(title="② 填翻译接口（第一次用必须做）",
                 text="「设置 → 翻译」里填上 DeepSeek 的 API Key，点「测试连接」通过就行。\n"
                      "Key 只存在你自己电脑上（data\\config.json），作者那边看不到。",
                 target=lambda: self._toolbar_button("设置"),
                 action=("打开设置（翻译）", lambda: self._open_settings("翻译"))),
            dict(title="③ 颜色、字体、描边",
                 text="「设置 → 频道」：每个频道一行 —— 名字、颜色、显不显示、小灯里有没有它。\n"
                      "频道名要和游戏里一致（英文客户端就写 Party / Guild / Tell）。\n"
                      "「设置 → 外观」：字体、字号、粗细、底色、边框，右边实时预览。",
                 target=lambda: self._toolbar_button("设置"),
                 action=("打开设置（频道）", lambda: self._open_settings("频道"))),
            dict(title="④ 翻错了就纠错（F10）",
                 text="在显示区选中翻错的那一条，按 F10（或点「✎纠错」），"
                      "把中文改成你要的，再点「保存并生效」。\n"
                      "以后遇到同样的句子直接用它，不花接口钱。\n"
                      "只想改一个词：选中那个英文词和对应的中文，点「加进术语表」。",
                 target=lambda: self._toolbar_button("纠错")),
            dict(title="⑤ 不想看谁说话：过滤他",
                 text="对着他说的那句话点右键，选「过滤这个说话人」，"
                      "以后他的话就不翻译、不显示了（想过滤自己也一样）。\n"
                      "也可以到「设置 → 监控」里的「过滤的说话人」一次填好几个名字。",
                 target=None),
            dict(title="⑥ 中文 → 英文（跟队友交流）",
                 text="按 F9 直接翻剪贴板里的中文；或者点「⇄ 互译」打开窗口：\n"
                      "输入框里打中文就出英文（自动复制到剪贴板），游戏里 Ctrl+V 粘贴。\n"
                      "它还会按最近的聊天，给你几句「可以这么回」的中英对照。",
                 target=lambda: self._toolbar_button("互译"),
                 action=("打开互译窗口", self._open_cn2en)),
            dict(title="⑦ 词典与学习：越用越准",
                 text="「▤词典」里是你自己的术语表，里面的词不会被模型乱翻；\n"
                      "「✦学习」里能看纠错历史、把常出现的生词收进词典。\n"
                      "程序自己也会学：你改过的句子、反复出现的高频说法都会自动记住，"
                      "以后这些句子本地直接翻，不花接口钱。",
                 target=lambda: self._toolbar_button("词典"),
                 action=("打开词典", app.open_dictionary)),
        ]

    # ---------------------------------------------------------------- 生命周期
    def start(self) -> None:
        app = self.app
        if app.pipeline.running:
            app.set_status("先点左边的 ■ 停止监听，再看新手教学", "warn")
            return
        app.stop_demo()
        self.index = 0
        self._build_panel()
        app.set_status("新手教学：跟着走一遍就会了（不改你的设置）", "info")
        self._show()

    def close(self, finished: bool = False) -> None:
        # 自己要收掉时，把主窗口那份引用也清掉 —— 否则"走完自动关闭"之后，
        # 用户再点一次「新手教学」会变成"收掉一个已经关掉的窗口"，什么也不会发生。
        if getattr(self.app, "_tour", None) is self:
            self.app._tour = None
        self._cancel_ring_job()
        if self._text_job is not None:
            try:
                self.app.root.after_cancel(self._text_job)
            except Exception:                      # noqa: BLE001
                pass
            self._text_job = None
        if self._ring_bind is not None:
            try:
                self.app.root.unbind("<Configure>", self._ring_bind)
            except Exception:                      # noqa: BLE001
                pass
            self._ring_bind = None
        self._clear_ring()
        self._restore_toolbar()
        self._close_opened()
        panel = self.panel
        self.panel = None
        if panel is not None:
            try:
                panel.destroy()
            except Exception:                      # noqa: BLE001
                pass
        self._restore_display()
        self._title = self._text = self._counter = None
        self._prev = self._next = self._action_button = None
        self._target = None
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

    # ---------------------------------------------------------------- 教学页
    def _build_panel(self) -> None:
        """把教学页搭在显示区的位置上（显示区先收起来，讲完原样还回去）。"""
        app = self.app
        parent = getattr(app, "card", None) or app.root
        self._hide_display()
        panel = tk.Frame(parent, bg=theme.PALETTE["surface"])
        panel.pack(fill="both", expand=True, padx=1, pady=1)
        self.panel = panel

        # 排版分三段：说明在上、按钮贴底、中间留白（留白不归任何控件，窗口小也不会挤掉按钮）。
        # pack 的 side="bottom" 是"越先 pack 越贴底"，所以先摆导航行、再摆动作行。
        row = tk.Frame(panel, bg=theme.PALETTE["surface"])
        row.pack(side="bottom", fill="x", padx=12, pady=(6, 10))
        action_row = tk.Frame(panel, bg=theme.PALETTE["surface"])
        action_row.pack(side="bottom", fill="x", padx=12, pady=(2, 0))
        self._action_row = action_row
        self._prev = ttk.Button(row, text="上一步", width=8, style="Compact.TButton",
                                command=self._go_prev, state="disabled")
        self._prev.pack(side="left")
        self._next = ttk.Button(row, text="下一步", width=9, style="CompactAccent.TButton",
                                command=self._go_next)
        self._next.pack(side="left", padx=6)

        head = tk.Frame(panel, bg=theme.PALETTE["surface"])
        head.pack(fill="x", padx=12, pady=(10, 0))
        tk.Label(head, text="新手教学", bg=theme.PALETTE["surface"],
                 fg=theme.PALETTE["muted"], font=("Microsoft YaHei", 9)).pack(side="left")
        self._counter = tk.Label(head, text="", bg=theme.PALETTE["surface"],
                                 fg=theme.PALETTE["accent"],
                                 font=("Microsoft YaHei", 9, "bold"))
        self._counter.pack(side="right")

        self._title = tk.Label(panel, text="", bg=theme.PALETTE["surface"],
                               fg=theme.PALETTE["text"], justify="left", anchor="w",
                               font=("Microsoft YaHei", 11, "bold"))
        self._title.pack(fill="x", padx=12, pady=(4, 4))
        # 说明文字**不加 expand**：只占它需要的高度，多余的空间留在它和按钮之间
        # （加了 expand 就会在正文下面撑出一大块空白，群里截图很难看）。
        # wraplength=0：换行不交给 Label 自己做 —— 它只在空格处断行，中文会被断得
        # 坑坑洼洼（实测「点顶部的「⊞」后面就断了）。换行在 _render_text 里按像素算。
        self._text = tk.Label(panel, text="", bg=theme.PALETTE["surface"],
                              fg=theme.PALETTE["text"], justify="left", anchor="nw",
                              wraplength=0, font=self._font)
        self._text.pack(fill="x", padx=12)

        # 窗口被拖动/缩放时，那圈金框要跟着目标走（只挪自己画的 Frame，很便宜）
        try:
            self._ring_bind = app.root.bind("<Configure>", self._on_configure, add="+")
        except Exception:                          # noqa: BLE001
            self._ring_bind = None
        # 换行宽度跟着"文字自己拿到的宽度"走（窗口拉宽拉窄都要重排）
        try:
            self._text.bind("<Configure>", self._on_text_configure, add="+")
        except Exception:                          # noqa: BLE001
            pass

    def _on_text_configure(self, event=None) -> None:
        """文字控件宽度变了 → 过一会儿（去抖）重新排一次版。"""
        if self._text_job is not None:
            return
        try:
            self._text_job = self.app.root.after(60, self._render_text)
        except Exception:                          # noqa: BLE001
            self._text_job = None

    def _render_text(self) -> None:
        """按控件实际宽度把说明重排一遍（**自己按像素断行**，中文才断得整齐）。"""
        self._text_job = None
        if self.panel is None or self._text is None:
            return
        try:
            width = max(WRAP_MIN, self._text.winfo_width() - 4)
        except Exception:                          # noqa: BLE001
            return
        wrapped = self._wrap_by_pixels(self._raw_text, width)
        try:
            if self._text.cget("text") != wrapped:  # 一样就别再设，免得 Configure 打乒乓
                self._text.configure(text=wrapped)
        except Exception:                          # noqa: BLE001
            pass

    def _wrap_by_pixels(self, text: str, width: int) -> str:
        """把一段文字按像素宽度断行：中文没有空格，交给 Label 的 wraplength 会断得很难看。"""
        lines = []
        for paragraph in str(text).split("\n"):
            line = ""
            for char in paragraph:
                try:
                    too_wide = line and self._font.measure(line + char) > width
                except Exception:                  # noqa: BLE001
                    too_wide = False
                if too_wide:
                    lines.append(line)
                    line = char
                else:
                    line += char
            lines.append(line)
        return "\n".join(lines)

    def _hide_display(self) -> None:
        app = self.app
        self._hidden = []
        for widget in (getattr(app, "scrollbar", None), getattr(app, "text", None)):
            if widget is None:
                continue
            try:
                if widget.winfo_manager():
                    widget.pack_forget()
                    self._hidden.append(widget)
            except Exception:                      # noqa: BLE001
                continue

    def _restore_display(self) -> None:
        """把显示区按原来的顺序放回去（滚动条在右、正文在左撑满）。"""
        app = self.app
        for widget in self._hidden:
            try:
                if widget is getattr(app, "scrollbar", None):
                    widget.pack(side="right", fill="y")
                else:
                    widget.pack(side="left", fill="both", expand=True, padx=1, pady=1)
            except Exception:                      # noqa: BLE001
                pass
        self._hidden = []

    # ---------------------------------------------------------------- 每步渲染
    def _show(self) -> None:
        steps = self._steps()
        if self.index >= len(steps):
            self.close(finished=True)
            return
        if self.panel is None:
            return
        step = steps[self.index]
        self._close_opened()                       # 上一步打开的窗口先收掉，别越开越多
        self._title.configure(text=step["title"])
        self._raw_text = step["text"]
        self._text.configure(text=self._raw_text)
        self._counter.configure(text="第 %d / %d 步" % (self.index + 1, len(steps)))
        self._next.configure(text="完成" if self.index >= len(steps) - 1 else "下一步")
        self._prev.configure(state="normal" if self.index else "disabled")
        self._show_action(step.get("action"))
        self._point_at(step.get("target"))
        self._render_text()                        # 立刻按当前宽度排一次版

    def _show_action(self, action) -> None:
        for child in self._action_row.winfo_children():
            child.destroy()
        self._last_action = action
        if not action:
            return
        label, command = action
        self._action_button = ttk.Button(self._action_row, text="%s →" % label,
                                         style="CompactAccent.TButton",
                                         command=self._run_action)
        self._action_button.pack(side="left")
        _ = command

    def _run_action(self) -> None:
        """用户自己点"打开 xx"才去开窗口 —— 教学自己绝不偷偷开。"""
        action = self._last_action
        if not action:
            return
        try:
            result = action[1]()
        except Exception as exc:                   # noqa: BLE001
            self.app.set_status("打不开：%s" % exc, "warn")
            return
        if result is not None:
            self._opened.append(result)
        self.app.set_status("用完关掉那个窗口，回到主窗口点「下一步」继续", "info")

    # ------------------------------------------------- 指着某个控件（主窗口内部）
    def _toolbar_button(self, label: str):
        """要指工具条上的按钮时，先把收起的工具条展开 —— 不然指着一个看不见的按钮，
        用户只会一脸问号（他的设置是"默认收起"）。教学结束时再还原回去。"""
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

    def _point_at(self, target) -> None:
        self._target = target() if callable(target) else target
        self._draw_ring()

    def _draw_ring(self) -> None:
        """给目标套一圈金框：一个 Frame，放在目标**下面**，只露出外面那一圈。

        放在目标下面有两个好处：绝不抢鼠标（目标照常能点），也不用管置顶/合成器
        （它就是主窗口里的一个普通控件）。窗口移动、缩放、展开工具条都会重画一次。
        """
        self._clear_ring()
        widget = self._target
        if widget is None or self.panel is None:
            return
        try:
            if not widget.winfo_exists() or not widget.winfo_ismapped():
                return
            widget.update_idletasks()
            width, height = widget.winfo_width(), widget.winfo_height()
            if width <= 1 or height <= 1:
                return
            ring = tk.Frame(widget.master, bg=SPOT_LINE, bd=0, highlightthickness=0)
            ring.place(x=widget.winfo_x() - RING, y=widget.winfo_y() - RING,
                       width=width + RING * 2, height=height + RING * 2)
            ring.lower(widget)                     # 压到目标下面：只留外面那一圈
            self._ring = ring
        except Exception:                          # noqa: BLE001
            self._ring = None

    def _clear_ring(self) -> None:
        ring = self._ring
        self._ring = None
        if ring is not None:
            try:
                ring.destroy()
            except Exception:                      # noqa: BLE001
                pass

    def _on_configure(self, _event=None) -> None:
        """窗口在动/在变 → 过一会儿把金框重新对齐一次（去抖，拖动时不狂重画）。"""
        if self.panel is None or self._ring_job is not None:
            return
        try:
            self._ring_job = self.app.root.after(80, self._reposition_ring)
        except Exception:                          # noqa: BLE001
            self._ring_job = None

    def _reposition_ring(self) -> None:
        self._ring_job = None
        if self.panel is not None:
            self._draw_ring()

    def _cancel_ring_job(self) -> None:
        job = self._ring_job
        self._ring_job = None
        if job is not None:
            try:
                self.app.root.after_cancel(job)
            except Exception:                      # noqa: BLE001
                pass

    # ---------------------------------------------------------------- 翻页
    def _go_next(self) -> None:
        self.index += 1
        self._show()

    def _go_prev(self) -> None:
        self.index = max(0, self.index - 1)
        self._show()

    # ---------------------------------------------------------------- 打开目标
    def _open_settings(self, title: str):
        """打开某个设置页（只有用户点「打开设置」才会走到这里）。"""
        from .settings import SettingsDialog

        dialog = SettingsDialog(self.app)
        dialog.open_category(title)
        return dialog.window

    def _open_cn2en(self):
        from .cn2en import CnToEnDialog

        # auto_suggest=False：教学里不许偷偷调接口（推荐回复要靠接口生成）
        dialog = CnToEnDialog(self.app, auto_suggest=False)
        return dialog.window

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

    def _close_opened(self) -> None:
        for window in self._opened:
            try:
                window.destroy()
            except Exception:                      # noqa: BLE001
                pass
        self._opened = []
