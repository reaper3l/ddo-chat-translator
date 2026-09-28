"""设置里的「外观」页：逐项自定义字体、字号、粗细、颜色、底色、边框，并实时预览。

可以单独设置的对象：频道前缀 / 玩家名 / 翻译正文 / 系统消息 / 英文原文 / 时间戳。
说明：Tk 的文本框没有"字形描边"这个能力，所以这里用「底色 + 边框」实现同样的
视觉效果（给文字加背景色和 1~3 像素边框）。留空/0 表示不启用。
"""
from __future__ import annotations

import copy
import tkinter as tk
from tkinter import colorchooser, font as tkfont, ttk

from . import style

SAMPLE_LINES = [
    ("小队", "(小队): [小队] Sckham: ", "兄弟们，你们在 Steam 上玩别的游戏吗？"),
    ("常规", "(常规): ", "我马上到，等我一分钟"),
    ("战利品", "(战利品): ", "Dorgeth 将 Jeweled Key 从 宝箱 中取出"),
]
SYSTEM_SAMPLE = "你的队友 Kendra Estleton 已死亡"


class AppearanceTab:
    def __init__(self, parent: tk.Misc, config: dict, dialog=None) -> None:
        self.config = config
        self.dialog = dialog          # 用来跳转到「频道」页（颜色统一在那边改）
        self.working = copy.deepcopy(config.get("appearance") or {})
        for name, _label in style.ELEMENTS:
            # 和默认值合并：老配置里没有的新字段（比如 follow_channel）也能取到默认值，
            # 否则勾选框会显示成"未勾"，但实际生效值却是默认的"跟随频道"，对不上。
            merged = dict(style.DEFAULTS[name])
            merged.update(self.working.get(name) or {})
            self.working[name] = merged
        self.current = "channel"
        self.vars = {}
        self._build(parent)
        self._load_element("channel")
        self.refresh_preview()

    # ------------------------------------------------------------------ 构建
    def _build(self, parent: tk.Misc) -> None:
        base = ttk.LabelFrame(parent, text="基础（各项默认跟随这里）")
        base.pack(fill="x", padx=10, pady=(8, 4))

        row = ttk.Frame(base)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text="字体", width=10).pack(side="left")
        self.base_family = tk.StringVar(value=self.config.get("font_family", "Microsoft YaHei"))
        ttk.Combobox(row, textvariable=self.base_family, width=22,
                     values=self._families()).pack(side="left")
        ttk.Label(row, text="字号", width=6).pack(side="left", padx=(10, 0))
        self.base_size = tk.StringVar(value=str(self.config.get("font_size", 11)))
        ttk.Spinbox(row, from_=8, to=32, width=5, textvariable=self.base_size).pack(side="left")
        ttk.Label(row, text="界面字号", width=8).pack(side="left", padx=(10, 0))
        self.ui_size = tk.StringVar(value=str(self.config.get("ui_font_size", 10)))
        ttk.Spinbox(row, from_=8, to=18, width=5, textvariable=self.ui_size).pack(side="left")

        row = ttk.Frame(base)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text="界面缩放", width=10).pack(side="left")
        self.ui_scale = tk.StringVar(value=str(self.config.get("ui_scale", 1.0)))
        ttk.Combobox(row, state="readonly", width=6, textvariable=self.ui_scale,
                     values=["0.8", "0.9", "1.0", "1.1", "1.25"]).pack(side="left")
        ttk.Label(row, text="（1.0=标准；调小窗口更小巧）",
                  style="Muted.TLabel").pack(side="left", padx=6)

        row = ttk.Frame(base)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text="窗口底色", width=10).pack(side="left")
        self.base_bg = tk.StringVar(value=self.config.get("bg_color", "#12141c"))
        ttk.Entry(row, textvariable=self.base_bg, width=12).pack(side="left")
        ttk.Button(row, text="取色", width=4,
                   command=lambda: self._pick(self.base_bg)).pack(side="left", padx=4)
        ttk.Label(row, text="正文默认色", width=10).pack(side="left", padx=(10, 0))
        self.base_fg = tk.StringVar(value=self.config.get("font_color", "#8ef58e"))
        ttk.Entry(row, textvariable=self.base_fg, width=12).pack(side="left")
        ttk.Button(row, text="取色", width=4,
                   command=lambda: self._pick(self.base_fg)).pack(side="left", padx=4)

        item = ttk.LabelFrame(parent, text="逐项自定义")
        item.pack(fill="x", padx=10, pady=4)

        row = ttk.Frame(item)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text="编辑对象", width=10).pack(side="left")
        self.element_choices = {label: key for key, label in style.ELEMENTS}
        self.element_var = tk.StringVar(value="频道前缀")
        combo = ttk.Combobox(row, state="readonly", width=14, textvariable=self.element_var,
                             values=[label for _key, label in style.ELEMENTS])
        combo.pack(side="left")
        combo.bind("<<ComboboxSelected>>", lambda _e: self._switch_element())

        row = ttk.Frame(item)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text="字体", width=10).pack(side="left")
        self.font_var = tk.StringVar()
        ttk.Combobox(row, textvariable=self.font_var, width=22,
                     values=["(跟随基础)"] + self._families()).pack(side="left")
        ttk.Label(row, text="字号", width=6).pack(side="left", padx=(10, 0))
        self.size_var = tk.StringVar()
        ttk.Spinbox(row, from_=-6, to=32, width=5, textvariable=self.size_var).pack(side="left")
        ttk.Label(row, text="(0=跟随基础，负数=小几号)", style="Muted.TLabel").pack(
            side="left", padx=6)

        row = ttk.Frame(item)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text="粗细", width=10).pack(side="left")
        self.weight_var = tk.StringVar(value="normal")
        ttk.Combobox(row, state="readonly", width=8, textvariable=self.weight_var,
                     values=["normal", "bold"]).pack(side="left")
        self.slant_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="斜体", variable=self.slant_var).pack(side="left", padx=10)
        ttk.Button(row, text="预览一下", command=self.refresh_preview).pack(side="left")

        row = ttk.Frame(item)
        row.pack(fill="x", pady=3)
        self.color_label = ttk.Label(row, text="文字颜色", width=10)
        self.color_label.pack(side="left")
        self.color_var = tk.StringVar()
        self.color_entry = ttk.Entry(row, textvariable=self.color_var, width=12)
        self.color_entry.pack(side="left")
        pick_button = ttk.Button(row, text="取色", width=4,
                                 command=lambda: self._pick(self.color_var))
        pick_button.pack(side="left", padx=4)
        default_button = ttk.Button(row, text="用默认色", width=8,
                                    command=lambda: self.color_var.set(""))
        default_button.pack(side="left")
        self.color_buttons = [pick_button, default_button]
        self.color_hint = ttk.Label(row, text="", style="Muted.TLabel")
        self.color_hint.pack(side="left", padx=6)

        row = ttk.Frame(item)
        row.pack(fill="x", pady=3)
        self.follow_var = tk.BooleanVar(value=False)
        self.follow_check = ttk.Checkbutton(
            row, text="颜色跟随频道颜色（游戏里整行同色：小队绿、公会蓝、常规黄…）",
            variable=self.follow_var)
        self.follow_check.pack(side="left")

        row = ttk.Frame(item)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text="底色/描边", width=10).pack(side="left")
        self.bg_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.bg_var, width=12).pack(side="left")
        ttk.Button(row, text="取色", width=4,
                   command=lambda: self._pick(self.bg_var)).pack(side="left", padx=4)
        ttk.Label(row, text="边框", width=5).pack(side="left", padx=(8, 0))
        self.border_var = tk.StringVar(value="0")
        ttk.Spinbox(row, from_=0, to=4, width=4, textvariable=self.border_var).pack(side="left")
        ttk.Label(row, text="（Tk 不支持字形描边，用底色+边框近似）",
                  style="Muted.TLabel").pack(side="left", padx=6)

        # 频道颜色不在这里改了 —— 已合并到「设置 → 频道」页：
        # 那里一个频道一行，**名字 / 颜色 / 要不要翻 / 要不要小灯**都在一起，
        # 而且那个颜色同时用于"频道前缀 + 正文（跟随频道色）+ 工具条那盏小灯"，
        # 三处一致，不会再出现"文字是绿的、小灯是另一种绿"这种对不上的情况。
        channel_hint = ttk.LabelFrame(parent, text="频道颜色")
        channel_hint.pack(fill="x", padx=10, pady=4)
        ttk.Label(
            channel_hint, style="Muted.TLabel", justify="left", wraplength=520,
            text="频道名、颜色、要不要翻译、要不要显示小灯，都在「设置 → 频道」里改。\n"
                 "那里的颜色同时用于这个频道的**前缀、正文（跟随频道色时）和工具条小灯**。"
        ).pack(anchor="w", padx=8, pady=6)
        ttk.Button(channel_hint, text="打开「频道」设置",
                   command=self._open_channel_settings).pack(
            anchor="w", padx=8, pady=(0, 8))

        preview_frame = ttk.LabelFrame(parent, text="预览")
        preview_frame.pack(fill="both", expand=True, padx=10, pady=(4, 8))
        self.preview = tk.Text(preview_frame, height=6, wrap="word")
        self.preview.pack(fill="both", expand=True, padx=4, pady=4)
        self.preview.configure(state="disabled")

        for var in (self.base_family, self.base_size, self.base_bg, self.base_fg,
                    self.ui_size):
            var.trace_add("write", lambda *_a: self.refresh_preview())
        for var in (self.font_var, self.size_var, self.weight_var, self.color_var,
                    self.bg_var, self.border_var):
            var.trace_add("write", lambda *_a: self._on_edit())
        self.slant_var.trace_add("write", lambda *_a: self._on_edit())
        self.follow_var.trace_add("write", lambda *_a: self._on_edit())

    def _families(self):
        try:
            families = sorted(set(tkfont.families()))
        except Exception:
            families = []
        return families

    def _pick(self, var: tk.StringVar) -> None:
        try:
            chosen = colorchooser.askcolor(color=var.get() or "#ffffff")
        except Exception:
            chosen = None
        if chosen and chosen[1]:
            var.set(chosen[1])

    def _open_channel_settings(self) -> None:
        """跳到「频道」页改频道名/颜色（颜色统一在那边管）。"""
        try:
            if self.dialog is not None:
                self.dialog.open_category("频道")
        except Exception:
            pass

    # ------------------------------------------------------------------ 编辑
    def _switch_element(self) -> None:
        self._store_current()
        key = self.element_choices.get(self.element_var.get(), "channel")
        self._load_element(key)
        self.refresh_preview()

    def _load_element(self, key: str) -> None:
        self.current = key
        spec = self.working.get(key, dict(style.DEFAULTS[key]))
        self.font_var.set(spec.get("family") or "(跟随基础)")
        self.size_var.set(str(spec.get("size", 0)))
        self.weight_var.set(spec.get("weight", "normal"))
        self.slant_var.set((spec.get("slant") or "roman") == "italic")
        # 频道前缀的颜色统一由下面「频道颜色」按频道设置，这里不再提供全局覆盖，
        # 否则两处设置会打架（谁生效也看不出来）。
        is_channel = key == "channel"
        self.color_var.set("" if is_channel else (spec.get("color", "") or ""))
        self.bg_var.set(spec.get("bg", "") or "")
        self.border_var.set(str(spec.get("border", 0) or 0))
        follow = bool(spec.get("follow_channel", False)) and not is_channel
        self.follow_var.set(follow)
        state = "disabled" if (is_channel or follow) else "normal"
        try:
            self.color_entry.configure(state=state)
            for button in self.color_buttons:
                button.configure(state=state)
            self.follow_check.configure(state="disabled" if is_channel else "normal")
        except Exception:
            pass
        if is_channel:
            hint = "← 频道前缀的颜色请用下方「频道颜色」按频道设置"
        elif follow:
            hint = "← 颜色跟随频道（想固定成一种颜色就取消上面的勾选）"
        elif key == "body":
            hint = "（留空 = 用基础里的正文默认色）"
        else:
            hint = ""
        self.color_hint.configure(text=hint)

    def _on_edit(self) -> None:
        self._store_current()
        self.refresh_preview()

    def _store_current(self) -> None:
        key = getattr(self, "current", "channel")
        family = self.font_var.get().strip()
        if family == "(跟随基础)":
            family = ""
        try:
            size = int(str(self.size_var.get()).strip() or 0)
        except Exception:
            size = 0
        try:
            border = max(0, int(str(self.border_var.get()).strip() or 0))
        except Exception:
            border = 0
        self.working[key] = {
            "family": family,
            "size": size,
            "weight": self.weight_var.get() or "normal",
            "slant": "italic" if self.slant_var.get() else "roman",
            "color": "" if key == "channel" else self.color_var.get().strip(),
            "bg": self.bg_var.get().strip(),
            "border": border,
            "follow_channel": bool(self.follow_var.get()) and key != "channel",
        }

    # ------------------------------------------------------------------ 预览
    def _preview_config(self) -> dict:
        config = dict(self.config)
        config["appearance"] = self.working
        # 频道颜色取当前配置里的那一份（「设置 → 频道」管着它），这样预览里
        # 看到的前缀/正文颜色和主窗口、和小灯都是同一个色
        config["channel_colors"] = dict(self.config.get("channel_colors") or {})
        config["font_family"] = self.base_family.get().strip() or "Microsoft YaHei"
        try:
            config["font_size"] = int(str(self.base_size.get()).strip() or 11)
        except Exception:
            config["font_size"] = 11
        config["bg_color"] = self.base_bg.get().strip() or "#12141c"
        config["font_color"] = self.base_fg.get().strip() or "#8ef58e"
        try:
            config["ui_font_size"] = int(str(self.ui_size.get()).strip() or 10)
        except Exception:
            config["ui_font_size"] = 10
        try:
            config["ui_scale"] = float(str(self.ui_scale.get()).strip() or 1.0)
        except Exception:
            config["ui_scale"] = 1.0
        return config

    def refresh_preview(self) -> None:
        if not hasattr(self, "preview"):      # 构建过程中被回调时先跳过
            return
        config = self._preview_config()
        style.apply_widget_style(self.preview, config)
        style.apply_tags(self.preview, config)
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        for channel, prefix, body in SAMPLE_LINES:
            self.preview.insert("end", prefix, "channel_" + channel)
            self.preview.insert("end", "Sckham: ", "name")
            self.preview.insert("end", body + "\n",
                                style.element_tag(config, "body", channel))
        self.preview.insert("end", "(小队): ", "channel_小队")
        self.preview.insert("end", SYSTEM_SAMPLE + "\n",
                            style.element_tag(config, "system", "小队"))
        self.preview.insert("end", "    english original line\n",
                            style.element_tag(config, "original", "小队"))
        self.preview.configure(state="disabled")

    # ------------------------------------------------------------------ 保存
    def save(self) -> None:
        self._store_current()
        self.config["appearance"] = copy.deepcopy(self.working)
        # 频道颜色不在这里写了：统一由「设置 → 频道」那张表管（保存时会同步到
        # channel_colors，供样式和渲染读取）。
        self.config["font_family"] = self.base_family.get().strip() or "Microsoft YaHei"
        try:
            self.config["font_size"] = int(str(self.base_size.get()).strip() or 11)
        except Exception:
            self.config["font_size"] = 11
        self.config["bg_color"] = self.base_bg.get().strip() or "#12141c"
        self.config["font_color"] = self.base_fg.get().strip() or "#8ef58e"
        try:
            self.config["ui_font_size"] = int(str(self.ui_size.get()).strip() or 10)
        except Exception:
            self.config["ui_font_size"] = 10
        try:
            self.config["ui_scale"] = float(str(self.ui_scale.get()).strip() or 1.0)
        except Exception:
            self.config["ui_scale"] = 1.0
        self.refresh_preview()      # 保存后再刷一次，保证预览和实际显示一致
