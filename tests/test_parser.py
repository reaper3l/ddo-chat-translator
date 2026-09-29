"""聊天行解析测试，用的都是这个项目真实遇到过的 OCR 样例。"""
from app.parser import ChatParser


def _chats(lines):
    return [event for event in ChatParser().parse(lines) if event.is_chat]


def test_single_prefix_chat():
    events = ChatParser().parse(["(常规)Alice: OMW, running to the quest now"])
    assert len(events) == 1
    event = events[0]
    assert event.kind == "chat"
    assert event.channel == "常规"
    assert event.speaker == "Alice"
    assert event.text == "OMW, running to the quest now"


def test_double_prefix_chat():
    events = ChatParser().parse(["(小队):[小队] Sckham: Guys, do you play other"])
    assert events[0].kind == "chat"
    assert events[0].channel == "小队"
    assert events[0].speaker == "Sckham"
    assert events[0].text == "Guys, do you play other"


def test_continuation_line_merges():
    lines = ["(小队):[小队] Sckham: Guys, do you play other", "games on Steam?"]
    events = ChatParser().parse(lines)
    assert len(events) == 1
    assert events[0].text == "Guys, do you play other games on Steam?"


def test_two_players_split_into_two_messages():
    lines = [
        "(小队):[小队] Favortank: wow",
        "(小队):[小队] Sckham: If you can add it to wishlist",
    ]
    chats = _chats(lines)
    assert len(chats) == 2
    assert chats[0].speaker == "Favortank"
    assert chats[1].speaker == "Sckham"
    assert chats[1].text == "If you can add it to wishlist"


def test_system_message_without_prefix():
    events = ChatParser().parse(["Grelik加入了你的队伍"])
    assert events[0].kind == "system"
    assert events[0].text == "Grelik加入了你的队伍"


def test_system_message_with_channel_prefix():
    events = ChatParser().parse(["(小队) Grelik加入了你的队伍"])
    assert events[0].kind == "system"
    assert events[0].channel == "小队"


def test_loot_line_is_dropped():
    events = ChatParser().parse(["[战利品] 你获得了 100 金币"])
    # 战利品行现在按游戏原样显示（系统消息），不再丢弃
    assert events[0].kind == "system"
    assert events[0].channel == "战利品"


def test_real_game_line_with_space_between_prefixes():
    """真实截图里的格式：(小队): [小队] 名字: 正文（两个前缀中间有空格）。"""
    events = ChatParser().parse(
        ["(小队): [小队] Sckham: Guys, do you play other games on Steam?"])
    assert len(events) == 1
    event = events[0]
    assert event.kind == "chat"
    assert event.channel == "小队"
    assert event.speaker == "Sckham"
    assert event.text == "Guys, do you play other games on Steam?"
    assert event.prefix_text == "(小队): [小队] "


def test_real_party_join_line_is_system():
    events = ChatParser().parse(["(小队): [小队]    Kendra Estleton 加入了你的队伍。"])
    assert events[0].kind == "system"
    assert events[0].channel == "小队"
    assert "加入了你的队伍" in events[0].text


def test_real_disconnect_line_is_system():
    events = ChatParser().parse(["(小队): [小队] Dorqeth 已断线。"])
    assert events[0].kind == "system"
    assert events[0].channel == "小队"


def test_real_teammate_death_line_is_system():
    events = ChatParser().parse(["(小队):   你的队友Kendra Estleton已死亡。"])
    assert events[0].kind == "system"
    assert events[0].channel == "小队"
    assert events[0].prefix_text == "(小队): "


def test_real_loot_line_is_system_with_channel():
    events = ChatParser().parse(["(战利品): Dorqeth 将 Jeweled Key 从 宝箱 中取出。"])
    assert events[0].kind == "system"
    assert events[0].channel == "战利品"


def test_url_on_next_line_merges_into_chat():
    lines = ["(小队): [小队] Sckham:",
             "https://store.steampowered.com/app/3615640/Reborn_by_Fire/"]
    events = ChatParser().parse(lines)
    assert len(events) == 1
    assert events[0].kind == "chat"
    assert events[0].speaker == "Sckham"
    assert "store.steampowered.com" in events[0].text


def test_wrapped_player_line_keeps_prefix_of_first_line():
    lines = ["(小队): [小队] Sckham: I'm a game programmer developing a game",
             "that's a mix of DDO and a Souls-like, but single-player."]
    events = ChatParser().parse(lines)
    assert len(events) == 1
    assert events[0].prefix_text == "(小队): [小队] "
    assert events[0].text.startswith("I'm a game programmer")
    assert events[0].text.endswith("but single-player")   # 句末标点会被清掉


