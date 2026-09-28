"""使用须知窗口。

两种用法：

* 首次启动 / 条款改版后 —— **必须**点「我已阅读并同意」才能继续；点「不同意，退出」
  或者直接关窗口 = 退出程序（on_result(False)）。
* 设置 → 关于 →「使用须知」—— 只看（readonly=True），给一个「关闭」。

窗口用 grab_set 做成模态：主窗口在它关掉之前点不动，避免"没看就接着用"。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from .. import config as config_module
from .. import disclaimer
from . import theme


class AgreementDialog:
    def __init__(self, app, on_result: Optional[Callable[[bool], None]] = None,
                 readonly: bool = False) -> None:
        self.app = app
        self.on_result = on_result
        self.readonly = readonly
        self.done = False

        self.window = tk.Toplevel(app.root)
        self.window.title(disclaimer.DISCLAIMER_TITLE)
        self.window.transient(app.root)
        theme.prepare_window(self.window, app.config)
        theme.frameless_dialog(self.window, disclaimer.DISCLAIMER_TITLE,
                               on_close=self.decline,
                               size=(660, 560))
        self._build()
        try:
            self.window.grab_set()           # 模态：先看完再操作主窗口
            self.window.focus_force()
        except Exception:
            pass
        try:
            self.window.bind("<Escape>", lambda _event: self.decline())
        except Exception:
            pass

    # ------------------------------------------------------------------ 界面
    def _build(self) -> None:
        holder = ttk.Frame(self.window)
        holder.pack(fill="both", expand=True, padx=14, pady=(10, 6))
        box = theme.text_widget(holder, height=18, wrap="word",
                                font=(self.app.config.get("font_family", "Microsoft YaHei"),
                                      self.app.config.get("ui_font_size", 11)),
                                padx=8, pady=6)
        scroll = ttk.Scrollbar(holder, orient="vertical", command=box.yview)
        box.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        box.pack(side="left", fill="both", expand=True)
        box.insert("1.0", disclaimer.text())
        box.configure(state="disabled")      # 只读，但还能选中/滚动

        row = ttk.Frame(self.window)
        row.pack(fill="x", padx=14, pady=(0, 12))
        if self.readonly:
            ttk.Button(row, text="关闭", command=self.close).pack(side="right")
        else:
            ttk.Button(row, text="我已阅读并同意", style="Accent.TButton",
                       command=self.agree).pack(side="left")
            ttk.Button(row, text="不同意，退出", command=self.decline).pack(
                side="right")
            tip = theme.label(row, "同意后会记住，下次不再打扰；条款更新会再问一次",
                              muted=True)
            tip.pack(side="left", padx=10)

    # ------------------------------------------------------------------ 动作
    def agree(self) -> None:
        """记住"用户同意了这一版条款"，然后回调 True。"""
        stamp = disclaimer.accept(self.app.config)
        disclaimer.log_acceptance(stamp)          # 日志里也留一条（含时间）
        try:
            config_module.save_config(self.app.config)
        except Exception:
            pass
        self.app.set_status("已同意使用须知（%s）" % stamp)
        self._finish(True)

    def decline(self) -> None:
        """不同意 / 直接关窗口：在必须同意的场景下等于退出程序。"""
        if not self.readonly:
            disclaimer.log_decline()              # 留痕：没同意就退了，程序什么都没做
        self._finish(False)

    def close(self) -> None:
        """只看条款时用它关窗口（不表达"同意/不同意"）。"""
        self._finish(False)

    def _finish(self, accepted: bool) -> None:
        if self.done:
            return
        self.done = True
        try:
            self.window.grab_release()
        except Exception:
            pass
        try:
            self.window.destroy()
        except Exception:
            pass
        if self.on_result is not None:
            self.on_result(accepted)
