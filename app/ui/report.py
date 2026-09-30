"""「反馈问题」窗口：填一句话 → 一键生成反馈包（自动带日志、配置、翻译记录）。

为什么这么设计：让用户手动贴日志不现实，而 issue 里没有日志又没法定位。
这里把该带的都自动收好、**把 API Key 这类敏感信息抹掉**，用户只要：
① 写一句问题 → ② 点「生成反馈包」→ ③ 去反馈页把压缩包拖进去（路径已复制到剪贴板）。
"""
from __future__ import annotations

import os
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from .. import diagnose, paths, update as update_module
from . import theme


class BugReportDialog:
    def __init__(self, app) -> None:
        self.app = app
        self.bundle: Path | None = None
        self.window = tk.Toplevel(app.root)
        self.window.title("反馈问题")
        self.window.transient(app.root)
        try:
            self.window.attributes("-topmost", True)
        except Exception:
            pass
        theme.prepare_window(self.window, app.config)
        theme.frameless_dialog(self.window, "反馈问题", autofocus=True,
                               size=(600, 480))
        self._build()

    def _build(self) -> None:
        theme.label(self.window, "遇到的问题（一句话也行：当时在做什么、看到什么、期望什么）",
                    anchor="w").pack(fill="x", padx=12, pady=(10, 2))
        self.problem = theme.text_widget(
            self.window, height=5, wrap="word",
            font=(self.app.config.get("font_family", "Microsoft YaHei"), 11))
        self.problem.pack(fill="x", padx=12)
        theme.set_dialog_input(self.window, self.problem)
        self.problem.focus_set()

        box = ttk.LabelFrame(self.window, text="反馈包里会带上这些")
        box.pack(fill="x", padx=12, pady=(10, 4))
        theme.label(box, "自动收集：程序版本 / 运行环境 / 配置（**API Key 已自动隐藏**）/ "
                         "最近日志 / 最近的翻译记录 / 最近一次识别到的原文行",
                    muted=True, anchor="w", justify="left", wraplength=520).pack(
            fill="x", padx=8, pady=(4, 2))
        self.with_screen = tk.BooleanVar(value=True)
        ttk.Checkbutton(box, text="附上最近一次识别到的画面截图（只有你框选的那个区域）",
                        variable=self.with_screen).pack(anchor="w", padx=8, pady=2)
        self.with_memory = tk.BooleanVar(value=True)
        ttk.Checkbutton(box, text="附上我的词典 / 学习库文件（memory.json）",
                        variable=self.with_memory).pack(anchor="w", padx=8, pady=(2, 6))

        row = ttk.Frame(self.window)
        row.pack(fill="x", padx=12, pady=(4, 2))
        ttk.Button(row, text="生成反馈包", style="Accent.TButton",
                   command=self.generate).pack(side="left")
        ttk.Button(row, text="复制报告文本", command=self.copy_text).pack(
            side="left", padx=6)
        ttk.Button(row, text="打开发反馈页", command=self.open_issue).pack(side="left")
        ttk.Button(row, text="关闭", command=self.window.destroy).pack(side="right")

        self.status = tk.StringVar(value="填一句话，然后点「生成反馈包」。")
        theme.label(self.window, "", muted=True, anchor="w").pack(
            fill="x", padx=12, pady=(0, 0))
        ttk.Label(self.window, textvariable=self.status,
                  style="Status.TLabel", wraplength=560,
                  justify="left").pack(fill="x", padx=12, pady=(4, 12))

    # ------------------------------------------------------------------ 操作
    def _report_text(self) -> str:
        app = self.app
        records = list(getattr(app, "records", []) or [])
        last_lines = []
        try:
            last_lines = list(getattr(app.pipeline, "_last_lines", []) or [])
            last_lines = [row[2] if isinstance(row, tuple) else row for row in last_lines]
        except Exception:
            last_lines = []
        return diagnose.build_report(
            problem=self.problem.get("1.0", "end"),
            config=app.config, records=records, last_lines=last_lines,
            memory=app.memory)

    def generate(self) -> None:
        report = self._report_text()
        extras = {}
        if self.with_memory.get():
            try:
                extras["memory.json"] = paths.MEMORY_PATH.read_text(
                    encoding="utf-8", errors="replace")
            except Exception:
                pass
        if self.with_screen.get():
            image = getattr(self.app.pipeline, "last_frame_image", None)
            if image is not None:
                try:
                    import io

                    buffer = io.BytesIO()
                    image.save(buffer, format="PNG")
                    extras["最近一次画面.png"] = buffer.getvalue()
                except Exception:
                    pass
        folder = paths.DATA_DIR / "反馈"
        try:
            self.bundle = diagnose.write_bundle(folder, report, extras)
        except Exception as exc:
            messagebox.showwarning("生成失败", str(exc), parent=self.window)
            return
        try:
            self.app.root.clipboard_clear()
            self.app.root.clipboard_append(str(self.bundle))
        except Exception:
            pass
        lines = [
            "已生成：%s" % self.bundle,
            "路径已复制到剪贴板。接着点「打开发反馈页」，把这句话贴上去、"
            "再把上面这个压缩包拖进附件里就行。",
        ]
        if self.with_screen.get() and "最近一次画面.png" not in extras:
            lines.append("（这次还没有识别过画面，所以没带截图）")
        self.status.set("\n".join(lines))
        try:
            os.startfile(str(folder))            # 直接把文件夹打开，方便拖拽
        except Exception:
            pass
        self.app.set_status("已生成反馈包：%s" % self.bundle.name, "ok")

    def copy_text(self) -> None:
        text = self._report_text()
        try:
            self.app.root.clipboard_clear()
            self.app.root.clipboard_append(text)
        except Exception:
            pass
        self.status.set("报告文本已复制到剪贴板（可以直接粘进反馈贴；内容里的 API Key "
                        "已经自动隐藏）")

    def open_issue(self) -> None:
        url = diagnose.issue_url()
        if not update_module.open_page(url):
            messagebox.showwarning("打不开浏览器", "请手动访问：%s" % url,
                                   parent=self.window)
