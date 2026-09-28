"""「根据上下文推荐回复」的输出解析测试（纯逻辑，不联网）。"""
from app.prompt import build_reply_messages, build_reply_system_prompt
from app.replies import parse_suggestions


def test_parses_pipe_lines():
    text = "好的，我马上过去 | omw\n等一下 | sec plz\n不客气 | np"
    assert parse_suggestions(text) == [("好的，我马上过去", "omw"),
                                       ("等一下", "sec plz"),
                                       ("不客气", "np")]


def test_parses_numbered_and_decorated_lines():
    """模型常加编号 / Markdown / 小标题，都要认。"""
    text = ("1. 中文：好的 | 英文：ok\n"
            "2) **我在红门** | **im at red door**\n"
            "- 我马上到 → omw\n")
    pairs = parse_suggestions(text)
    assert ("好的", "ok") in pairs
    assert ("我在红门", "im at red door") in pairs
    assert ("我马上到", "omw") in pairs


def test_parses_json_array():
    text = '[{"zh": "谢谢", "en": "ty"}, {"中文": "稍等", "英文": "sec"}]'
    assert parse_suggestions(text) == [("谢谢", "ty"), ("稍等", "sec")]


def test_skips_junk_lines_and_duplicates():
    text = ("```\n"
            "好的 | ok\n"
            "好的 | ok\n"
            "英文那半不是英文 | 中文\n"
            "这里没有分隔符\n"
            "```\n")
    assert parse_suggestions(text) == [("好的", "ok")]


def test_empty_input():
    assert parse_suggestions("") == []
    assert parse_suggestions("模型什么都没说") == []


def test_reply_messages_include_context_and_draft():
    context = [("Sinoke", "you coming?", "你要来吗？"),
               ("Guihuo", "omw", "马上到")]
    messages = build_reply_messages(build_reply_system_prompt(), context,
                                    ["你的队友 X 已死亡"], "我马上到，等我")
    assert messages[0]["role"] == "system"
    user = messages[-1]["content"]
    assert "Sinoke: you coming? → 你要来吗？" in user
    assert "你的队友 X 已死亡" in user
    assert "我马上到，等我" in user
    # 提示词里要交代输出格式（一行一条"中文 | English"）
    assert "中文 | English" in messages[0]["content"]


def test_reply_messages_without_context():
    messages = build_reply_messages(build_reply_system_prompt(), [], [])
    assert "没有采集到内容" in messages[-1]["content"]
