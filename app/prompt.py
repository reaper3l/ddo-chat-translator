"""提示词构建。

原则：游戏的语境、俚语、术语说清楚，输出格式卡死，用户的纠正偏好按需附加。
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Sequence, Tuple

# 常用缩写/俚语速查（写进 prompt，模型对没进术语表的缩写也能翻对）
SLANG_TABLE = """\
OMW=马上到  BRB=马上回来  AFK=暂离  BIO=去洗手间  LFM=组队招募  LFG=找队伍
LF=找(某职业)  WT B=收购  WTS=出售  WTT=求换  TY/THX/TYVM=谢谢  NP=不客气
YW=不客气  GL=祝好运  HF=玩得开心  GG=打得好  GJ=干得漂亮  WB=欢迎回来
CYA=再见  DC=断线/掉线  RIP=挂了/没了  OOM=没蓝了  OOC=没血了  INC=有怪来了
PULL=拉怪  AGGRO=仇恨  KITE=拉着跑  ZERG=一波冲  WIPE=团灭  REZ/RAISE=复活
SHRINE=神龛/恢复点  HIRE=雇佣兵  PLAT=铂金币  FAVOR=声望  TR=真轮回  ETR=史诗真轮回
ITR=种族真轮回  R1~R10=死神难度1~10  EE/EH/EN=史诗精英/困难/普通  ELITE=精英难度
REAPER=死神难度  QUEST=任务  DUNGEON=副本  RAID=团队副本  LOOT=战利品  CHEST=宝箱
SHARE=共享任务  ABANDON=放弃任务  FLAG=开启任务前置  BIND=灵魂绑定  TP=传送
BUFF=增益  DEBUFF=减益  CC=控制技能  LOS=视线  DPS=输出  TANK=坦克  HEAL=治疗
PLZ=请  SRY=抱歉  IDK=不知道  IMO=我觉得  BTW=对了  FYI=提醒一下  ASAP=尽快
GTG=要下线了  NVM=算了  WTF=什么鬼  LOL=哈哈  ROFL=笑死  GRATS/GZ=恭喜
"""

_PLACEHOLDER_RULE = (
    "文中形如 {{TERM_0}} 的记号是已经确认过的游戏术语，"
    "必须**原样**保留（连花括号一起），不要翻译、不要改写、不要移动位置。"
)

# 从真实对局里挑出来的易错句：这些句子旧版翻得最差，写进提示词当范例
TRICKY_EXAMPLES = """\
- i never die → 我从来没死过（不是我从不死亡/我从没驯服过）
- ok so i do die ha → 行吧，看来我确实会死，哈哈
- ahh i have to turn in, my bad → 啊，我得先交任务，我的错（turn in = 交任务）
- clicked enter while showing normal → 显示普通难度的时候手滑点了进入
- yes, or abandon → 对，或者放弃任务
- if you abandon X and i share it to you → 如果你先放弃X，我再共享给你
- well can't enter → 呃，进不去
- can share? → 能共享任务吗？
- need heals → 需要治疗
- lol ha → 哈哈
- gtg → 我先下了
"""


def _memory_block(memory) -> str:
    """把你纠正过的东西写进 prompt（有上限，避免 prompt 无限膨胀）。"""
    if memory is None:
        return ""
    lines: List[str] = []

    terms = memory.prompt_terms(limit=25)
    if terms:
        pairs = "，".join("%s=%s" % (i["text"], i["zh"]) for i in terms)
        lines.append("【我已确认的术语】%s" % pairs)

    phrases = memory.prompt_phrases(limit=12)
    if phrases:
        pairs = "；".join(
            "%s → %s" % (str(i.get("source", ""))[:40], i.get("zh", "")) for i in phrases
        )
        lines.append("【我改过并希望保持的译法】%s" % pairs)

    return "\n".join(lines)


def build_system_prompt(mode: str = "quality", memory=None) -> str:
    memory_block = _memory_block(memory)

    if mode == "fast":
        text = (
            "你是 DDO（龙与地下城 Online）游戏聊天翻译助手，把英文玩家聊天翻成简体中文口语。\n"
            "输入来自 OCR，可能有错拼/漏字母/空格错误，请按游戏语境猜对再翻。\n"
            f"{_PLACEHOLDER_RULE}\n"
            "术语：elite=精英难度（不是真轮回），TR=真轮回，R1-R10=死神难度，"
            "quest=任务，dungeon=副本，shrine=神龛/恢复点，favor=声望，plat=铂金币。\n"
            "缩写：OMW=马上到，BRB=马上回来，AFK=暂离，LFG=找队伍，LFM=缺人组队，"
            "TY=谢谢，NP=不客气，GL=祝好运，HF=玩得开心，GG=打得好，WIPE=团灭，REZ=复活。\n"
            "要求：说人话，不要逐词直译；除了人名/队伍名/网址，译文里不要留英文单词；"
            "中文里不要有多余空格。\n"
            "只输出中文译文本身，不要英文原文，不要解释，不要加引号；"
            "纯乱码就把原文原样返回，不要写「看不清楚」这类说明。\n"
        )
        return text + (memory_block + "\n" if memory_block else "")

    text = f"""你是 DDO（龙与地下城 Online，英文名 Dungeons & Dragons Online）的游戏聊天翻译助手。
