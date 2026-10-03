"""纠错窗口、学习中心、词典管理。

这三块合起来就是"自我优化"的入口：
  * 纠错：选中一条翻译 → 改成对的 → 立刻记住（下次同句直接用）。
  * 学习中心：把聊天里反复出现、术语表又没有的词收编；也能看纠错历史、导入导出。
  * 词典：搜索/新增/删除术语（用户词优先于内置词）。
"""
from __future__ import annotations

import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path

from .. import config as config_module
from .. import glossary_io
from . import ask
from . import theme


class CorrectionDialog:
    """修正一条翻译。"""

    def __init__(self, app, source: str, translated: str, on_saved=None) -> None:
        self.app = app
        self.source = source
        self.before = translated
        self.on_saved = on_saved
        self.memory = app.memory

        self.window = tk.Toplevel(app.root)
        self.window.title("纠正这条翻译")
        self.window.transient(app.root)
        self.window.attributes("-topmost", True)
        theme.prepare_window(self.window, app.config)
        theme.frameless_dialog(self.window, "纠正这条翻译", autofocus=True,
                               size=(620, 420))
        self._build()

    def _build(self) -> None:
        theme.label(self.window, "① 玩家原话（英文，参考用）", muted=True,
                    anchor="w").pack(
            fill="x", padx=12, pady=(10, 2))
        source_box = theme.text_widget(self.window, height=3, wrap="word")
        source_box.pack(fill="x", padx=12)
        source_box.insert("1.0", self.source)
        source_box.configure(state="disabled")

        theme.label(self.window, "② 改成正确的中文", anchor="w").pack(
            fill="x", padx=12, pady=(10, 2))
        self.edit = theme.text_widget(
            self.window, height=3, wrap="word",
            font=(self.app.config.get("font_family", "Microsoft YaHei"), 12))
        self.edit.pack(fill="x", padx=12)
        self.edit.insert("1.0", self.before)
        # 明确指定输入框：本窗口第一个 Text 是只读的原文框，不能让焦点落到它上面
        theme.set_dialog_input(self.window, self.edit)
        self.edit.focus_set()
        self.edit.bind("<Control-Return>", self._save)

        self.remember = tk.BooleanVar(value=True)
        ttk.Checkbutton(self.window,
                        text="记住这句：以后遇到同样的句子直接用这个译文（不调用翻译接口）",
                        variable=self.remember).pack(anchor="w", padx=12, pady=8)

        info = "这句你已经改过 %d 次" % self._count() if self._count() else \
            "改过之后，同样的句子会自动用你的译文；改得多了还会写进提示词影响新句子。"
        theme.label(self.window, info, muted=True, wraplength=560,
                    justify="left").pack(anchor="w", padx=12)

        row = ttk.Frame(self.window)
        row.pack(fill="x", padx=12, pady=10)
        ttk.Button(row, text="保存并生效（Ctrl+Enter）", command=self._save).pack(
            side="left")
        ttk.Button(row, text="取消", command=self.window.destroy).pack(
            side="right")

    def _count(self) -> int:
        from .. import textutil

        item = self.memory.data.get("phrases", {}).get(textutil.fingerprint(self.source))
        return int(item.get("count", 0)) if item else 0

    def _save(self, _event=None):
        after = self.edit.get("1.0", "end").strip()
        if not after:
            messagebox.showwarning("提示", "译文不能是空的")
            return "break"
        min_count = int(self.app.config.get("learn_min_count", 2))
        result = self.memory.learn_correction(self.source, self.before, after,
                                              min_count=min_count)
        self.app.memory.flush(force=True)
        self.app.pipeline.invalidate_cache()
        if self.on_saved:
            self.on_saved(after, result)
        message = "已记住" if self.remember.get() else "已记录"
        if result.get("stable"):
            message += "（这句改过 %d 次，也会写进提示词）" % result["count"]
        self.app.set_status(message, "ok")
        self.window.destroy()
        return "break"


