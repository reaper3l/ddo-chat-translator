"""提示词：聊天翻译 / 中译英 / 英译中 三套各自该有的要求。"""
from app.prompt import (build_en2zh_system_prompt, build_zh2en_system_prompt,
                        build_reply_system_prompt)


def test_zh2en_prompt_asks_for_game_english():
    text = build_zh2en_system_prompt()
    assert "中文" in text and "英文" in text
    for token in ("OMW", "BRB", "LFM", "只输出英文"):
        assert token in text, token


def test_en2zh_prompt_asks_for_chinese():
    text = build_en2zh_system_prompt()
    for token in ("只输出中文", "OMW", "LFM", "不要音译"):
        assert token in text, token
    # 两套提示词不能是同一份（方向不同）
    assert text != build_zh2en_system_prompt()


def test_reply_prompt_keeps_bilingual_output():
    text = build_reply_system_prompt()
    assert "中文" in text and "英文" in text


class _Memory:
    """最小号的 memory 替身：只需要 prompt_terms。"""

    def prompt_terms(self, limit: int = 25):
        return [{"text": "shrine", "zh": "神龛"}]


def test_prompts_include_my_terms_both_ways():
    zh2en = build_zh2en_system_prompt(_Memory())
    assert "神龛=shrine" in zh2en             # 中文→英文：按"我的说法"输出英文
    en2zh = build_en2zh_system_prompt(_Memory())
    assert "shrine=神龛" in en2zh             # 英文→中文：按"我的译法"输出中文