你的任务：把游戏聊天框里外国玩家说的英文，翻译成自然、简短的中文口语，让我能立刻看懂。

【输入特点】
1. 文字来自屏幕 OCR，经常有错拼、漏字母、粘连空格（例如 "doyou" 其实是 "do you"，
   "can share" 可能被认成 "can shayes"）。请根据游戏聊天语境自动纠正后再翻译。
2. 玩家大量使用缩写和俚语，必须按游戏习惯翻译。
3. {_PLACEHOLDER_RULE}

【DDO 术语特别说明】
- elite = 精英难度（绝对不要翻成"真轮回/史诗/传奇"）；TR = 真轮回（转生）；ETR = 史诗真轮回
- R1-R10 = 死神难度 1-10；reaper = 死神难度；EE/EH/EN = 史诗精英/史诗困难/史诗普通
- quest = 任务；dungeon = 副本；raid = 团队副本；share = 共享任务；abandon = 放弃任务
- shrine = 神龛（补血补蓝的地方）；favor = 声望；plat = 铂金币；hireling = 雇佣兵
- AC = 护甲等级；HP = 血量；SP = 蓝量；aggro = 仇恨；buff/debuff = 增益/减益
- Shroud = 幽影堡；ToD/Tower of Despair = 绝望之塔；VON1~VON6 = 龙1~龙6

【常用缩写与俚语】
{SLANG_TABLE}
【这些句子最容易翻错，照这样来】
{TRICKY_EXAMPLES}
【翻译风格（信达雅）】
- 说人话：翻出来要像中国玩家在游戏里说中文，不要逐词直译，不要翻译腔。
- 不要中英夹杂：除了人名、队伍名、网址、物品英文名，译文里不要留英文单词。
- 中文里不要有多余空格，标点用中文标点（，。？！）。
- 短句就短翻：yes=好/可以，omw=马上到，lol ha=哈哈，gi=好主意（好主意=good idea）。
- 语气词要自然：ahh→啊，argh→啊呀，well→呃/好吧，ha→哈。
- 前后文里提到的任务名、难度、人名要保持一致。
- 不要把语气词、缩写当成"要逐词翻译的单词"，要转成对应的中文口语。
【输出要求】
- 只输出中文译文本身。不要带英文原文，不要解释，不要加"翻译："之类的前缀，不要加引号。
- 保持玩家说话的口语感，短句就短翻（"yes" 就是"好/可以"）。
- 人名、队伍名、网址、物品英文名保持原样，不要硬翻。
- 任务/副本名用通用译名（例如 Forgotten Caverns=遗忘洞穴、Shroud=幽影堡）；拿不准就意译，不要留英文。
- 如果原文确实无法理解（纯乱码），**原样输出原文**。绝对不要输出"（看不清楚）""无法翻译"
  这种给用户看的说明文字 —— 译文必须是译文。
"""
    if memory_block:
        text += "\n" + memory_block + "\n"
    return text


def build_zh2en_system_prompt(memory=None) -> str:
    hints = ""
    if memory is not None:
        terms = memory.prompt_terms(limit=20)
        if terms:
            pairs = "，".join("%s=%s" % (i["zh"], i["text"]) for i in terms)
            hints = f"\n【用词习惯（中文→英文）】{pairs}"
    return f"""你是 DDO 玩家的聊天翻译助手。把我要说的中文，翻译成外国玩家一眼就懂的游戏口语英文。

要求：
1. 能用一个缩写就用缩写，老外就是这么聊天的：
   OMW=马上到，BRB=马上回来，AFK=暂离，LFM=缺人组队，LFG=找队伍，TY=谢谢，
   NP=不客气，GL=祝好运，HF=玩得开心，GG=打得好，OOM=没蓝了，REZ=复活，
   SHRINE=去神龛，R1/R2=死神难度1/2，ELITE=精英难度，SHARE=共享任务，ABANDON=放弃任务。
2. 输出短句，符合游戏聊天习惯，不要书面语。
3. 只输出英文本身，不要引号，不要解释，不要中文。
4. 人名和道具名保持原样。{hints}
"""


def build_messages(system_prompt: str,
                   context: Sequence[Tuple[str, str]],
                   system_events: Sequence[str],
                   text: str) -> List[Dict[str, str]]:
    """组装发给模型的 messages。context 是 [(英文原文, 中文译文), ...]。"""
    messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]

    if system_events:
        joined = "；".join(event for event in system_events if event)
        if joined:
            messages.append({
                "role": "system",
                "content": "【刚才游戏里的系统提示，仅供参考上下文】" + joined[:300],
            })

    for source, translated in context:
        messages.append({"role": "user", "content": source})
        messages.append({"role": "assistant", "content": translated})

    messages.append({"role": "user", "content": text})
    return messages


def summarize_for_log(messages: Sequence[Dict[str, str]], limit: int = 120) -> str:
    parts = []
    for message in messages:
        content = re.sub(r"\s+", " ", message.get("content", ""))
        parts.append("[%s] %s" % (message.get("role"), content[:limit]))
    return " | ".join(parts)