def test_merged_line_is_split_into_messages():
    """OCR 把两条聊天挤进同一个框（空格还丢了）时要能拆回两条。"""
    events = ChatParser().parse(
        ["(小队):[小队]Dorqeth:gi(小队):[小队]Dorqeth:eliteright?"])
    chats = [event for event in events if event.is_chat]
    assert len(chats) == 2
    assert chats[0].text == "gi"
    assert chats[1].text == "eliteright?"
    assert chats[0].prefix_text == "(小队):[小队]"


def test_merged_system_line_is_split_too():
    events = ChatParser().parse(["(小队):你现在是队长。   (小队):Dorqeth已断线。"])
    systems = [event for event in events if event.kind == "system"]
    assert len(systems) == 2
    assert systems[0].text.startswith("你现在是队长")
    assert systems[1].text.startswith("Dorqeth已断线")


def test_semicolon_after_channel_is_tolerated():
    """OCR 有时把 (小队): 读成 (小队);"""
    events = ChatParser().parse(["(小队);[小队]Dorqeth:gi"])
    assert events[0].kind == "chat"
    assert events[0].speaker == "Dorqeth"
    assert events[0].channel == "小队"


def test_missing_open_bracket_is_repaired():
    chats = _chats(["小队）Sckham: hello there"])
    assert len(chats) == 1
    assert chats[0].channel == "小队"
    assert chats[0].speaker == "Sckham"
    assert chats[0].text == "hello there"


def test_noise_lines_are_dropped():
    events = ChatParser().parse(["...", "·", "-"])
    assert events and all(event.kind == "drop" for event in events)


def test_speaker_without_prefix_inherits_channel():
    chats = _chats(["(常规)Alice: hello there", "Bob: hi there"])
    assert len(chats) == 2
    assert chats[1].speaker == "Bob"
    assert chats[1].channel == "常规"


def test_ocr_channel_variants():
    for raw, expected in (("小", "小队"), ("寸队", "小队"), ("①队", "小队"),
                          ("水队", "小队"), ("公会", "公会"), ("常规", "常规")):
        events = ChatParser().parse(["( %s )Alice: this is a test line" % raw])
        assert events[0].channel == expected, (raw, events[0].channel)


# ---------------------------------------------------------------------------
# 下面这些样例全部来自用户实测贴回来的日志（2026-09-27 那一批）。
# 它们曾经被"系统消息关键词白名单"整条丢掉，玩家真正的发言就这么没了。
# ---------------------------------------------------------------------------

def test_icon_noise_before_name_still_counts_as_chat():
    """OCR 把等级/图标读进名字前面（"S Sinoke:" / "9 Guihuo:" / "V Warzar:"）。"""
    cases = [
        ("(小队):[小队]S Sinoke:guihuo,nikyireddoor (小队)", "Sinoke",
         "guihuo,nikyireddoor"),
        ("(小队):[小队]9 Guihuo:in", "Guihuo", "in"),
        ("(小队):小队]V Warzar:petforthedoor?", "Warzar", "petforthedoor?"),
    ]
    for line, speaker, body in cases:
        chats = _chats([line])
        assert len(chats) == 1, line
        assert chats[0].speaker == speaker, line
        assert chats[0].text == body, (line, chats[0].text)


def test_doubled_close_bracket_is_repaired():
    """OCR 把右括号读成 "))"，不修的话整条玩家发言会变成系统消息被丢掉。"""
    chats = _chats(["(小队)):[小队]Sinoke:yes"])
    assert len(chats) == 1
    assert chats[0].speaker == "Sinoke"
    assert chats[0].text == "yes"


def test_missing_open_bracket_on_second_prefix_splits_line():
    """第二条消息丢了左括号（"(…错误):你的队友…"），要拆出来别粘在正文后面。"""
    events = ChatParser().parse(
        ["(小队):[小队]Guihuo: 堡垒? 错误):你的队友已经锁定了冒险难度"])
    chats = [event for event in events if event.is_chat]
    systems = [event for event in events if event.kind == "system"]
    assert [event.text for event in chats] == ["堡垒?"]
    assert len(systems) == 1
    assert "锁定了冒险难度" in systems[0].text


def test_body_repeated_twice_is_collapsed():
    """OCR 会把同一条贴两遍（实测 "位面监狱位面监狱"）。"""
    chats = _chats(["(小队):[小队]Sinoke: 位面监狱位面监狱"])
    assert chats[0].text == "位面监狱"


