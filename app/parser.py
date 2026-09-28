"""把 OCR 出来的文本行解析成"聊天消息 / 系统消息"。

这里保留旧版程序在真实游戏里试出来的规则（这些是实测结论，不要凭感觉改）：

1. DDO 聊天行的实际形态（真实截图里就是这样）：
       (小队): [小队] Sckham: Guys, do you play other games on Steam?
       (小队): [小队]    Kendra Estleton 加入了你的队伍。
       (战利品): Dorqeth 将 Jeweled Key 从 宝箱 中取出。
       (小队):   你的队友Kendra Estleton已死亡。
   即：`(频道): ` 开头（玩家行后面还有 `[频道] ` 标签），然后是 `玩家名: 正文`；
   没有 `玩家名:` 的行是系统消息（组队/死亡/断线/战利品），原样显示不翻译。
   注意两个前缀之间**有空格**，前缀扫描必须允许空格，否则玩家行会被误判成系统消息。
2. 长消息被 OCR 拆成多行时，没有前缀的那一行是上一条消息的续行，要接回去。
3. 系统消息是中文（"Grelik加入了你的队伍"），只当上下文，不当聊天。
4. 前缀会被 OCR 认花：(小 -> 小队, 战 -> 战利品, 寸队/可队/①队 -> 小队 等)。
5. OCR 偶尔丢掉左括号，只剩 "小队）Sckham: ..."，需要自动补回。

解析器是纯函数：同样的输入永远得到同样的输出，方便单测。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from . import textutil

KIND_CHAT = "chat"
KIND_SYSTEM = "system"
KIND_DROP = "drop"

# 只把"开头的括号前缀"当频道标签，( ) （ ） [ ] 【 】 都算。
# 前后的 \s* 很关键：真实格式是 "(小队): [小队] 名字: 正文"，两个前缀中间有空格。
PREFIX_RE = re.compile(
    r"\s*[\(（\[\【]\s*([^\)）\]\】【\n]{1,12}?)\s*[\)）\]\】]\s*[:：;；]?\s*")
CLOSERS = "）)]】]"

# OCR 把右括号读重："(小队)):" → "(小队):"。只在冒号紧跟在重复括号后面时才折叠，
# 免得动到正文里的 "))"（比如玩家打的"哈哈))"）。
_DOUBLE_CLOSER_RE = re.compile(r"([\)）\]\】])\1+\s*(?=[:：;；])")

# OCR 认错的频道名 → 标准频道名（精确匹配优先）
DEFAULT_ALIASES: Dict[str, str] = {
    "小队": "小队", "小": "小队", "小际": "小队", "小阴": "小队", "小对": "小队",
    "寸队": "小队", "可队": "小队", "①队": "小队", "水队": "小队", "木队": "小队",
    "队": "小队", "小队伍": "小队",
    "队伍": "队伍", "团队": "队伍", "组队": "队伍",
    "公会": "公会", "工会": "公会", "公会频道": "公会",
    "常规": "常规", "普通": "常规", "常用": "常规", "常规聊天": "常规",
    "公共": "公共", "世界": "公共", "综合": "公共",
    "悄悄话": "悄悄话", "密语": "悄悄话", "私聊": "悄悄话",
    "战利品": "战利品", "战": "战利品", "战励品": "战利品",
}

# 模糊兜底规则：(子串, 频道名)，按顺序取第一个命中
_FUZZY_RULES: List[Tuple[str, str]] = [
    ("战", "战利品"),
    ("公会", "公会"),
    ("工会", "公会"),
    ("公共", "公共"),
    ("世界", "公共"),
    ("综合", "公共"),
    ("悄悄", "悄悄话"),
    ("密语", "悄悄话"),
    ("私聊", "悄悄话"),
    ("常规", "常规"),
    ("普通", "常规"),
    ("常用", "常规"),
    ("队伍", "队伍"),
    ("团队", "队伍"),
    ("组队", "队伍"),
    ("小", "小队"),
    ("队", "小队"),
]

SPEAKER_RE = re.compile(
    r"^(?P<name>[A-Za-z][A-Za-z0-9_'\-\.]{0,23})\s*[:：]\s*(?P<body>.*)$"
)

# "宽容版"找名字：OCR 经常把等级/图标一起读进来（实测见过
# "(小队):[小队]S Sinoke:..."、"[小队]9 Guihuo:..."、"[小队]V Warzar:..."）。
# 锚定写法会因为前面那个杂质字符直接失败，整条玩家发言就被当成系统消息丢掉了，
# 所以名字允许不在行首 —— 但前面的杂质必须极短（见 _is_junk_prefix）。
SPEAKER_LOOSE_RE = re.compile(
    r"(?<![A-Za-z0-9_'\-\.])(?P<name>[A-Za-z][A-Za-z0-9_'\-\.]{1,23})\s*[:：]\s*(?P<body>.*)$"
)
NAME_JUNK_LIMIT = 3          # 名字前面最多容忍几个字符的 OCR 杂质
NAME_SEARCH_WINDOW = 40      # 只在行首这一段里找名字，避免正文里的 "xx:" 被误认

# 杂质里如果混进了"频道标签"（OCR 把 (小队) 多读了一遍，或丢左括号后标签粘在正文上），
# 先把标签整块去掉再判断，否则 "小队]V " 会因为这 5 个字符被判成正文。
JUNK_LABEL_WORDS = ("小队", "队伍", "团队", "公会", "工会", "常规", "普通", "公共",
                    "世界", "综合", "悄悄话", "密语", "战利品")
JUNK_TRIM_CHARS = " \t]）)】>:：<([【"

# "新消息开头"的第二种形态：OCR 把左括号丢了，只剩 "错误):" 这样的尾巴。
# 只认"标签里有中文"或"能归一化成频道名"的情况，避免把正文里的英文括号当分隔。
LABEL_SPLIT_RE = re.compile(
    r"(?P<label>[^()\[\]（）【】\s]{1,8}?)(?P<close>[\)）\]\】>》])\s*[:：]\s*")

# 链接行：游戏里常把长链接换到下一行。它长得像 "https:" + "//..."，
# 会被 SPEAKER_RE 误当成"玩家名: 正文"，所以要先识别出来当续行处理。
URL_START_RE = re.compile(r"^\s*(?:https?|ftp|www)\b", re.IGNORECASE)


@dataclass
class Event:
    """一条解析结果。"""

    kind: str
    text: str
    channel: str = ""
    speaker: str = ""
    raw: str = ""
    prefixes: List[str] = field(default_factory=list)
    prefix_text: str = ""      # 原始前缀文本（含空格），显示时 1:1 还原游戏里的样子

    @property
    def is_chat(self) -> bool:
        return self.kind == KIND_CHAT


def alias_table_for(names: Sequence[str],
                    extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """按"当前频道表"生成别名表（识别跟着设置走）。

    内置的那堆 OCR 错字别名（寸队→小队、战励品→战利品…）**只对表里存在的频道生效**：
    玩家改了名字、或者删掉某个频道以后，识别也跟着变 —— 不会再把已经删掉的频道
    认出来，也就不会冒出"设置里根本没有的频道"。
    """
    wanted = [str(name).strip() for name in (names or []) if str(name).strip()]
    table: Dict[str, str] = {}
    for alias, canonical in DEFAULT_ALIASES.items():
        if canonical in wanted:
            table[alias] = canonical
    for name in wanted:
        table[name] = name
    if extra:
        table.update({str(k): str(v) for k, v in extra.items()})
    return table


def normalize_channel(raw: str, aliases: Optional[Dict[str, str]] = None,
                      allowed: Optional[Sequence[str]] = None) -> str:
    """把 OCR 认出的频道名归一化成标准频道；认不出来返回空串。

    `allowed` 是"当前允许的频道名集合"（一般直接由别名表推导）：模糊兜底规则
    只会归到集合里的频道，免得把已经删掉的频道又认回来。
    """
    name = (raw or "").strip().strip(":：").replace(" ", "").replace("　", "")
    if not name:
        return ""
    table = DEFAULT_ALIASES if aliases is None else aliases
    if name in table:
        return table[name]
    allowed_set = set(table.values()) if allowed is None else set(allowed)
    # 这个名字会不会其实是"表里没有的另一个频道"？（例如表里没有"队伍"，
    # 而标签是"团队/队伍"）—— 是的话别用模糊规则硬凑到某个频道上，
    # 返回空，让上层提示"去 设置 → 频道 加一行"。
    absent = {alias for alias, canonical in DEFAULT_ALIASES.items()
              if canonical not in allowed_set}
    if name in absent or any(len(word) >= 2 and word in name for word in absent):
        return ""
    for token, channel in _FUZZY_RULES:
        if channel not in allowed_set:
            continue
        if token in name:
            return channel
    return ""


def _repair_missing_open_bracket(line: str) -> str:
    """修补行首的前缀：OCR 会丢左括号，也会把右括号读重。

    实测样例：
        小队）Sckham: hi          → (小队）Sckham: hi      （丢了左括号）
        (小队)):[小队]Sinoke:yes  → (小队):[小队]Sinoke:yes（多了一个右括号，
                                    不修的话整条玩家发言会被当成系统消息丢掉）
    """
    head = line[:14]
    fixed = _DOUBLE_CLOSER_RE.sub(r"\1", head) + line[14:]
    line = fixed
    head = line[:6]
    if line[:1] not in "(（[【":
        index = min((head.index(ch) for ch in CLOSERS if ch in head), default=-1)
        if 0 < index <= 5:
            return "(" + line
    return line


def _leading_prefixes(line: str) -> List[Tuple[int, int, str]]:
    """从行首连续读取频道前缀，返回 [(start, end, 原始频道名), ...]。"""
    spans: List[Tuple[int, int, str]] = []
    pos = 0
    while pos < len(line):
        match = PREFIX_RE.match(line, pos)
        if not match:
            break
        spans.append((match.start(), match.end(), match.group(1)))
        pos = match.end()
    return spans


def _split_speaker(rest: str) -> Tuple[str, str]:
    """从"频道前缀之后"的文本里找玩家名，返回 (名字, 正文)。找不到返回 ("", 原文)。"""
    match = SPEAKER_RE.match(rest)
    if match:
        return match.group("name"), match.group("body")
    loose = SPEAKER_LOOSE_RE.search(rest[:NAME_SEARCH_WINDOW])
    if loose and _is_junk_prefix(rest[:loose.start()]):
        return loose.group("name"), loose.group("body")
    return "", rest


def _clean_chat_body(body: str) -> str:
    """清理玩家正文（去掉首尾垃圾，并还原"整条被贴两遍"的重复）。"""
    return textutil.collapse_doubled(textutil.clean_body(body))


def _split_trailing_notice(body: str) -> Tuple[str, str]:
    """把"被 OCR 粘在玩家正文后面的系统提示"切出来。

    实测（用户日志）：
        "笑死 Imao的队友Guihuo已死亡"  → 正文 "笑死 Imao" + 提示 "你的队友Guihuo已死亡"

    这里只用**很具体的句式**（名字+已死亡/已断线/加入/离开），免得把玩家正常聊到的
    "我的队友"当成系统提示切走。
    """
    for pattern in _TRAILING_NOTICE_RES:
        match = pattern.search(body or "")
        if match and match.start() > 0:
            notice = body[match.start():].strip()
            if notice.startswith("的队友"):
                notice = "你" + notice          # OCR 常把"你"吃掉
            return body[: match.start()].strip(), notice
    return body, ""


_NAME = r"[A-Za-z0-9_'\-\.]{2,24}"
_TRAILING_NOTICE_RES = (
    re.compile(r"(?:你)?的队友\s*%s\s*(?:已死亡|已断线|已复活|已经离线)" % _NAME),
    re.compile(r"%s\s*(?:已死亡|已断线|已复活|加入了你的队伍|离开了你的队伍|已加入小队)" % _NAME),
    re.compile(r"(?:你)?现在是队长"),
    re.compile(r"你已将?\s*%s\s*(?:移出了小队|移出队伍)" % _NAME),
)


def _is_junk_prefix(junk: str) -> bool:
    """名字前面那点东西是不是 OCR 杂质（等级数字、图标被认成字母等）。

    允许："" / "9 " / "V " / "(< " / "8 "。
    不允许："hello "（那是正文，不是杂质），所以限制长度和字母数量。

    注意只数**拉丁**字母：中文的 isalpha() 也是 True，"小队]" 这种
    "频道标签被 OCR 多读了一遍"的杂质必须仍算杂质。
    """
    junk = (junk or "").strip()
    for word in JUNK_LABEL_WORDS:
        junk = junk.replace(word, "")
    junk = junk.strip(JUNK_TRIM_CHARS)
    if not junk:
        return True
    if len(junk) > NAME_JUNK_LIMIT:
        return False
    latin = sum(1 for ch in junk if "a" <= ch.lower() <= "z")
    return latin <= 1


def _strip_leading_prefixes(line: str, aliases) -> str:
    spans = _leading_prefixes(line)
    return (line[spans[-1][1]:] if spans else line).strip()


def split_merged_line(line: str, aliases=None) -> List[str]:
    """把"OCR 把两条消息塞进一个框"的行拆回多条。

    真实遇到的样子（文字全挤在一起、空格丢失）：
        (小队):[小队]Dorqeth:gi(小队):[小队]Dorqeth:eliteright?
        (小队):[小队]Guihuo:堡垒? 错误):你的队友已经锁定了冒险难度
    判断依据：行内出现新的"频道前缀"，且它前面那一段已经是一条完整消息
    （含 `玩家名:` 或者是有中文的系统消息），就认为是下一条消息的开头。
    """
    cuts = []
    for match in PREFIX_RE.finditer(line):
        if normalize_channel(match.group(1), aliases):
            cuts.append(match.start())
    for match in LABEL_SPLIT_RE.finditer(line):
        label = match.group("label")
        if textutil.has_cjk(label) or normalize_channel(label, aliases):
            cuts.append(match.start())
    if not cuts:
        return [line]

    segments: List[str] = []
    cursor = 0
    for cut in sorted(set(cuts)):
        if cut <= cursor:
            continue                          # 行首那个前缀属于本段，不算分隔
        head = _strip_leading_prefixes(line[cursor:cut], aliases)
        if not head:
            continue                          # 前面只有前缀，说明这是同一条消息的第二个前缀
        name, _body = _split_speaker(head)
        if name or textutil.has_cjk(head):
            segments.append(line[cursor:cut].strip())
            cursor = cut
    segments.append(line[cursor:].strip())
    return [segment for segment in segments if segment]


class ChatParser:
    """把一帧 OCR 结果解析成事件列表。"""

    def __init__(self, aliases: Optional[Dict[str, str]] = None,
                 infer_channel_from_previous: bool = True) -> None:
        # 传进来的别名表就是"当前全部可识别的频道"（由 app/channels.py 按频道表生成）；
        # 传 None 时用内置那套（单测和老工具用）。
        self.aliases = dict(DEFAULT_ALIASES) if aliases is None else dict(aliases)
        self.infer_channel_from_previous = infer_channel_from_previous

    # ---------------------------------------------------------------- 主入口
    def parse(self, lines: Sequence[str]) -> List[Event]:
        events: List[Event] = []
        last_chat: Optional[Event] = None
        for raw_line in lines:
            line = textutil.normalize(raw_line)
            if not line:
                continue
            line = _repair_missing_open_bracket(line)
            # OCR 有时把两条消息塞进同一个框，先拆回多条再逐条处理
            for segment in split_merged_line(line, self.aliases):
                last_chat = self._parse_segment(segment, raw_line, events, last_chat)
        return events

    def _parse_segment(self, line: str, raw_line: str, events: List[Event],
                       last_chat: Optional[Event]) -> Optional[Event]:
        """处理一条消息，返回新的 last_chat。"""
        spans = _leading_prefixes(line)
        prefix_text = line[:spans[-1][1]] if spans else ""
        channels = [c for c in (normalize_channel(s[2], self.aliases) for s in spans) if c]
        channel = channels[0] if channels else ""
        rest = (line[spans[-1][1]:] if spans else line).strip()

        # ---- 有频道前缀 ----
        if spans:
            name, body = _split_speaker(rest)

            # 有 "玩家名:" → 玩家发言（正文可能为空，等下一行的续行接上）
            if name:
                clean, notice = _split_trailing_notice(_clean_chat_body(body))
                event = Event(KIND_CHAT, clean, channel, name,
                              raw_line, [s[2] for s in spans], prefix_text)
                events.append(event)
                # 提示被切出来的话，照样当成系统消息显示（本来就是要看的组队/生死信息）
                if notice:
                    events.append(Event(KIND_SYSTEM, notice, channel, "", raw_line,
                                        [s[2] for s in spans], prefix_text))
                return event

            # 没有 "玩家名:" → 系统消息（组队/死亡/断线/战利品），原样显示不翻译
            text = textutil.clean_body(rest)
            if text and (textutil.has_cjk(text) or textutil.has_latin(text)):
                events.append(Event(KIND_SYSTEM, text, channel, "", raw_line,
                                    [s[2] for s in spans], prefix_text))
            else:
                events.append(Event(KIND_DROP, text, channel, "", raw_line,
                                    [s[2] for s in spans], prefix_text))
            return None

        # ---- 没有前缀 ----
        # 链接行（https://... / www.xxx）一定是上一条消息的续行
        if URL_START_RE.match(line):
            if last_chat is not None:
                tail = textutil.clean_body(line)
                if tail:
                    last_chat.text = textutil.normalize(last_chat.text + " " + tail)
            else:
                events.append(Event(KIND_SYSTEM, textutil.clean_body(line), "", "", raw_line))
            return last_chat

        if textutil.has_cjk(line):
            events.append(Event(KIND_SYSTEM, textutil.clean_body(line), "", "", raw_line))
            return None

        # "Name: text" 但前缀丢了 → 按聊天行处理，频道沿用上一条
        name, body = _split_speaker(line)
        if name and last_chat is not None:
            body = _clean_chat_body(body)
            if textutil.is_noise(body):
                return last_chat
            event = Event(KIND_CHAT, body,
                          last_chat.channel if self.infer_channel_from_previous else "",
                          name, raw_line)
            events.append(event)
            return event

        # 续行：接到上一条聊天消息后面
        if last_chat is not None and not textutil.is_noise(line):
            tail = textutil.clean_body(line)
            if tail:
                last_chat.text = textutil.normalize(last_chat.text + " " + tail)
            return last_chat

        events.append(Event(KIND_DROP, line, "", "", raw_line))
        return last_chat

    # ------------------------------------------------------------ 便捷方法
    def chat_events(self, lines: Sequence[str]) -> List[Event]:
        return [event for event in self.parse(lines) if event.is_chat]
