"""频道表测试（可自定义：增删改、老配置迁移）。"""
from app import channels
from app.parser import ChatParser


def test_default_channels_have_no_team_channel():
    """游戏里没有"队伍"这个频道，默认表里就不该有它。"""
    names = [entry["name"] for entry in channels.DEFAULT_CHANNELS]
    assert "小队" in names
    assert "队伍" not in names
    assert "战利品" in names


def test_effective_migrates_legacy_dicts():
    """老配置只有两个字典时，要迁移成频道表（颜色/开关都保留）。"""
    config = {
        "channel_colors": {"小队": "#112233", "公会": "#445566", "战利品": "#778899"},
        "channels_enabled": {"小队": True, "公会": False, "战利品": False},
    }
    items = channels.effective(config)
    by_name = {entry["name"]: entry for entry in items}
    assert by_name["小队"]["color"] == "#112233"
    assert by_name["小队"]["enabled"] is True
    assert by_name["公会"]["color"] == "#445566"
    assert by_name["公会"]["enabled"] is False
    # 迁移结果要写回配置（界面和流水线都读这两个字典）
    assert config["channel_colors"]["小队"] == "#112233"
    assert config["channels_enabled"]["公会"] is False
    assert isinstance(config["channels"], list)


def test_legacy_team_channel_is_dropped():
    """老配置里如果有"队伍"，迁移时丢掉（游戏里并没有这个频道）。"""
    config = {
        "channel_colors": {"小队": "#22bb2e", "队伍": "#7ee787"},
        "channels_enabled": {"小队": True, "队伍": True},
    }
    names = channels.names(config)
    assert "小队" in names
    assert "队伍" not in names


def test_custom_channel_round_trip():
    """玩家自己加的频道要能存下来、能开关、能改色。"""
    config = {}
    items = channels.effective(config)
    items.append({"name": "团队", "color": "#a371f7", "enabled": True})
    channels.sync(config, items)
    assert "团队" in channels.names(config)
    assert channels.color_map(config)["团队"] == "#a371f7"
    assert channels.enabled_map(config)["团队"] is True

    # 再走一遍（模拟重启后读配置）也不能丢
    again = channels.effective(config)
    assert [entry["name"] for entry in again if entry["name"] == "团队"]


def test_apply_legacy_folds_edits_back():
    """在外观页改了颜色、在监控页改了开关，保存时要并回频道表。"""
    config = {}
    channels.effective(config)
    config["channel_colors"] = dict(config["channel_colors"], 小队="#ff0000")
    config["channels_enabled"] = dict(config["channels_enabled"], 公会=True)
    items = channels.apply_legacy(config)
    by_name = {entry["name"]: entry for entry in items}
    assert by_name["小队"]["color"] == "#ff0000"
    assert by_name["公会"]["enabled"] is True


def test_alias_table_recognises_custom_channel():
    """自定义频道名要能被解析器认出来。"""
    config = {"channels": [{"name": "团队", "color": "#a371f7", "enabled": True}]}
    table = channels.alias_table(config)
    assert table.get("团队") == "团队"
    events = ChatParser(table).parse(["(团队): [团队] Alice: hello there"])
    assert events[0].kind == "chat"
    assert events[0].channel == "团队"


def test_short_name_trims_long_names():
    assert channels.short_name("小队") == "小队"
    assert channels.short_name("战利品") == "战利品"
    assert channels.short_name("悄悄话频道") == "悄悄"


def test_pipeline_only_shows_configured_channels():
    """关掉的频道不显示；认不出来的频道不隐藏（fail-open，别让更新后的聊天消失）。"""
    from app.pipeline import Pipeline

    assert not Pipeline.is_panel_text("(小队):[小队] Alice: hi there")

    def enabled_for(config, channel):
        return channels.enabled_map(config).get(channel, True)

    config = {}
    channels.effective(config)
    assert enabled_for(config, "战利品") is False
    assert enabled_for(config, "小队") is True
    assert enabled_for(config, "还没登记的频道") is True


def test_default_config_has_no_channel_list():
    """回归：默认配置里**不能**预先塞一个 channels 列表。

    踩过的坑：默认配置带了列表以后，effective() 看到"已有列表"就不会去读用户
    旧配置里的 channels_enabled —— 用户关掉的频道会被默认值（全开）冲掉。
    """
    from app.config import DEFAULT_CONFIG

    assert "channels" not in DEFAULT_CONFIG


def test_load_config_migrates_user_switches():
    """真正走一遍 load_config：用户关掉的频道必须活下来。"""
    import json
    import tempfile
    from pathlib import Path

    from app import config as config_module
    from app import paths

    folder = Path(tempfile.mkdtemp(prefix="ddo_cfg_"))
    file_path = folder / "config.json"
    file_path.write_text(json.dumps({
        "channel_colors": {"小队": "#112233", "公会": "#445566"},
        "channels_enabled": {"小队": True, "公会": False, "战利品": False},
    }, ensure_ascii=False), encoding="utf-8")

    original = paths.CONFIG_PATH
    paths.CONFIG_PATH = file_path
    try:
        config = config_module.load_config()
    finally:
        paths.CONFIG_PATH = original

    enabled = channels.enabled_map(config)
    assert enabled["小队"] is True
    assert enabled["公会"] is False            # 关键：用户关掉的不能被冲成开
    assert channels.color_map(config)["小队"] == "#112233"
    assert "队伍" not in enabled