class LearningCenterDialog:
    """学习中心：待学习词 / 已学习 / 纠错历史。"""

    def __init__(self, app) -> None:
        self.app = app
        self.memory = app.memory
        self.window = tk.Toplevel(app.root)
        self.window.title("学习中心")
        self.window.transient(app.root)
        theme.prepare_window(self.window, app.config)
        theme.frameless_dialog(self.window, "学习中心", size=(820, 560))
        self._build()
        self.refresh()

    def _build(self) -> None:
        notebook = ttk.Notebook(self.window)
        notebook.pack(fill="both", expand=True, padx=8, pady=8)

        # ---- 待学习词 ----
        tab = ttk.Frame(notebook)
        notebook.add(tab, text="待学习词")
        theme.label(tab, "这些词在聊天里反复出现，但术语表里没有。给它填个中文，"
                         "以后就会被当作术语保护起来（翻译更准，也不容易被模型乱翻）。",
                    muted=True, wraplength=760, justify="left").pack(anchor="w", pady=(4, 6))
        self.cand_tree = ttk.Treeview(tab, columns=("word", "count", "problem", "sample"),
                                      show="headings", height=12)
        for column, title, width in (("word", "英文词", 150),
                                     ("count", "出现次数", 80),
                                     ("problem", "翻不出来", 80),
                                     ("sample", "出现在（例子）", 400)):
            self.cand_tree.heading(column, text=title)
            self.cand_tree.column(column, width=width)
        self.cand_tree.pack(fill="both", expand=True)
        row = ttk.Frame(tab)
        row.pack(fill="x", pady=6)
        ttk.Button(row, text="加入词典", command=self.add_term).pack(side="left")
        ttk.Button(row, text="忽略这个词", command=self.ignore_term).pack(
            side="left", padx=6)
        ttk.Button(row, text="清空列表", command=self.clear_candidates).pack(
            side="left", padx=6)
        ttk.Button(row, text="刷新", command=self.refresh).pack(side="right")

        # ---- 已学习 ----
        tab2 = ttk.Frame(notebook)
        notebook.add(tab2, text="已学习")
        self.learned_tree = ttk.Treeview(
            tab2, columns=("kind", "source", "target", "count"),
            show="headings", height=14)
        for column, title, width in (("kind", "类型", 90),
                                     ("source", "原文/术语", 300),
                                     ("target", "译文", 260),
                                     ("count", "次数", 60)):
            self.learned_tree.heading(column, text=title)
            self.learned_tree.column(column, width=width)
        self.learned_tree.pack(fill="both", expand=True)
        row2 = ttk.Frame(tab2)
        row2.pack(fill="x", pady=6)
        ttk.Button(row2, text="删除选中", command=self.delete_learned).pack(side="left")
        ttk.Button(row2, text="导出学习库", command=self.export_memory).pack(
            side="left", padx=6)
        ttk.Button(row2, text="导入学习库", command=self.import_memory).pack(
            side="left", padx=6)
        ttk.Button(row2, text="刷新", command=self.refresh).pack(side="right")

        # ---- 纠错历史 ----
        tab3 = ttk.Frame(notebook)
        notebook.add(tab3, text="纠错历史")
        self.history_tree = ttk.Treeview(
            tab3, columns=("time", "source", "before", "after"),
            show="headings", height=14)
        for column, title, width in (("time", "时间", 140),
                                     ("source", "英文原文", 220),
                                     ("before", "改前", 190),
                                     ("after", "改后", 190)):
            self.history_tree.heading(column, text=title)
            self.history_tree.column(column, width=width)
        self.history_tree.pack(fill="both", expand=True)

        self.stats_label = theme.label(self.window, "", muted=True, anchor="w")
        self.stats_label.pack(fill="x", padx=10)
        ttk.Button(self.window, text="关闭", command=self.window.destroy).pack(
            side="right", padx=10, pady=6)

    # ------------------------------------------------------------ 数据刷新
    def refresh(self) -> None:
        min_count = int(self.app.config.get("candidate_min_count", 3))

        for item in self.cand_tree.get_children():
            self.cand_tree.delete(item)
        for row in self.memory.candidates(min_count=min_count):
            self.cand_tree.insert("", "end", iid="cand:%s" % row["token"],
                                  values=(row["token"], row["count"], row.get("problem", 0),
                                          (row["samples"] or [""])[0]))

        for item in self.learned_tree.get_children():
            self.learned_tree.delete(item)
        for term in self.memory.term_list():
            self.learned_tree.insert("", "end", iid="term:%s" % term["text"].lower(),
                                     values=("术语", term["text"], term["zh"],
                                             term.get("count", 1)))
        for phrase in self.memory.phrase_rules(limit=300):
            self.learned_tree.insert(
                "", "end", iid="phrase:%s" % phrase.get("source", "")[:60],
                values=("整句", phrase.get("source", ""), phrase.get("zh", ""),
                        phrase.get("count", 1)))

        for item in self.history_tree.get_children():
            self.history_tree.delete(item)
        for record in reversed(self.memory.data.get("corrections", [])[-200:]):
            self.history_tree.insert("", "end", values=(
                record.get("time", ""), record.get("source", ""),
                record.get("before", ""), record.get("after", "")))

        summary = self.memory.summary()
        stats = self.memory.data.get("stats", {})
        self.stats_label.config(text="  ".join("%s %d" % kv for kv in summary.items())
                                + "   |   累计翻译 %d 条，记忆命中 %d 次，接口调用 %d 次"
                                % (stats.get("translated", 0), stats.get("memory_hits", 0),
                                   stats.get("api_calls", 0)))

    # ------------------------------------------------------------ 待学习词
    def _selected_candidate(self):
        selection = self.cand_tree.selection()
        if not selection:
            messagebox.showinfo("提示", "先选中一个词")
            return None
        return selection[0].split(":", 1)[-1]

    def add_term(self) -> None:
        token = self._selected_candidate()
        if not token:
            return
        ask.ask_one(self.app, "加入词典", "「%s」应该翻译成什么？" % token,
                    on_submit=lambda zh: self._save_candidate(token, zh),
                    hint="填好点确定，就会加进术语表（以后词典里优先用你的译法）")

    def _save_candidate(self, token: str, zh: str) -> None:
        self.memory.set_term(token, zh.strip())
        self.memory.ignore_candidate(token)
        self.memory.flush(force=True)
        self.app.rebuild_glossary()
        self.app.set_status("已把 %s → %s 加入术语表" % (token, zh.strip()), "ok")
        self.refresh()

    def ignore_term(self) -> None:
        token = self._selected_candidate()
        if not token:
            return
        self.memory.ignore_candidate(token)
        self.refresh()

    def clear_candidates(self) -> None:
        if messagebox.askyesno("确认", "清空待学习列表？（不会删除已学的词）"):
            self.memory.clear_candidates()
            self.refresh()

    # ------------------------------------------------------------ 已学习
    def delete_learned(self) -> None:
        selection = self.learned_tree.selection()
        if not selection:
            messagebox.showinfo("提示", "先选中一行")
            return
        for item_id in selection:
            kind, _, value = item_id.partition(":")
            if kind == "term":
                self.memory.delete_term(value)
            elif kind == "phrase":
                self.memory.delete_phrase(value)
        self.memory.flush(force=True)
        self.app.rebuild_glossary()
        self.refresh()

    def export_memory(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self.window, defaultextension=".json",
            initialfile="ddo_学习库_%s.json" % time.strftime("%Y%m%d"),
            filetypes=[("JSON", "*.json")])
        if path and self.memory.export_to(path):
            messagebox.showinfo("完成", "已导出到：\n%s" % path)

    def import_memory(self) -> None:
        path = filedialog.askopenfilename(parent=self.window,
                                          filetypes=[("JSON", "*.json")])
        if not path:
            return
        added = self.memory.import_from(path)
        self.memory.flush(force=True)
        self.app.rebuild_glossary()
        messagebox.showinfo("完成", "导入完成：句子 %d 条，术语 %d 条"
                            % (added.get("phrases", 0), added.get("terms", 0)))
        self.refresh()