def test_loot_panel_lines_are_not_player_chat():
    """战利品/宝箱面板（含各种 OCR 错字版本）不能被当成玩家发言。"""
    lines = [
        "(聊天): 战利品:vyarzar舟",
        "(聊天): (战利品:你舟iron1Key从玉相取正",
        "(聊天): (战利丽:imoke舟ialesorvaior从玉相中取正",
        "(利a):im1oke付+4vatcn PhysicalResistance5从宝箱",
        "(聊天): 玉相你寸里且时间:0天19时2刀5/秒",
    ]
    for line in lines:
        assert _chats([line]) == [], line


def test_system_notice_glued_to_chat_body_is_split_out():
    """OCR 把系统提示粘在玩家正文后面时：正文归正文，提示归提示。

    实测："笑死 Imao的队友Guihuo已死亡" —— 以前整条会被当成玩家发言翻掉。
    """
    events = ChatParser().parse(["(小队):[小队]Guihuo: 笑死 Imao的队友Guihuo已死亡"])
    chats = [event for event in events if event.is_chat]
    systems = [event for event in events if event.kind == "system"]
    assert [event.text for event in chats] == ["笑死 Imao"]
    assert len(systems) == 1
    assert "你的队友Guihuo已死亡" in systems[0].text


def test_normal_mention_of_teammate_is_not_split():
    """玩家正常聊到"队友"不能被当成系统提示切走。"""
    chats = _chats(["(小队):[小队]Guihuo: my teanmate is afk, lets wait"])
    assert [event.text for event in chats] == ["my teanmate is afk, lets wait"]
    chats = _chats(["(小队):[小队]Guihuo: 我的队友在挂机"])
    assert [event.text for event in chats] == ["我的队友在挂机"]


# --------------------------------------------------------------- 悄悄话（私聊）
# 游戏里的悄悄话是另一套格式（实测截图）：
#     (私聊): 你对 Rockok说，da lao shui jiao le ..     ← 我发给对方
#     (私聊): Rockok告诉你: o na jiu shui jiao ba        ← 对方发给我
# 两种都不是 "名字:" 开头，以前整条被当成系统消息、**不翻译**。

def test_whisper_incoming_is_chat():
    events = ChatParser().parse(["(私聊): Rockok告诉你: o na jiu shui jiao ba"])
    assert len(events) == 1
    event = events[0]
    assert event.kind == "chat"
    assert event.channel == "悄悄话"
    assert event.speaker == "Rockok告诉你"      # 主语照游戏原样，显示才 1:1
    assert event.text == "o na jiu shui jiao ba"


def test_whisper_outgoing_is_chat():
    events = ChatParser().parse(["(私聊): 你对 Rockok说，da lao shui jiao le .."])
    assert len(events) == 1
    assert events[0].kind == "chat"
    assert events[0].channel == "悄悄话"
    assert events[0].speaker == "你对 Rockok说"
    assert events[0].text == "da lao shui jiao le"


def test_whisper_without_prefix_still_recognized():
    """OCR 把 "(私聊): " 整个吃掉时也要认得出来 —— 这个格式本身能自证。"""
    events = ChatParser().parse(["Rockok告诉你: need heals for shroud"])
    assert len(events) == 1
    assert events[0].kind == "chat"
    assert events[0].channel == "悄悄话"
    assert events[0].text == "need heals for shroud"


def test_whisper_ocr_junk_and_two_word_names():
    # 等级/图标被 OCR 读成单个字母 → 按老规矩当杂质丢掉（和普通聊天一致）
    incoming = ChatParser().parse(["(私聊): V Warzar告诉你: in"])[0]
    assert incoming.speaker == "Warzar告诉你"
    # 名字里带空格（陪宠/委任名）照样认得出来
    two_words = ChatParser().parse(["(私聊): 你对 Kendra Estleton说，hi"])[0]
    assert two_words.speaker == "你对 Kendra Estleton说"
    assert two_words.text == "hi"


def test_whisper_body_continuation_merges():
    """悄悄话正文被 OCR 折到下一行时要接回去。"""
    events = ChatParser().parse(["(私聊): Rockok告诉你:", "we need a healer"])
    chats = [event for event in events if event.is_chat]
    assert len(chats) == 1
    assert chats[0].text == "we need a healer"


def test_whisper_chinese_body_is_kept():
    events = ChatParser().parse(["(私聊): Rockok告诉你: 你好，在吗"])
    assert events[0].kind == "chat"
    assert events[0].channel == "悄悄话"
    assert "你好" in events[0].text


def test_normal_channels_not_affected_by_whisper_rules():
    """普通频道不能被悄悄话规则抢走。"""
    chats = _chats(["(小队):[小队]Guihuo: xie xie da lao!",
                    "(常规)Alice: OMW"])
    assert [event.channel for event in chats] == ["小队", "常规"]
    assert [event.speaker for event in chats] == ["Guihuo", "Alice"]


