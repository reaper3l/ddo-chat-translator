"""外观样式解析测试。"""
from app.config import DEFAULT_CONFIG
from app.ui import style
from app.ui import theme


def test_mix_blends_two_colors():
    """频道小灯"变暗"用的颜色混合（theme.mix）。"""
    assert theme.mix("#000000", "#ffffff", 0.5) == "#808080"
    assert theme.mix("#ffffff", "#000000", 0.0) == "#ffffff"
    assert theme.mix("#ffffff", "#000000", 1.0) == "#000000"
    assert theme.mix("#ff0000", "#0000ff", 1.0) == "#0000ff"
    # 比例越界要夹住，不认识的色值原样返回（不能把界面颜色搞崩）
    assert theme.mix("#ffffff", "#000000", 5) == "#000000"
    assert theme.mix("red", "#000000", 0.5) == "red"


def _config() -> dict:
    config = dict(DEFAULT_CONFIG)
    config["appearance"] = {key: dict(value)
                            for key, value in DEFAULT_CONFIG["appearance"].items()}
    config["channel_colors"] = dict(DEFAULT_CONFIG["channel_colors"])
    return config


def test_channel_color_only_comes_from_channel_colors():
    """频道前缀的颜色只能来自"频道颜色"，逐项里的统一色不再生效，
    否则两处设置会打架、也看不出谁生效。"""
    config = _config()
    config["channel_colors"]["小队"] = "#112233"
    config["appearance"]["channel"]["color"] = "#ff0000"      # 老的残留值
    assert style.resolve_color(config, "channel", "小队") == "#112233"


def test_per_element_font_and_color_override():
    config = _config()
    config["font_family"] = "Microsoft YaHei"
    config["font_size"] = 12
    config["appearance"]["name"].update({
        "family": "SimSun", "size": 6, "weight": "normal",
        "slant": "italic", "color": "#123456",
    })
    assert style.resolve_font(config, "name") == ("SimSun", 6, "normal", "italic")
    assert style.resolve_color(config, "name") == "#123456"


def test_body_falls_back_to_global_defaults():
    config = _config()
    assert style.resolve_font(config, "body") == ("Microsoft YaHei", 11, "normal", "roman")
    assert style.resolve_color(config, "body") == config["font_color"]


def test_negative_size_is_relative_to_base():
    config = _config()
    config["font_size"] = 14
    config["appearance"]["original"] = {"size": -3}
    assert style.resolve_font(config, "original")[1] == 11


def test_border_and_background_options():
    config = _config()
    config["appearance"]["body"].update({"bg": "#000000", "border": 2})
    options = style.tag_options(config, "body")
    assert options["background"] == "#000000"
    assert options["borderwidth"] == 2
    assert options["relief"] == "solid"


def test_zero_border_means_no_border():
    config = _config()
    options = style.tag_options(config, "body")
    assert "borderwidth" not in options and "background" not in options
