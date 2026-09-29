"""中文客户端回归语料：这些都是玩家实机反馈过的行，解析结果不许变。

为什么单独固化这批：为了支持英文客户端改过解析器，担心把中文这边的容错率改低了。
这批数据就是"改之前"的行为快照 —— 生成时拿 v3.0.25 的解析器逐条核对过，78 行完全一致；
以后谁再动解析器，跑一下这个文件就知道有没有影响中文识别。
（要新增语料：把实机行加到 cases 里，再用当时的解析器跑一遍确认结果是对的。）
"""
from app.channels import alias_table
from app.config import DEFAULT_CONFIG
from app.parser import ChatParser

ZH_CASES = [
('(小队): [小队] Sckham: Guys, do you play other games on Steam?', [['chat', '小队', 'Sckham', 'Guys, do you play other games on Steam?']]),
    ('(小队): [小队]    Kendra Estleton 加入了你的队伍。', [['system', '小队', '', 'Kendra Estleton 加入了你的队伍']]),
    ('(战利品): Dorqeth 将 Jeweled Key 从 宝箱 中取出。', [['system', '战利品', '', 'Dorqeth 将 Jeweled Key 从 宝箱 中取出']]),
    ('(小队):   你的队友Kendra Estleton已死亡。', [['system', '小队', '', '你的队友Kendra Estleton已死亡']]),
    ('(小队):[小队]Dorqeth:gi(小队):[小队]Dorqeth:eliteright?', [['chat', '小队', 'Dorqeth', 'gi'], ['chat', '小队', 'Dorqeth', 'eliteright?']]),
    ('(小队):[小队]Guihuo:堡垒? 错误):你的队友已经锁定了冒险难度', [['chat', '小队', 'Guihuo', '堡垒?'], ['system', '', '', '错误):你的队友已经锁定了冒险难度']]),
    ('(小队):[小队]S Sinoke:guihuo,nikyireddoor (小队)', [['chat', '小队', 'Sinoke', 'guihuo,nikyireddoor'], ['drop', '小队', '', '']]),
    ('(小队):[小队]9 Guihuo:in (小队)', [['chat', '小队', 'Guihuo', 'in'], ['drop', '小队', '', '']]),
    ('(小队):[小队]V Warzar:petforthedoor?', [['chat', '小队', 'Warzar', 'petforthedoor?']]),
    ('(聊天): 战利品:你舟5unsone从玉相取正', [['system', '', '', '战利品:你舟5unsone从玉相取正']]),
    ('(小队):你加入了Sinoke的队伍', [['system', '小队', '', '你加入了Sinoke的队伍']]),
    ('(小队):你已加入小队聊天室', [['system', '小队', '', '你已加入小队聊天室']]),
    ('(小队): [小队] Guihuo: 嗨,马上到', [['chat', '小队', 'Guihuo', '嗨,马上到']]),
    ('(小队):[小队] Sinoke: 嗨,位面监狱那边', [['chat', '小队', 'Sinoke', '嗨,位面监狱那边']]),
    ('(小队):[小队]Guihuo: 在', [['chat', '小队', 'Guihuo', '在']]),
    ('(小队):[小队]V Warzar:mingonnavape', [['chat', '小队', 'Warzar', 'mingonnavape']]),
    ('(小队):[小队]Warzar: 我马上就去吸一口', [['chat', '小队', 'Warzar', '我马上就去吸一口']]),
    ('(小队):[小队]Sinoke: (看不清楚)', [['chat', '小队', 'Sinoke', '(看不清楚)']]),
    ('(小队):[小队] Guihuo: 走神了', [['chat', '小队', 'Guihuo', '走神了']]),
    ('(小队):[小队] Guihuo:zoushenle', [['chat', '小队', 'Guihuo', 'zoushenle']]),
    ('(小队):[小队] Guihuo: 堡垒?', [['chat', '小队', 'Guihuo', '堡垒?']]),
    ('(错误):你的队友已经锁定了冒险难度', [['system', '', '', '你的队友已经锁定了冒险难度']]),
    ('(小队):[小队]Sinoke:yes', [['chat', '小队', 'Sinoke', 'yes']]),
    ('(小队):[小队] Warzar: 宠物去开门?', [['chat', '小队', 'Warzar', '宠物去开门?']]),
    ('(小队):[小队] Moisooiims: 哦你', [['chat', '小队', 'Moisooiims', '哦你']]),
    ('(小队):你的队友Beruthiell已死亡', [['system', '小队', '', '你的队友Beruthiell已死亡']]),
    ('(小队):[小队]Beruthiell: 完成', [['chat', '小队', 'Beruthiell', '完成']]),
    ('(小队): Zhaoyang 加入了你的队伍', [['system', '小队', '', 'Zhaoyang 加入了你的队伍']]),
    ('(小队):[小队] Zhaoyang: 马上到', [['chat', '小队', 'Zhaoyang', '马上到']]),
    ('(小队):[小队] Laranjinha-1: 大家都知道盗贼是可以牺牲的', [['chat', '小队', 'Laranjinha-1', '大家都知道盗贼是可以牺牲的']]),
    ('(小队:小队] perutnleil: 我靠', [['chat', '小队', 'perutnleil', '我靠']]),
    ('(小队):[小队]Beruthiell: 现在去眼魔巢穴', [['chat', '小队', 'Beruthiell', '现在去眼魔巢穴']]),
    ('(小队):DuerimGuardwell离开了你的队伍', [['system', '小队', '', 'DuerimGuardwell离开了你的队伍']]),
    ('(小队):[小队]Laranjinha-1: 位面监狱?', [['chat', '小队', 'Laranjinha-1', '位面监狱?']]),
    ('(小队):[小队] Beruthiell: 所以是位面监狱、绝望之塔和哀嚎?', [['chat', '小队', 'Beruthiell', '所以是位面监狱、绝望之塔和哀嚎?']]),
    ('(小队):[小队] Laranjinha-1: 上上下下', [['chat', '小队', 'Laranjinha-1', '上上下下']]),
    ('(小队):DuerimGuardwell加入了你的队伍', [['system', '小队', '', 'DuerimGuardwell加入了你的队伍']]),
    ('(小队):[小队]Laranjinha-1: 嗯嗯', [['chat', '小队', 'Laranjinha-1', '嗯嗯']]),
    ('(小队):[小队]Laranjinha-1: 守序', [['chat', '小队', 'Laranjinha-1', '守序']]),
    ('(小队):[小队]Beruthiell: 嗯......', [['chat', '小队', 'Beruthiell', '嗯']]),
    ('(小队):[小队]Laranjinha-1: 拿到宝珠了吗?', [['chat', '小队', 'Laranjinha-1', '拿到宝珠了吗?']]),
    ('(小队):[小队]Beruthiell: 不确定是不是两个人都需要', [['chat', '小队', 'Beruthiell', '不确定是不是两个人都需要']]),
    ('(小队):[小队]Beruthiell: 稍等', [['chat', '小队', 'Beruthiell', '稍等']]),
    ('(小队):[小队]Beruthiell: 在杜鲁格', [['chat', '小队', 'Beruthiell', '在杜鲁格']]),
    ('(小队):[小队]Beruthiell: 把我锁里面', [['chat', '小队', 'Beruthiell', '把我锁里面']]),
    ('(小队):[小队]Laranjinha-1: 请拉一下拉杆', [['chat', '小队', 'Laranjinha-1', '请拉一下拉杆']]),
    ('(小队):[小队]Laranjinha-1: 拖动这里可以', [['chat', '小队', 'Laranjinha-1', '拖动这里可以']]),
    ('(小队):[小队]Beruthiell: 极光射线太疼了', [['chat', '小队', 'Beruthiell', '极光射线太疼了']]),
    ('(小队):[小队]Guihuo: 我今天被它一下秒了', [['chat', '小队', 'Guihuo', '我今天被它一下秒了']]),
    ('(小队):[小队]Beruthiell: 还好火盾救了我一下 :D', [['chat', '小队', 'Beruthiell', '还好火盾救了我一下 :D']]),
    ('(小队):[小队]Zhaoyang: 谢谢大家', [['chat', '小队', 'Zhaoyang', '谢谢大家']]),
    ('(小队):Zhaoyang离开了你的队伍', [['system', '小队', '', 'Zhaoyang离开了你的队伍']]),
    ('(小队):[小队]Beruthiell: 一般般吧', [['chat', '小队', 'Beruthiell', '一般般吧']]),
    ('(小队):[小队]Guihuo: 100张复活卷轴', [['chat', '小队', 'Guihuo', '100张复活卷轴']]),
    ('(小队):[小队]Beruthiell: 休息中', [['chat', '小队', 'Beruthiell', '休息中']]),
    ('(小队):[小队]Beruthiell: 欢迎回来', [['chat', '小队', 'Beruthiell', '欢迎回来']]),
    ('(小队):[小队]Laranjinha-1: 现在去还是等会儿?', [['chat', '小队', 'Laranjinha-1', '现在去还是等会儿?']]),
    ('(小队):[小队]Guihuo: 谢谢', [['chat', '小队', 'Guihuo', '谢谢']]),
    ('(小队):[小队]Beruthiell: 我们完蛋了', [['chat', '小队', 'Beruthiell', '我们完蛋了']]),
    ('(小队):[小队]Laranjinha-1: 你要开始了吗?', [['chat', '小队', 'Laranjinha-1', '你要开始了吗?']]),
    ('(小队):[小队]Laranjinha-1: 1号塔', [['chat', '小队', 'Laranjinha-1', '1号塔']]),
    ('(小队):[小队]Beruthiell: 还有人在吗?', [['chat', '小队', 'Beruthiell', '还有人在吗?']]),
    ('(小队):[小队]Dreambarb: 走 小队', [['chat', '小队', 'Dreambarb', '走']]),
    ('(小队):[小队]AngelGwing: 谢谢大家 (小队)', [['chat', '小队', 'AngelGwing', '谢谢大家'], ['drop', '小队', '', '']]),
    ('(小队):[小队] Mornyngstar: 在重置吗?小队', [['chat', '小队', 'Mornyngstar', '在重置吗?']]),
    ('(小队):AngelGwing离开了你的队伍。 小队', [['system', '小队', '', 'AngelGwing离开了你的队伍']]),
    ('(小队):[小队] Bob: 我要回小队', [['chat', '小队', 'Bob', '我要回小队']]),
    ('(私聊): 你对 Rockok说，da lao shui jiao le ..', [['chat', '悄悄话', '你对 Rockok说', 'da lao shui jiao le']]),
    ('(私聊): Rockok告诉你: o na jiu shui jiao ba', [['chat', '悄悄话', 'Rockok告诉你', 'o na jiu shui jiao ba']]),
    ('(小队):[小队] Ize: 能共享任务吗?', [['chat', '小队', 'Ize', '能共享任务吗?']]),
    ('(小队): [小队] Ize: 如果你放弃任务遗忘洞穴然后共享给你,那就能进精英难度了', [['chat', '小队', 'Ize', '如果你放弃任务遗忘洞穴然后共享给你,那就能进精英难度了']]),
    ('(小队): [小队] Dorgeth: 啊 我得先交任务 我的错', [['chat', '小队', 'Dorgeth', '啊 我得先交任务 我的错']]),
    ('(小队):[小队]Laranjinha-1: 大家都说葡萄牙语吗?', [['chat', '小队', 'Laranjinha-1', '大家都说葡萄牙语吗?']]),
    ('(小队):[小队]Guihuo: 堡垒? 错误):你的队友已经锁定了冒险难度', [['chat', '小队', 'Guihuo', '堡垒?'], ['system', '', '', '错误):你的队友已经锁定了冒险难度']]),
    ('(小队):[小队]8 Sinoke: hi, pop side (小队)', [['chat', '小队', 'Sinoke', 'hi, pop side'], ['drop', '小队', '', '']]),
    ('(小队):[小队]Sinoke: 位面监狱位面监狱', [['chat', '小队', 'Sinoke', '位面监狱']]),
    ('(小队):[小队]Sinoke: (看不清楚)', [['chat', '小队', 'Sinoke', '(看不清楚)']]),
    ('(小队):[小队] Guihuo: 别管我', [['chat', '小队', 'Guihuo', '别管我']]),
]


def test_zh_corpus_parses_exactly_as_before():
    parser = ChatParser(alias_table(DEFAULT_CONFIG))
    for line, expected in ZH_CASES:
        events = parser.parse([line])
        got = [[event.kind, event.channel, event.speaker, event.text]
               for event in events]
        assert got == expected, "%s\n  期望 %s\n  实际 %s" % (line, expected, got)


def test_zh_corpus_counts_do_not_drop():
    """粗粒度体检：聊天/系统条数别突然变少（少一条就是漏识别了）。"""
    parser = ChatParser(alias_table(DEFAULT_CONFIG))
    chats = systems = 0
    for line, _expected in ZH_CASES:
        for event in parser.parse([line]):
            chats += 1 if event.is_chat else 0
            systems += 1 if event.kind == "system" else 0
    assert chats >= 45 and systems >= 2, (chats, systems)