# ------------------------------------------------- 正文尾巴上的"频道标签"
# 游戏聊天框在每一行**右边**还画一次频道名，OCR 常把它并进正文尾部。实测译文里
# 会多出"小队"两个字（"走" → "走小队"），同一条消息还会因为"带标签/不带标签"
# 两个读法被显示两遍。

def test_trailing_channel_label_is_stripped():
    assert _chats(["(小队):[小队] Dreambarb: out 小队"])[0].text == "out"
    assert _chats(["(小队):[小队] AngelGwing: ty all (小队)"])[0].text == "ty all"
    assert _chats(["(小队):[小队] AngelGwing: ty all[小队]"])[0].text == "ty all"
    assert _chats(["(小队):[小队] Mornyngstar: resetting?小队"])[0].text == "resetting?"
    assert _chats(["(常规): Alice: hi 常规"])[0].text == "hi"
    # 系统消息尾巴上也会粘（"…离开了你的队伍。 小队"）
    systems = [event for event in ChatParser().parse(
        ["(小队):AngelGwing离开了你的队伍。 小队"]) if event.kind == "system"]
    assert systems and systems[0].text == "AngelGwing离开了你的队伍"


def test_trailing_label_stripped_in_whisper():
    # 尾巴上的标签必须是**这一行的频道**才摘（悄悄话行右边画的是"私聊/悄悄话"）
    assert _chats(["(私聊): Rockok告诉你: need heals 私聊"])[0].text == "need heals"
    # 别的频道名挂在这一行末尾不算标签，不能动
    assert _chats(["(私聊): Rockok告诉你: need heals 小队"])[0].text == "need heals 小队"


def test_trailing_label_does_not_eat_real_text():
    # 中文正文里"回小队"是正常说法（标签直接粘在中文后面时不砍）
    assert _chats(["(小队):[小队] Bob: 我要回小队"])[0].text == "我要回小队"
    # 普通英文结尾不能动
    assert _chats(["(小队):[小队] Bob: need a squad"])[0].text == "need a squad"


def test_trailing_label_tolerates_ocr_misread():
    """标签被 OCR 认花（"小际"）时，只要它跟正文之间有空格，也按标签摘掉。"""
    assert _chats(["(小队):[小队] Bob: out 小际"])[0].text == "out"


# --------------------------------------------------- 英文客户端（游戏语言是英文）
# 玩家截图：频道标签变成 (Standard)/(Guild:)/(Party:)/(Tell:)，悄悄话是
# "X tells you, '…'" 和 "You tell X, '…'"。以前这些都不认。

def test_english_client_channels_work_without_config():
    """不用改设置：英文频道名（含大小写、括号里带冒号）直接认，映射到对应中文频道。"""
    chats = _chats(["(Guild): [Guild] Huzi-2: liao ge zhen zao"])
    assert (chats[0].channel, chats[0].speaker, chats[0].text) == (
        "公会", "Huzi-2", "liao ge zhen zao")
    chats = _chats(["(Guild:): [Guild] Medics: o,huzi zao"])
    assert chats[0].channel == "公会"
    chats = _chats(["(GUILD): [Guild] Medics: o,huzi zao"])
    assert chats[0].channel == "公会"
    chats = _chats(["(Party): [Party] Huzi-2: hai bu qu shang ban"])
    assert chats[0].channel == "小队"
    chats = _chats(["(Standard): Alice: hello there"])
    assert chats[0].channel == "常规"


def test_english_whisper_both_directions():
    incoming = _chats(["(Tell): Huzi-2 tells you, 'halo nihao'"])[0]
    assert incoming.channel == "悄悄话"
    assert incoming.speaker == "Huzi-2告诉你"
    assert incoming.text == "halo nihao"

    outgoing = _chats(["(Tell): You tell Huzi-2, 'halo nihao'"])[0]
    assert outgoing.channel == "悄悄话"
    assert outgoing.speaker == "你对 Huzi-2说"
    assert outgoing.text == "halo nihao"


def test_english_trailing_label_only_when_same_channel():
    """英文正文里 "party/guild" 这种词很常见，只有本行频道名当尾巴时才摘。"""
    # 本行是公会，尾巴 "Guild" 是公会 → 摘
    assert _chats(["(Guild): [Guild] Huzi-2: out Guild"])[0].text == "out"
    # 本行是公会，句子正常以 party 结尾 → 不能动
    assert _chats(["(Guild): [Guild] Huzi-2: looking for party"])[0].text == \
        "looking for party"
