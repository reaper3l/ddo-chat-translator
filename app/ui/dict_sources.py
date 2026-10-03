"""「词典源」对话框：像订阅列表一样管理词典来源。

三种源：
* **官方源**：程序内置，必须验签，永远是第一个（可以停用，不能删除）；
* **本地文件**：自己整理的 JSON，改完点一下「立即更新」就生效；
* **网地址**：别人分享的 JSON（Gitee/GitHub raw、自建静态文件都行）。

两条底线（写在界面上，也写在代码里）：
1. 所有源**只做加法**：同一个词先出现的赢 —— 官方 > 你加的源；
2. 你自己在词典里改过的词永远最大，任何源都覆盖不了它。
"""
from __future__ import annotations

import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .. import config as config_module
from .. import public_dict
from . import ask, theme

KIND_TEXT = {public_dict.KIND_OFFICIAL: "官方（内置）",
             public_dict.KIND_FILE: "本地文件",
             public_dict.KIND_URL: "网地址"}


class DictSourcesDialog:
    def __init__(self, app, on_change=None) -> None:
        self.app = app
        self.on_change = on_change
        self.window = tk.Toplevel(app.root)
        self.window.title("词典源")
        self.window.transient(app.root)
        theme.prepare_window(self.window, app.config)
        theme.frameless_dialog(self.window, "词典源", size=(740, 480))
        self._build()
        self.refresh()

    # ------------------------------------------------------------------ 构建
    def _build(self) -> None:
        head = ttk.Frame(self.window)
        head.pack(fill="x", padx=12, pady=(10, 4))
        theme.label(
            head,
            "官方源由程序内置、必须验签；你也可以自己加源，像订阅列表一样：\n"
            "本地文件（自己整理的 JSON，改完立刻生效）或网地址（别人分享的 JSON）。\n"
            "所有源都只做加法：同名不覆盖，官方 > 你加的源，你自己改过的词永远最大。",
            muted=True, justify="left").pack(anchor="w")

        self.tree = ttk.Treeview(self.window,
                                 columns=("on", "name", "kind", "terms", "when", "note"),
                                 show="headings", height=10, selectmode="browse")
        for column, title, width, anchor in (
                ("on", "启用", 46, "center"), ("name", "名称", 170, "w"),
                ("kind", "类型", 90, "w"), ("terms", "词条", 56, "center"),
                ("when", "更新于", 104, "center"), ("note", "地址 / 上次错误", 240, "w")):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, anchor=anchor)
        self.tree.pack(fill="both", expand=True, padx=12, pady=(6, 0))
        self.tree.bind("<Double-1>", lambda _event: self.toggle())

        row = ttk.Frame(self.window)
        row.pack(fill="x", padx=12, pady=(8, 2))
        ttk.Button(row, text="添加本地文件…", command=self.add_file).pack(side="left")
        ttk.Button(row, text="添加网地址…", command=self.add_url).pack(side="left", padx=6)
        ttk.Button(row, text="启用/停用", command=self.toggle).pack(side="left", padx=6)
        ttk.Button(row, text="上移", command=lambda: self.move(-1)).pack(side="left")
        ttk.Button(row, text="下移", command=lambda: self.move(1)).pack(side="left", padx=6)
        ttk.Button(row, text="删除", command=self.remove).pack(side="left")

        bottom = ttk.Frame(self.window)
        bottom.pack(fill="x", padx=12, pady=(6, 10))
        self.status = theme.label(bottom, "", muted=True)
        self.status.pack(side="left")
        ttk.Button(bottom, text="关闭", command=self.window.destroy).pack(side="right")
        ttk.Button(bottom, text="立即更新", command=self.refresh_all).pack(
            side="right", padx=6)

    # ------------------------------------------------------------------ 状态
    def _rows(self):
        return public_dict.sources(self.app.config)

    def _selected_index(self) -> int:
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("提示", "先在上面选中一行", parent=self.window)
            return -1
        ids = [row["id"] for row in self._rows()]
        return ids.index(selection[0]) if selection[0] in ids else -1

    def refresh(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        try:
            state = public_dict.status(self.app.config)
        except Exception:                          # noqa: BLE001
            return
        for row in state["sources"]:
            when = "-"
            if row["updated_at"]:
                when = time.strftime("%m-%d %H:%M", time.localtime(row["updated_at"]))
            note = row["error"] or row["path"] or row["url"] or ""
            self.tree.insert("", "end", iid=row["id"], values=(
                "开" if row["enabled"] else "关", row["name"],
                KIND_TEXT.get(row["kind"], row["kind"]), row["terms"], when, note))
        text = "合并后共 %d 条词" % state["terms"]
        if state.get("disabled"):
            text += "；本机停用 %d 条（你改过译法的）" % state["disabled"]
        self.status.configure(text=text)

    # ------------------------------------------------------------------ 动作
    def toggle(self) -> None:
        index = self._selected_index()
        if index < 0:
            return
        rows = self._rows()
        if rows[index]["kind"] == public_dict.KIND_OFFICIAL and rows[index]["enabled"]:
            if not messagebox.askyesno(
                    "确认", "停用官方词典？之后只用内置表和你自己加的源。",
                    parent=self.window):
                return
        rows[index]["enabled"] = not rows[index]["enabled"]
        self._save(rows, select=rows[index]["id"])

    def move(self, delta: int) -> None:
        index = self._selected_index()
        if index < 0:
            return
        rows = self._rows()
        target = index + delta
        if (rows[index]["kind"] == public_dict.KIND_OFFICIAL
                or target < 1 or target >= len(rows)):
            return
        rows[index], rows[target] = rows[target], rows[index]
        self._save(rows, select=rows[target]["id"])

    def remove(self) -> None:
        index = self._selected_index()
        if index < 0:
            return
        rows = self._rows()
        if rows[index]["kind"] == public_dict.KIND_OFFICIAL:
            messagebox.showinfo("提示", "官方源不能删除（可以停用）", parent=self.window)
            return
        if not messagebox.askyesno(
                "确认", "删除词典源「%s」？\n它加进来的词会在重建术语表后消失。"
                        % rows[index]["name"], parent=self.window):
            return
        rows.pop(index)
        self._save(rows)

    def add_file(self) -> None:
        if not self._check_room():
            return
        path = filedialog.askopenfilename(
            parent=self.window, title="选择词典 JSON",
            filetypes=[("词典 JSON", "*.json"), ("所有文件", "*.*")])
        if not path:
            return
        default = Path(path).stem

        def submit(name: str) -> None:
            self._append({"name": (name or "").strip() or default, "kind": "file",
                          "path": path, "enabled": True, "ack": True})

        ask.ask_one(self.app, "添加本地词典源", "给它起个名字", submit,
                    hint="本地文件随时可以改，改完点「立即更新」就生效；同名不会覆盖官方词。",
                    initial=default)

    def add_url(self) -> None:
        if not self._check_room():
            return

        def submit(name: str, url: str) -> None:
            url = (url or "").strip()
            if not url.lower().startswith(("http://", "https://")):
                messagebox.showwarning("提示", "网地址要以 http:// 或 https:// 开头",
                                       parent=self.window)
                return
            if not messagebox.askyesno(
                    "来源由你自己判断",
                    "网上的词典源不是官方发布的，内容和安全性由提供者负责。\n"
                    "程序能保证两件事：同名不会覆盖官方词和你自己的词；"
                    "不想要了随时删掉。\n\n确定添加这个源吗？", parent=self.window):
                return
            self._append({"name": (name or "").strip() or "网词典源", "kind": "url",
                          "url": url, "enabled": True, "ack": True})

        ask.ask_two(self.app, "添加网词典源", "名称", "地址（http/https 的 JSON）",
                    submit, hint="地址指向的 JSON 和官方词典同一种格式；"
                                 "带 .sig 的话程序会自动验签。")

    def refresh_all(self) -> None:
        """手动更新一次：官方源和你加的网源都试一遍，本地文件立刻重读。"""
        self.app.maybe_sync_public_dict(manual=True)
        self.window.after(1200, self.refresh)
        self.window.after(4000, self.refresh)

    # ------------------------------------------------------------------ 内部
    def _check_room(self) -> bool:
        if len(self._rows()) >= public_dict.MAX_SOURCES:
            messagebox.showinfo(
                "提示", "源太多了（最多 %d 个），先删掉几个再加。"
                        % public_dict.MAX_SOURCES, parent=self.window)
            return False
        return True

    def _append(self, source: dict) -> None:
        rows = self._rows()
        used = {row["id"] for row in rows}
        index = 1
        while ("src%s-%d" % (time.strftime("%Y%m%d"), index)) in used:
            index += 1
        source["id"] = "src%s-%d" % (time.strftime("%Y%m%d"), index)
        rows.append(source)
        self._save(rows, select=source["id"])

    def _save(self, rows, select: str = "") -> None:
        payload = []
        for row in rows:
            if row["kind"] == public_dict.KIND_OFFICIAL:
                continue                   # 官方源不进配置（程序内置的那份）
            payload.append({
                "id": row["id"], "name": row["name"], "kind": row["kind"],
                "url": row.get("url", ""), "path": row.get("path", ""),
                "enabled": bool(row.get("enabled", True)),
                "ack": bool(row.get("ack", False)),
                "pubkey": row.get("pubkey", ""),
            })
        self.app.config["dict_sources"] = payload
        config_module.save_config(self.app.config)
        public_dict.invalidate()
        self.app.rebuild_glossary()
        self.refresh()
        if select and self.tree.exists(select):
            self.tree.selection_set(select)
        self.app.maybe_sync_public_dict(manual=True)
        if callable(self.on_change):
            try:
                self.on_change()
            except Exception:                      # noqa: BLE001
                pass
