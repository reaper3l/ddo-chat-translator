"""频道定义：名字、颜色、是否显示 —— 全部由配置驱动，玩家可以自己增删改。

为什么不在代码里写死：玩家反馈游戏里其实没有"队伍"这个频道，而且游戏更新时
频道名可能变、也可能多出新频道。写死就只能等程序更新，所以把频道表放进配置：

    "channels": [
        {"name": "小队", "color": "#22bb2e", "enabled": true},
        ...
    ]

兼容旧配置：以前只有两个字典（`channel_colors` 配色、`channels_enabled` 开关）。
`effective()` 会在缺少 `channels` 时把它们迁移成新列表；`sync()` 再把列表写回
这两个字典 —— 界面（外观页的配色格子）和流水线都还在读它们，这样就不用大改。
"""
from __future__ import annotations

from typing import Dict, List

from .parser import alias_table_for

# 默认频道表（按游戏里实际的频道；**没有"队伍"** —— 游戏里不存在这个频道）
# 字段说明：
#   name    频道名（要和游戏里显示的一致）
#   color   颜色（翻译窗口里的前缀/正文色 + 工具条收起时那盏小灯的颜色）
#   enabled 这个频道显不显示、翻不翻译
#   strip   工具条收起时，要不要在这一排小灯里显示它（用户可自定义，默认都显示）
DEFAULT_CHANNELS: List[Dict[str, object]] = [
    {"name": "小队", "color": "#22bb2e", "enabled": True},
    {"name": "公会", "color": "#79c0ff", "enabled": True},
    {"name": "常规", "color": "#ffd866", "enabled": True},
    {"name": "公共", "color": "#ffa657", "enabled": True},
    {"name": "悄悄话", "color": "#ff9ecd", "enabled": True},
    {"name": "战利品", "color": "#a9b1ba", "enabled": False},
]

# 以前版本里有过、但游戏里并没有的频道：迁移时直接丢掉（需要的话可以在
# 设置 → 频道 里自己加回来）。
# 注意只丢"队伍"这一个名字 —— "团队/组队"是解析器里"队伍"的别名，
# 玩家真要自己建一个叫"团队"的频道也不该被吃掉。
DROPPED_CHANNELS = ("队伍",)

FALLBACK_COLOR = "#8b949e"


def short_name(name: str) -> str:
    """小灯上的短标签：不超过 3 个字就原样，超了就取前两个字。"""
    text = (name or "").strip()
    return text if len(text) <= 3 else text[:2]


def _clean_entry(entry) -> Dict[str, object] | None:
    if not isinstance(entry, dict):
        return None
    name = str(entry.get("name", "")).strip()
    if not name:
        return None
    color = str(entry.get("color") or "").strip() or FALLBACK_COLOR
    return {"name": name, "color": color,
            "enabled": bool(entry.get("enabled", True)),
            "strip": bool(entry.get("strip", True))}


def effective(config: dict) -> List[Dict[str, object]]:
    """当前生效的频道列表（缺 `channels` 时从旧配置迁移，并写回配置）。"""
    items: List[Dict[str, object]] = []
    raw = config.get("channels")
    if isinstance(raw, list):
        for entry in raw:
            cleaned = _clean_entry(entry)
            if cleaned and cleaned["name"] not in DROPPED_CHANNELS:
                items.append(cleaned)

    if not items:
        colors = config.get("channel_colors") or {}
        enabled = config.get("channels_enabled") or {}
        for entry in DEFAULT_CHANNELS:
            name = str(entry["name"])
            items.append({
                "name": name,
                "color": str(colors.get(name) or entry["color"]),
                "enabled": bool(enabled.get(name, entry["enabled"])),
                "strip": True,
            })
        # 旧配置里额外自定义过、且不在默认表里的频道也保留下来
        known = {item["name"] for item in items}
        for name, color in colors.items():
            name = str(name).strip()
            if not name or name in known or name in DROPPED_CHANNELS:
                continue
            items.append({"name": name, "color": str(color) or FALLBACK_COLOR,
                          "enabled": bool(enabled.get(name, True)), "strip": True})

    sync(config, items)
    return items


def sync(config: dict, items: List[Dict[str, object]]) -> None:
    """把频道列表写回配置，并同步两个旧字典（界面和流水线还在读它们）。"""
    cleaned = [entry for entry in (_clean_entry(item) for item in items) if entry]
    config["channels"] = cleaned
    config["channel_colors"] = {entry["name"]: entry["color"] for entry in cleaned}
    config["channels_enabled"] = {entry["name"]: bool(entry["enabled"])
                                  for entry in cleaned}


def apply_legacy(config: dict) -> List[Dict[str, object]]:
    """把旧字典（外观页改的颜色、监控页改的开关）合并进频道列表。

    设置窗口保存时调用一次，这样无论用户是在"频道"页改的，还是在"外观/监控"页
    改的，最后都落到同一份列表上。
    """
    # 先把旧字典**抄一份**：下面的 effective() 会按列表重写它们（迁移逻辑），
    # 先读后写才不会把用户刚改的颜色/开关冲掉。
    colors = dict(config.get("channel_colors") or {})
    enabled = dict(config.get("channels_enabled") or {})
    items = effective(config)
    for entry in items:
        name = str(entry["name"])
        color = str(colors.get(name) or "").strip()
        if color:
            entry["color"] = color
        if name in enabled:
            entry["enabled"] = bool(enabled[name])
    sync(config, items)
    return items


def names(config: dict) -> List[str]:
    return [str(entry["name"]) for entry in effective(config)]


def enabled_map(config: dict) -> Dict[str, bool]:
    return {str(entry["name"]): bool(entry["enabled"]) for entry in effective(config)}


def color_map(config: dict) -> Dict[str, str]:
    return {str(entry["name"]): str(entry["color"]) for entry in effective(config)}


def strip_map(config: dict) -> Dict[str, bool]:
    """哪些频道要出现在工具条收起时的那排小灯里（用户可自己选）。"""
    return {str(entry["name"]): bool(entry.get("strip", True))
            for entry in effective(config)}


def alias_table(config: dict) -> Dict[str, str]:
    """给解析器用的别名表：识别跟着「设置 → 频道」走。

    内置的 OCR 错字别名只对表里存在的频道生效，玩家自定义的频道名也一起认。
    """
    user = config.get("prefix_aliases")
    return alias_table_for(names(config), user if isinstance(user, dict) else None)
