"""「挖掘高频短语」窗口：把聊天里反复出现的词组收进术语表，减少接口调用。

流程（只有中间那一步花钱，而且是**一次请求问完几十个**）：
  1. 本地统计：从本机已有的翻译记录里找出反复出现的词组（免费、瞬间完成）；
  2. 配中文：把这一批候选**一次**发给模型（DeepSeek），拿回「序号. 中文」；
  3. 你扫一眼勾选（或点「全部采纳」），写进术语表 —— 以后包含这些词组的句子，
     能整句由术语拼出来时就不用调接口了，其它句子也保证译法一致。

为什么默认要人点一下：**术语是影响所有句子**的东西，自动入库万一收错了会到处生效。
想完全自动，勾上窗口里的「以后自动采纳高置信度的」（见 main_window 的定时任务）。
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from .. import config as config_module
from .. import phrases as phrases_module
from . import theme

CHECKED = "☑"
UNCHECKED = "☐"


class PhraseMiningDialog:
    def __init__(self, app) -> None:
        self.app = app
        self.candidates: list = []
        self.queue: "queue.Queue[tuple]" = queue.Queue()
        self.window = tk.Toplevel(app.root)
        self.window.title("挖掘高频短语")
        self.window.transient(app.root)
        theme.prepare_window(self.window, app.config)
        theme.frameless_dialog(self.window, "挖掘高频短语（减少接口调用）",
                               size=(760, 520))
        self._build()
        self.mine_now()
        self._poll()

    # ------------------------------------------------------------------ 构建
    def _build(self) -> None:
        head = ttk.Frame(self.window)
        head.pack(fill="x", padx=12, pady=(10, 4))
        theme.label(
            head,
            "把聊天里反复出现的词组收进术语表：\n"
            "· 短句正好就是这个词组时（omw、need heals…），程序直接拼出中文，不用调接口；\n"
            "· 其它句子也用同一个译法，读起来更稳。\n"
            "第 1 步本地统计不花钱；第 2 步把这批候选一次问完（≈ 一条消息的钱）。",
            muted=True, justify="left").pack(anchor="w")

        self.tree = ttk.Treeview(self.window, columns=("on", "phrase", "count", "zh"),
                                 show="headings", height=12, selectmode="browse")
        for column, title, width, anchor in (
                ("on", "采纳", 46, "center"), ("phrase", "英文词组", 260, "w"),
                ("count", "出现句数", 80, "center"), ("zh", "建议中文", 300, "w")):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, anchor=anchor)
        self.tree.pack(fill="both", expand=True, padx=12, pady=(6, 0))
        self.tree.bind("<Button-1>", self._maybe_toggle)

        row = ttk.Frame(self.window)
        row.pack(fill="x", padx=12, pady=(8, 2))
        ttk.Button(row, text="1) 重新统计", command=self.mine_now).pack(side="left")
        ttk.Button(row, text="2) 让模型配中文（1 次请求）",
                   command=self.translate_now).pack(side="left", padx=6)
        ttk.Button(row, text="3) 采纳勾选的", style="Accent.TButton",
                   command=self.accept).pack(side="left")
        ttk.Button(row, text="高置信度全选",
                   command=self.check_confident).pack(side="left", padx=6)

        self.auto_var = tk.BooleanVar(
            value=bool(self.app.config.get("phrase_auto_enabled", False)))
        ttk.Checkbutton(
            self.window,
            text="以后自动采纳高置信度的（出现 ≥%d 句，程序自己完成，不用我点）"
                 % int(self.app.config.get("phrase_auto_min_count", 5) or 5),
            variable=self.auto_var, command=self._save_auto).pack(
            anchor="w", padx=12, pady=(4, 0))

        bottom = ttk.Frame(self.window)
        bottom.pack(fill="x", padx=12, pady=(4, 10))
        self.status = theme.label(bottom, "", muted=True)
        self.status.pack(side="left")
        ttk.Button(bottom, text="关闭", command=self.window.destroy).pack(side="right")

    # ------------------------------------------------------------------ 动作
    def mine_now(self) -> None:
        """第 1 步：本地统计（不联网、不花钱）。"""
        pairs = self.app.pipeline.cached_pairs() + phrases_module.pairs_from_memory(
            self.app.memory)
        known = list(self.app.glossary.terms().keys())
        self.candidates = phrases_module.mine(pairs, known=known)
        self._fill()
        if self.candidates:
            self.status.configure(
                text="找到 %d 个候选词组（共 %d 句翻译记录）—— 下一步让模型配中文"
                     % (len(self.candidates), len(pairs)))
        else:
            self.status.configure(
                text="暂时没有新的高频词组（多翻一会儿、或先积累一些翻译记录再来）")

    def _fill(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        for index, cand in enumerate(self.candidates):
            self.tree.insert("", "end", iid=str(index),
                             values=(UNCHECKED, cand["phrase"], cand["count"],
                                     cand.get("zh", "")))

    def check_confident(self) -> None:
        """把"出现次数多"的候选先勾上（省得一个个点）。"""
        threshold = int(self.app.config.get("phrase_auto_min_count", 5) or 5)
        for index, cand in enumerate(self.candidates):
            checked = CHECKED if cand["count"] >= threshold else UNCHECKED
            self.tree.set(str(index), "on", checked)

    def _maybe_toggle(self, event) -> None:
        """点「采纳」那一列就切换勾选。"""
        if self.tree.identify_column(event.x) != "#1":
            return
        row = self.tree.identify_row(event.y)
        if not row:
            return
        current = self.tree.set(row, "on")
        self.tree.set(row, "on", UNCHECKED if current == CHECKED else CHECKED)

    def translate_now(self) -> None:
        """第 2 步：把候选**一次**发给模型配中文（后台线程，UI 不卡）。"""
        if not self.candidates:
            messagebox.showinfo("提示", "先点「1) 重新统计」找出候选词组", parent=self.window)
            return
        engine = self.app.pipeline.engine
        if not getattr(engine, "available", lambda: False)():
            messagebox.showwarning(
                "当前引擎用不了",
                "这一步需要一个可用的翻译引擎（比如填好 DeepSeek API Key）。\n"
                "也可以先自己填中文：直接双击「建议中文」那一列改。",
                parent=self.window)
            return
        wanted = [cand["phrase"] for cand in self.candidates]
        messages = phrases_module.build_messages(wanted)
        timeout = float(self.app.config.get("timeout_seconds", 20))
        self.status.configure(text="正在让模型给 %d 个词组配中文…" % len(wanted))

        def work() -> None:
            try:
                result = engine.translate("\n".join(wanted), messages, timeout=timeout)
                self.queue.put(("done", result))
            except Exception as exc:                  # noqa: BLE001
                self.queue.put(("error", str(exc)))

        threading.Thread(target=work, name="phrase-gloss", daemon=True).start()

    def _apply_gloss(self, text: str) -> int:
        glossary = phrases_module.parse_reply(
            text, [cand["phrase"] for cand in self.candidates])
        filled = 0
        for index, cand in enumerate(self.candidates):
            zh = glossary.get(cand["phrase"])
            if not zh:
                continue
            cand["zh"] = zh
            filled += 1
            self.tree.set(str(index), "zh", zh)
        return filled

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "done":
                    text = getattr(payload, "text", "") or ""
                    if not getattr(payload, "ok", False):
                        self.status.configure(text="配中文失败：%s"
                                                   % (getattr(payload, "error", "") or "未知原因"))
                    else:
                        filled = self._apply_gloss(text)
                        self.status.configure(
                            text="已配好 %d 个（可双击「建议中文」自己改）→ 勾选后点「采纳勾选的」"
                                 % filled)
                else:
                    self.status.configure(text="出错了：%s" % payload)
        except queue.Empty:
            pass
        try:
            self.window.after(150, self._poll)
        except tk.TclError:
            pass

    def accept(self) -> None:
        """第 3 步：把勾选且有中文的词组写进术语表。"""
        added, skipped = [], 0
        for index, cand in enumerate(self.candidates):
            if self.tree.set(str(index), "on") != CHECKED:
                continue
            zh = str(cand.get("zh") or "").strip()
            if not zh:
                skipped += 1
                continue
            self.app.memory.set_term(cand["phrase"], zh, source="phrase")
            added.append(cand["phrase"])
        if not added:
            messagebox.showinfo(
                "还没有可采纳的",
                "勾选的行还要有「建议中文」才能采纳。\n"
                "先点「2) 让模型配中文」，或者双击那一列自己填。",
                parent=self.window)
            return
        self.app.memory.flush(force=True)
        self.app.rebuild_glossary()
        self.mine_now()                 # 已采纳的会被排除，列表自动刷新
        message = "已采纳 %d 个词组" % len(added)
        if skipped:
            message += "（%d 个没填中文，跳过了）" % skipped
        self.status.configure(text=message)
        self.app.set_status(message + "：这些词组以后直接命中术语表，少调接口", "ok")

    def _save_auto(self) -> None:
        self.app.config["phrase_auto_enabled"] = bool(self.auto_var.get())
        config_module.save_config(self.app.config)
        if self.auto_var.get():
            self.status.configure(
                text="已打开自动采纳：程序每隔一段时间自己挖一次，"
                     "只收出现次数够高的（收错的词可以在词典里删掉）")
            self.app.auto_mine_phrases()      # 打开就立刻挖一次，别等下一个整点
