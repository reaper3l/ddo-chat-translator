"""显示样式：把配置里的外观设置翻译成 Tk 的字体/颜色/标签。

主窗口和"设置 → 外观"里的预览共用这里的逻辑，保证所见即所得。
可以逐项自定义的对象：频道前缀、玩家名、翻译正文、系统消息、英文原文、时间戳。
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

ELEMENTS = [
    ("channel", "频道前缀"),
    ("name", "玩家名"),
    ("body", "翻译正文"),
    ("system", "系统消息"),
    ("original", "英文原文"),
    ("timestamp", "时间戳"),
]

DEFAULTS: Dict[str, dict] = {
    "channel": {"family": "", "size": 0, "weight": "bold", "slant": "roman",
                "color": "", "bg": "", "border": 0, "follow_channel": False},
    "name": {"family": "", "size": 0, "weight": "bold", "slant": "roman",
             "color": "#ffffff", "bg": "", "border": 0, "follow_channel": False},
    # 正文默认"跟随频道颜色"：小队绿、公会蓝、常规黄……和游戏里整行同色一致
    "body": {"family": "", "size": 0, "weight": "normal", "slant": "roman",
             "color": "", "bg": "", "border": 0, "follow_channel": True},
    "system": {"family": "", "size": 0, "weight": "normal", "slant": "roman",
               "color": "#e8e8e8", "bg": "", "border": 0, "follow_channel": False},
    "original": {"family": "", "size": -2, "weight": "normal", "slant": "roman",
                 "color": "#98a1ab", "bg": "", "border": 0, "follow_channel": False},
    "timestamp": {"family": "", "size": -2, "weight": "normal", "slant": "roman",
                  "color": "#98a1ab", "bg": "", "border": 0, "follow_channel": False},
}

TAG_OF_ELEMENT = {
    "timestamp": "meta",
    "name": "name",
    "body": "body",
    "system": "system",
    "original": "orig",
}


def element_spec(config: dict, element: str) -> dict:
    spec = dict(DEFAULTS.get(element, {}))
    appearance = config.get("appearance") or {}
    spec.update(appearance.get(element) or {})
    return spec


def resolve_font(config: dict, element: str) -> Tuple[str, int, str, str]:
    spec = element_spec(config, element)
    family = (spec.get("family") or "").strip() or config.get("font_family", "Microsoft YaHei")
    base = int(config.get("font_size", 11) or 11)
    try:
        size = int(spec.get("size", 0) or 0)
    except Exception:
        size = 0
    if size < 0:
        size = max(7, base + size)
    elif size == 0:
        size = base
    weight = (spec.get("weight") or "normal").strip() or "normal"
    slant = (spec.get("slant") or "roman").strip() or "roman"
    return family, size, weight, slant


def resolve_color(config: dict, element: str, channel: Optional[str] = None) -> str:
    # 频道前缀的颜色只认"频道颜色"里的按频道设置，不接受逐项里的统一覆盖，
    # 否则用户在两处设颜色会互相打架。
    if element == "channel":
        colors = config.get("channel_colors") or {}
        return colors.get(channel or "", "") or config.get("font_color", "#8ef58e")
    spec = element_spec(config, element)
    color = (spec.get("color") or "").strip()
    if color:
        return color
    if element == "body":
        return config.get("font_color", "#8ef58e")
    return "#e8e8e8"


def tag_options(config: dict, element: str, channel: Optional[str] = None) -> dict:
    spec = element_spec(config, element)
    options = {
        "font": resolve_font(config, element),
        "foreground": resolve_color(config, element, channel),
    }
    background = (spec.get("bg") or "").strip()
    if background:
        options["background"] = background
    try:
        border = int(spec.get("border", 0) or 0)
    except Exception:
        border = 0
    if border > 0:
        options["borderwidth"] = border
        options["relief"] = "solid"
    return options


def element_tag(config: dict, element: str, channel: Optional[str] = None) -> str:
    """返回该元素在某频道下应当使用的标签名。

    开了"跟随频道颜色"的元素，会为每个频道准备一个派生标签（如 body_小队），
    这样同一句译文在小队频道是绿色、在公会频道是蓝色 —— 和游戏里的整行同色一致。
    """
    base = TAG_OF_ELEMENT.get(element)
    if not base:
        if element == "channel":
            return ("channel_%s" % channel) if channel else "meta"
        return "body"
    if channel and element_spec(config, element).get("follow_channel"):
        return "%s_%s" % (base, channel)
    return base


def apply_widget_style(text_widget, config: dict) -> None:
    """文本框自身的底色/默认字色/基础字体。"""
    options = {
        "bg": config.get("bg_color", "#12141c"),
        "fg": config.get("font_color", "#8ef58e"),
        "font": resolve_font(config, "body"),
    }
    try:
        text_widget.configure(**options)
    except Exception:
        pass


def apply_tags(text_widget, config: dict) -> None:
    """把全部标签按当前配置配好（主窗口和预览都用这个）。"""
    for element, tag in TAG_OF_ELEMENT.items():
        try:
            text_widget.tag_configure(tag, **tag_options(config, element))
        except Exception:
            pass
        # 跟随频道颜色的元素：再为每个频道派生一份标签
        if element_spec(config, element).get("follow_channel"):
            for channel in (config.get("channel_colors") or {}):
                options = tag_options(config, element)
                options["foreground"] = resolve_color(config, "channel", channel)
                try:
                    text_widget.tag_configure("%s_%s" % (tag, channel), **options)
                except Exception:
                    pass

    # 出错提示：沿用"原文"的字体，但用醒目颜色
    warn_options = tag_options(config, "original")
    warn_options["foreground"] = "#ff8f8f"
    try:
        text_widget.tag_configure("warn", **warn_options)
    except Exception:
        pass

    for channel in (config.get("channel_colors") or {}):
        try:
            text_widget.tag_configure("channel_" + channel,
                                      **tag_options(config, "channel", channel))
        except Exception:
            pass
