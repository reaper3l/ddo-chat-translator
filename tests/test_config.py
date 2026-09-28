"""配置默认值测试。

设置窗口里的勾选框直接读 `config[key]`，所以**每个开关都必须在 DEFAULT_CONFIG 里**
有默认值：少了的话窗口会显示成"没勾上"，用户一按保存就把功能关掉了
（v3.0.6 开发时就踩过这个坑：`system_whitelist` 漏在默认配置外面）。
"""
from app.config import DEFAULT_CONFIG, load_config
from app.pipeline import Pipeline


def test_filter_switch_is_on_by_default():
    assert DEFAULT_CONFIG["system_whitelist"] is True
    assert DEFAULT_CONFIG["show_system"] is True


def test_boolean_switches_used_by_settings_exist():
    switches = (
        "show_system", "system_whitelist", "show_notes", "show_original",
        "show_timestamp", "always_on_top", "frameless", "toolbar_icons_only",
        "toolbar_collapsed", "show_status_bar", "use_glossary",
        "use_extra_glossary", "skip_identical_frame", "band_ocr",
        "low_priority", "idle_backoff", "merge_same_row", "ui_animation",
    )
    for key in switches:
        assert key in DEFAULT_CONFIG, "默认配置缺少开关：%s" % key
        assert isinstance(DEFAULT_CONFIG[key], bool), key


def test_values_used_by_settings_widgets_exist():
    for key in ("interval_ms", "ocr_threads", "ocr_upscale", "dedup_ttl_seconds",
                "context_turns", "timeout_seconds", "learn_min_count",
                "candidate_min_count", "max_lines", "transparency_mode", "alpha",
                "engine", "translate_mode", "capture_backend", "ui_scale"):
        assert key in DEFAULT_CONFIG, "默认配置缺少设置项：%s" % key


def test_load_config_starts_from_defaults():
    """用户的 config.json 是"增量覆盖"，任何默认项都不该因为用户文件而消失。"""
    config = load_config()
    for key in DEFAULT_CONFIG:
        assert key in config, key
    assert Pipeline.is_useful_notice("你加入了某某的队伍")
    assert not Pipeline.is_useful_notice("宝箱信息：被拾取次数 1")
