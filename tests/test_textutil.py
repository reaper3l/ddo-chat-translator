"""文本工具测试。"""
from app import textutil


def test_fingerprint_ignores_punctuation_and_case():
    assert textutil.fingerprint("Hello, World!") == "helloworld"
    assert textutil.fingerprint("HELLO   world") == "helloworld"


def test_is_noise():
    assert textutil.is_noise("...")
    assert textutil.is_noise("·")
    assert textutil.is_noise("")
    assert not textutil.is_noise("hi there")
    assert not textutil.is_noise("你好")


def test_clean_body_strips_wrapping():
    assert textutil.clean_body(": hello there") == "hello there"
    assert textutil.clean_body("hello (") == "hello"


def test_cjk_helpers():
    assert textutil.has_cjk("abc你好")
    assert textutil.has_latin("abc")
    assert textutil.cjk_ratio("你好世界") > 0.9
    assert textutil.cjk_ratio("abcdef") == 0.0


def test_url_protect_and_restore():
    text = "look https: //store.steampowered.com/app/1 ok"
    protected, urls = textutil.protect_urls(text)
    assert "{{URL_0}}" in protected
    assert "steampowered" not in protected
    assert textutil.restore_urls(protected, urls) == "look https://store.steampowered.com/app/1 ok"


def test_leftover_marks_are_stripped():
    assert textutil.strip_leftover_marks("中文 {{TERM_9}} 结尾") == "中文  结尾"


def test_polish_removes_spaces_between_chinese():
    assert textutil.polish_translation("呃 进不去") == "呃进不去"
    assert textutil.polish_translation("好 所以我做 死亡 哈哈") == "好所以我做死亡哈哈"
    assert textutil.polish_translation("如果你放弃任务遗忘洞穴然后共享给你，那就能进精英难度了") \
        == "如果你放弃任务遗忘洞穴然后共享给你，那就能进精英难度了"


def test_polish_converts_ascii_punctuation_after_chinese():
    assert textutil.polish_translation("精英难度 对吧?") == "精英难度对吧？"
    assert textutil.polish_translation("兄弟们,你们在 Steam 上玩别的游戏吗?") \
        == "兄弟们，你们在 Steam 上玩别的游戏吗？"


def test_polish_keeps_emoticon_and_latin_spacing():
    assert textutil.polish_translation("祝好运 :)") == "祝好运 :)"
    assert textutil.polish_translation("我从没死过 haha") == "我从没死过 haha"


def test_collapse_doubled():
    assert textutil.collapse_doubled("位面监狱位面监狱") == "位面监狱"
    # 正常口语不能被误伤
    assert textutil.collapse_doubled("ok ok") == "ok ok"
    assert textutil.collapse_doubled("hahaha") == "hahaha"
    assert textutil.collapse_doubled("hi") == "hi"


def test_is_refusal_only_catches_model_meta_talk():
    # 模型插话（说明看不懂），要当"没翻出来"处理
    assert textutil.is_refusal("（看不清楚）")
    assert textutil.is_refusal("[看不清]")
    assert textutil.is_refusal("抱歉，我无法翻译这句。")
    assert textutil.is_refusal("原文是乱码，无法识别")
    # 正常译文不能被误判
    assert not textutil.is_refusal("看不清楚")          # can't see clearly 的正常译法
    assert not textutil.is_refusal("抱歉")              # sorry 的正常译法
    assert not textutil.is_refusal("我不懂")
    assert not textutil.is_refusal("马上到")
