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


# ---------------------------------------------------------------------------
# same_ocr_message：专治"同一句被 OCR 读成几种写法"
# 用的是用户实测日志里的原句。
# ---------------------------------------------------------------------------

def test_same_ocr_message_catches_variants():
    pairs = [
        ("lgotone-shotbyittoday", "|gotone-shotbyittoday"),
        ("lgotone-shotbyittoday", "Igotone-shotbyittoday"),
        ("100ScrollsofResurrection", "1ooScrollsofResurrection"),
        ("100ScrollsofResurrection", "100S ScrollsofResurrection"),
        ("headingtoEyeareanow", "headingtoE Eye areanow"),
        ("everyoneknows roguesare expendable",
         "everyone knows roguesaree expendable"),
        # 长句被读短了（尾巴丢了）
        ("there is something like thisinArtofWar", "there is something like his"),
    ]
    for first, second in pairs:
        assert textutil.same_ocr_message(first, second), (first, second)
        assert textutil.same_ocr_message(second, first), (first, second)


def test_same_ocr_message_keeps_different_texts_apart():
    """两句完全不同的话不能被判成同一条（英文只有 26 个字母，集合相似度会误判）。"""
    pairs = [
        ("need heals for shroud", "pull the lever please"),
        ("welcome back everyone", "heading to the shrine now"),
        ("i got one shot by it today", "there is something like this in art of war"),
        ("yes", "in"),
        ("omw", "ty"),
        ("need heals", "need heals fast"),          # 真的追加了内容
        ("ok restart", "ok"),
    ]
    for first, second in pairs:
        assert not textutil.same_ocr_message(first, second), (first, second)


def test_ocr_similar_is_order_sensitive():
    """集合相似度给高分、顺序相似度必须给低分的情况。"""
    a = "please wait for me i need to repair my gear"
    b = "my dear repair man ate seven pears on the road"
    assert textutil.similar(textutil.fingerprint(a), textutil.fingerprint(b)) > 0.6
    assert textutil.ocr_similar(a, b) < 0.6


def test_detect_direction_for_manual_translation():
    """手动翻译窗口靠它决定翻哪边（中→英 / 英→中）。"""
    assert textutil.detect_direction("马上到，等我一下") == "zh2en"
    assert textutil.detect_direction("omw, be right there") == "en2zh"
    assert textutil.detect_direction("") == "en2zh"          # 空输入按英文兜底
    assert textutil.detect_direction("halo nihao") == "en2zh"   # 拼音也当英文（翻成中文）
    assert textutil.detect_direction("我要 go to 市场") == "zh2en"  # 中文为主
    assert textutil.detect_direction("hello there 好") == "en2zh"   # 字母为主


def test_resolve_direction_can_be_forced():
    """窗口里的"方向"下拉：自动 / 强制翻成英文 / 强制翻成中文。"""
    assert textutil.resolve_direction("omw", "auto") == "en2zh"
    assert textutil.resolve_direction("马上到", "auto") == "zh2en"
    # 强制之后，内容是什么都不改方向（用户说了算）
    assert textutil.resolve_direction("马上到", "en2zh") == "en2zh"
    assert textutil.resolve_direction("omw", "zh2en") == "zh2en"
    assert textutil.resolve_direction("", "zh2en") == "zh2en"
