"""高频短语挖掘：本地统计要准、模型回复要能解析、垃圾词组要被挡住。"""
from app import phrases


def _pairs():
    return [
        ("omw, see you there", "马上到，那里见"),
        ("need heals for shroud", "需要治疗 for 幽影堡"),
        ("on my way now", "我现在在路上"),
        ("i am on my way", "我马上到"),
        ("on my way to the shrine", "在去神龛的路上"),
        ("ok", "好"),
    ]


def test_mine_keeps_repeated_phrases_and_keeps_them_distinct():
    found = phrases.mine(_pairs(), min_count=3)
    keys = [item["phrase"] for item in found]
    assert "on my way" in keys
    # 被更长的高频词组包住的子词组不该重复出现
    assert "my way" not in keys and "way" not in keys
    top = found[0]
    assert top["phrase"] == "on my way" and top["count"] == 3
    assert top["samples"]                       # 带例句，方便人判断


def test_mine_skips_low_frequency_and_all_stopword_phrases():
    found = phrases.mine(_pairs(), min_count=3)
    keys = {item["phrase"] for item in found}
    assert "see you" not in keys                # 只出现 1 次
    assert "need heals" not in keys             # 只有 1 句里有
    # 全是停用词的组合（"i am" / "to the"）不收
    assert not any(item["phrase"] == "i am" for item in found)


def test_mine_respects_known_terms():
    known = ["on my way", "need heals"]
    keys = {item["phrase"] for item in phrases.mine(_pairs(), known=known, min_count=3)}
    assert "on my way" not in keys              # 已经在术语表里了
    assert "need heals" not in keys
    # 更长的词组已收录时，它的子词组也不该冒出来（实测：on my way 已内置，
    # 结果候选里只剩没用的 "my way"）
    assert "my way" not in keys


def test_mine_counts_each_sentence_once():
    """同一句里把同一个词组说了三遍，也只算一句（否则一句话就能刷到"高频"）。"""
    pairs = [("omw omw omw now see you later", "马上到")] * 3
    assert phrases.mine(pairs, min_count=4) == []      # 3 句 < 门槛 4
    found = phrases.mine(pairs, min_count=3)           # 3 句才算够
    assert found and all(item["count"] == 3 for item in found)


def test_pairs_from_cache_and_memory():
    cache = {"a": {"src": "need heals", "zh": "需要治疗"},
             "b": {"src": "", "zh": "空"},
             "c": {"src": "pop side", "zh": "pop side"},      # 译文没中文 → 丢
             "d": "老格式（只有译文）"}
    assert phrases.pairs_from_cache(cache) == [("need heals", "需要治疗")]

    class _Memory:
        data = {"phrases": {"fp": {"source": "rez plz", "zh": "复活我"}}}

    assert phrases.pairs_from_memory(_Memory()) == [("rez plz", "复活我")]


def test_parse_reply_accepts_good_lines_and_drops_bad_ones():
    items = ["omw", "on my way", "need heals"]
    reply = "1. 马上到\n2. 在路上\n3) 需要治疗\n4. 多余的\n乱七八糟\n3. 重复的"
    parsed = phrases.parse_reply(reply, items)
    # 序号超范围的（4.）、没有序号的（乱七八糟）都丢掉；
    # 同一个序号出现两次时后出现的胜（模型一般不会这样，真这样时按最后一行为准）
    assert parsed == {"omw": "马上到", "on my way": "在路上", "need heals": "重复的"}
    assert phrases.parse_reply("1. 马上到", items) == {"omw": "马上到"}


def test_parse_reply_rejects_english_or_empty_gloss():
    items = ["omw", "rez plz"]
    assert phrases.parse_reply("1. omw\n2. 复活我", items) == {"rez plz": "复活我"}
    assert phrases.parse_reply("1.\n2. 复活我", items) == {"rez plz": "复活我"}
    assert phrases.parse_reply("", items) == {}


def test_build_messages_is_one_request_for_all_phrases():
    messages = phrases.build_messages(["omw", "on my way"])
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["content"].splitlines() == ["1. omw", "2. on my way"]
