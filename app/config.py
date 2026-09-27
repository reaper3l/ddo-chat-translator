"""配置管理：默认值 + 增量合并 + 原子保存。"""
from __future__ import annotations

from typing import Any, Dict

from . import paths

DEFAULT_CONFIG: Dict[str, Any] = {
    "version": 3,
    # 坐标体系：auto=按显示器感知 DPI（推荐，框选与截图都用物理像素）
    #          legacy=旧行为（不感知 DPI，坐标会被 Windows 按缩放虚拟化）
    # 改完需要重启程序。
    "dpi_mode": "auto",
    # 监控
    "region": None,              # [x1, y1, x2, y2]，屏幕物理像素
    "interval_ms": 1200,         # 两次截图之间的间隔
    "skip_identical_frame": True,  # 画面像素完全没变时跳过 OCR
    "merge_same_row": True,        # 同一行被 OCR 切成两段时拼回去
    # OCR 前放大图片：auto=小图自动放大 2 倍（推荐）；也可以填 1 / 2 / 3
    "ocr_upscale": "auto",
    # 系统消息（组队/死亡/断线/战利品）默认也显示 —— 和游戏聊天框保持一致
    "show_system": True,
    "show_notes": False,         # 是否在译文后面标注"记忆命中/缓存"等来源
    # 翻译
    "engine": "deepseek",        # deepseek | mymemory | offline
    "deepseek_key": "",
    "deepseek_model": "deepseek-chat",
    "translate_mode": "quality", # quality | fast
    "context_turns": 5,          # 送给模型的上文条数
    "timeout_seconds": 20,
    "max_queue": 30,
    # 界面
    "always_on_top": True,
    "frameless": True,           # 默认无边框：占用小、贴着游戏也不挡画面
    "toolbar_icons_only": True,  # 工具栏默认只显示图标（更紧凑，悬停有说明）
    "toolbar_collapsed": True,   # 工具条默认折叠，只留标题；点标题展开/收起
    "show_status_bar": True,     # 底部状态栏（待译/调用等计数）
    # 背景透明：off=不透明；alpha=整窗半透明；key=只透明背景（文字保持清晰）
    "transparency_mode": "off",
    "alpha": "0.85",             # alpha 模式下的整窗透明度（0.3~1.0）
    "ui_scale": 1.0,             # 界面缩放：1.0=96DPI 经典比例；0.85 更小巧
    "show_original": False,
    "show_timestamp": False,
    "font_family": "Microsoft YaHei",
    "font_size": 11,
    "ui_font_size": 11,          # 界面（按钮/设置）字号，聊天文字另有设置
    "font_weight": "normal",
    "font_color": "#8ef58e",
    "bg_color": "#12141c",
    # 逐项外观：family/size/weight/slant/color/bg/border 都能单独设
    #   size=0 跟随基础字号；size 为负数表示比基础字号小 N 号
    #   color 留空表示用默认（频道前缀用该频道的颜色，正文用 font_color）
    #   bg=底色，border=边框宽度（Tk 文本框不支持字形描边，用底色+边框近似）
    "appearance": {
        "channel": {"family": "", "size": 0, "weight": "bold", "slant": "roman",
                    "color": "", "bg": "", "border": 0, "follow_channel": False},
        "name": {"family": "", "size": 0, "weight": "bold", "slant": "roman",
                 "color": "#ffffff", "bg": "", "border": 0, "follow_channel": False},
        "body": {"family": "", "size": 0, "weight": "normal", "slant": "roman",
                 "color": "", "bg": "", "border": 0, "follow_channel": True},
        "system": {"family": "", "size": 0, "weight": "normal", "slant": "roman",
                   "color": "#e8e8e8", "bg": "", "border": 0, "follow_channel": False},
        "original": {"family": "", "size": -2, "weight": "normal", "slant": "roman",
                     "color": "#98a1ab", "bg": "", "border": 0, "follow_channel": False},
        "timestamp": {"family": "", "size": -2, "weight": "normal", "slant": "roman",
                      "color": "#98a1ab", "bg": "", "border": 0, "follow_channel": False},
    },
    "channel_colors": {
        "小队": "#7ee787",
        "队伍": "#7ee787",
        "公会": "#79c0ff",
        "常规": "#ffd866",
        "公共": "#ffa657",
        "悄悄话": "#ff9ecd",
        "战利品": "#8b949e",
    },
    "channels_enabled": {
        "小队": True,
        "队伍": True,
        "公会": True,
        "常规": True,
        "公共": True,
        "悄悄话": True,
    },
    "max_lines": 600,            # 显示区最多保留多少行，防止越用越卡
    # 词典 / 学习
    "use_glossary": True,
    "use_extra_glossary": True,
    "learn_min_count": 2,        # 同一句被修正多少次后写入长期规则
    "candidate_min_count": 3,    # 陌生词出现多少次后进入"待学习"列表
    "dedup_ttl_seconds": 90,
    # 高级
    "window_pos": None,
    "window_size": [520, 620],
}


def load_config() -> Dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    config["channel_colors"] = dict(DEFAULT_CONFIG["channel_colors"])
    config["channels_enabled"] = dict(DEFAULT_CONFIG["channels_enabled"])
    user = paths.read_json(paths.CONFIG_PATH, {})
    if isinstance(user, dict):
        for key, value in user.items():
            config[key] = value
    return config


def save_config(config: Dict[str, Any]) -> bool:
    return paths.write_json(paths.CONFIG_PATH, config)