class DictionaryDialog:
    """术语表管理：搜索、添加、覆盖、删除、导入导出。"""

    def __init__(self, app) -> None:
        self.app = app
        self.window = tk.Toplevel(app.root)
        self.window.title("词典 / 术语表")
        self.window.transient(app.root)
        theme.prepare_window(self.window, app.config)
        theme.frameless_dialog(self.window, "词典 / 术语表", size=(760, 560))
        self._build()
        self.refresh()

    def _build(self) -> None:
        row = ttk.Frame(self.window)
        row.pack(fill="x", padx=10, pady=8)
        theme.label(row, "搜索:", muted=True).pack(side="left")
        self.keyword = tk.StringVar()
        entry = ttk.Entry(row, textvariable=self.keyword, width=30)
        entry.pack(side="left", padx=6)
        entry.bind("<Return>", lambda _e: self.refresh())
        ttk.Button(row, text="搜索", command=self.refresh).pack(side="left")
        ttk.Button(row, text="新增/覆盖", command=self.add_term).pack(
            side="left", padx=6)
        ttk.Button(row, text="删除选中的用户词", command=self.delete_term).pack(
            side="left", padx=6)

        io_row = ttk.Frame(self.window)
        io_row.pack(fill="x", padx=10, pady=(0, 4))
        ttk.Button(io_row, text="导出全部…",
                   command=lambda: self.export_terms(only_mine=False)).pack(side="left")
        ttk.Button(io_row, text="只导出我的…",
                   command=lambda: self.export_terms(only_mine=True)).pack(
            side="left", padx=6)
        ttk.Button(io_row, text="导入…", command=self.import_terms).pack(side="left")
        theme.label(io_row, "导出 JSON / CSV；导入只写入新增或改动过的词",
                    muted=True).pack(side="left", padx=10)

        # 公共词典：启动时从网上下载的签名词表（只下载、不上传任何东西）
        pub_row = ttk.Frame(self.window)
        pub_row.pack(fill="x", padx=10, pady=(0, 4))
        self.public_var = tk.BooleanVar(
            value=bool(self.app.config.get("public_dict_enabled", True)))
        ttk.Checkbutton(pub_row, text="自动获取公共词典", variable=self.public_var,
                        command=self._save_public_switch).pack(side="left")
        ttk.Button(pub_row, text="立即更新",
                   command=self.update_public).pack(side="left", padx=6)
        self.public_label = theme.label(pub_row, "", muted=True)
        self.public_label.pack(side="left", padx=6)

        self.tree = ttk.Treeview(self.window, columns=("term", "zh", "source"),
                                 show="headings", height=18)
        for column, title, width in (("term", "英文术语", 240),
                                     ("zh", "中文", 300),
                                     ("source", "来源", 140)):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width)
        self.tree.pack(fill="both", expand=True, padx=10)

        bottom = ttk.Frame(self.window)
        bottom.pack(fill="x", padx=10, pady=8)
        self.count_label = theme.label(bottom, "", muted=True)
        self.count_label.pack(side="left")
        ttk.Button(bottom, text="关闭", command=self.window.destroy).pack(side="right")

    def refresh(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        keyword = self.keyword.get().strip().lower()
        user_terms = {t["text"].lower(): t for t in self.app.memory.term_list()}

        rows = []
        for term, zh in self.app.glossary.terms().items():
            if keyword and keyword not in term.lower() and keyword not in str(zh).lower():
                continue
            source = "我的" if term.lower() in user_terms else "内置"
            rows.append((term, zh, source))
        rows.sort(key=lambda item: (item[2] != "我的", item[0].lower()))
        used_ids = set()
        for term, zh, source in rows[:1500]:
            item_id = "dict:%s" % term.lower()
            if item_id in used_ids:          # 大小写不同的同名词，跳过重复项
                continue
            used_ids.add(item_id)
            self.tree.insert("", "end", iid=item_id, values=(term, zh, source))
        self.count_label.config(text="共 %d 条（显示前 1500 条）" % len(rows))
        self._refresh_public()

    # ------------------------------------------------------------ 公共词典
    def _refresh_public(self) -> None:
        """显示公共词典状态（条数、更新时间）。"""
        from .. import public_dict

        try:
            status = public_dict.status(self.app.config)
        except Exception:
            return
        if not status["enabled"]:
            text = "公共词典：已关闭（只用内置表）"
        elif not status["has_data"]:
            text = "公共词典：还没下载过（联网后会自动获取）"
        else:
            when = time.strftime("%m-%d %H:%M",
                                 time.localtime(status["updated_at"] or 0))
            text = "公共词典：%d 条，更新于 %s（每 %d 小时检查一次）" % (
                status["terms"], when, status["interval_hours"])
            if status.get("disabled"):
                text += "；本机已停用 %d 条（你改过译法的）" % status["disabled"]
        self.public_label.configure(text=text)

    def _save_public_switch(self) -> None:
        """开关公共词典：关掉立刻把公共词条从当前术语表里撤下来。"""
        enabled = bool(self.public_var.get())
        self.app.config["public_dict_enabled"] = enabled
        config_module.save_config(self.app.config)
        self.app.rebuild_glossary()
        self.refresh()
        self.app.set_status("公共词典已%s" % ("开启" if enabled else "关闭"), "info")

    def update_public(self) -> None:
        """手动更新一次（结果在主窗口状态栏显示，这里刷两次标签）。"""
        self.app.maybe_sync_public_dict(manual=True)
        self.window.after(1500, self.refresh)
        self.window.after(5000, self.refresh)

    def add_term(self) -> None:
        # 选中某一行时，把它带进输入框 —— 改一改点确定就是「覆盖」
        term, zh = "", ""
        selection = self.tree.selection()
        if selection:
            values = self.tree.item(selection[0], "values")
            if len(values) >= 2:
                term, zh = str(values[0]), str(values[1])
        ask.ask_two(
            self.app, "新增 / 覆盖术语",
            "英文术语（或缩写）", "中文翻译",
            first=term, second=zh, hint="同名的词会被覆盖；内置词也能用你自己的译法盖掉",
            on_submit=self._save_term)

    def _save_term(self, term: str, zh: str) -> None:
        self.app.memory.set_term(term.strip(), zh.strip())
        self.app.memory.flush(force=True)
        self.app.rebuild_glossary()
        self.refresh()
        self.app.set_status("已加入术语表：%s → %s" % (term.strip(), zh.strip()), "ok")

    def delete_term(self) -> None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("提示", "先选中一行")
            return
        term = selection[0].split(":", 1)[-1]
        if self.app.memory.delete_term(term):
            self.app.memory.flush(force=True)
            self.app.rebuild_glossary()
            self.refresh()
        else:
            messagebox.showinfo("提示", "内置词不能删除，只能用同名的用户词覆盖它")

    # -------------------------------------------------------------- 导出 / 导入
    def export_terms(self, only_mine: bool = False) -> None:
        """导出术语表。

        only_mine=True 时只导出"我的词条"（自己加的 / 学习到的 / 纠错来的），
        不含内置表 —— 自己备份、换电脑搬的时候更干净；默认导出整份，方便分享给队友。
        """
        if only_mine:
            terms = glossary_io.user_terms(self.app.memory.term_list())
            title = "只导出我的词条"
            default_name = "ddo术语表_我的.json"
        else:
            terms = self.app.glossary.terms()
            title = "导出全部术语"
            default_name = "ddo术语表_全部.json"
        if not terms:
            messagebox.showinfo(
                "提示",
                "你的词条还是空的（内置词条不算「我的词条」）。\n"
                "可以在学习中心收编生词，或者用「新增/覆盖」自己加。" if only_mine
                else "现在术语表是空的，没什么可导出的", parent=self.window)
            return
        path = filedialog.asksaveasfilename(
            parent=self.window, title=title, defaultextension=".json",
            initialfile=default_name,
            filetypes=[("JSON（推荐，可导入回来）", "*.json"),
                       ("CSV / 表格（方便 Excel 编辑）", "*.csv"),
                       ("所有文件", "*.*")])
        if not path:
            return
        fmt = "csv" if str(path).lower().endswith((".csv", ".txt")) else "json"
        try:
            Path(path).write_text(glossary_io.dump_terms(terms, fmt), encoding="utf-8-sig"
                                  if fmt == "csv" else "utf-8")
        except Exception as exc:
            messagebox.showwarning("导出失败", str(exc), parent=self.window)
            return
        self.app.set_status("已导出 %d 条%s到 %s"
                            % (len(terms), "" if only_mine else "术语", path), "ok")
        messagebox.showinfo(
            "导出完成",
            "已导出 %d 条%s：\n%s\n\n%s"
            % (len(terms),
               "我的词条" if only_mine else "术语（内置 + 我的）",
               path,
               "这份只包含你自己加的词，适合备份。"
               if only_mine else "发给队友，他们用「导入…」就能直接用。"),
            parent=self.window)

    def import_terms(self) -> None:
        """从 JSON/CSV 导入术语；只写入新增或改过译法的词。"""
        path = filedialog.askopenfilename(
            parent=self.window, title="导入术语表",
            filetypes=[("术语表（JSON / CSV）", "*.json *.csv *.txt"),
                       ("所有文件", "*.*")])
        if not path:
            return
        try:
            incoming = glossary_io.load_terms(
                Path(path).read_text(encoding="utf-8-sig", errors="replace"), path)
        except Exception as exc:
            messagebox.showwarning("导入失败", "读不出这个文件：%s" % exc, parent=self.window)
            return
        to_write, unchanged = glossary_io.plan_import(self.app.glossary.terms(), incoming)
        if not to_write:
            messagebox.showinfo(
                "没有需要导入的",
                "文件里 %d 条术语和现在的译法一模一样，不用导入。" % unchanged,
                parent=self.window)
            return
        if not messagebox.askyesno(
                "确认导入",
                "文件里共 %d 条：\n· 新增/改动 %d 条（会覆盖同名译法）\n· 和现在相同 %d 条（跳过）\n\n"
                "继续导入吗？（内置词不会被删掉，只会被你的同名译法覆盖）"
                % (len(incoming), len(to_write), unchanged), parent=self.window):
            return
        for term, zh in to_write.items():
            self.app.memory.set_term(term, zh, source="import")
        self.app.memory.flush(force=True)
        self.app.rebuild_glossary()
        self.refresh()
        self.app.set_status("已导入 %d 条术语（跳过 %d 条相同的）"
                            % (len(to_write), unchanged), "ok")
        messagebox.showinfo(
            "导入完成",
            "已导入 %d 条（跳过 %d 条和现有译法相同的）。" % (len(to_write), unchanged),
            parent=self.window)
