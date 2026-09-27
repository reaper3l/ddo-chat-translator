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


def normalize_channel(raw: str, aliases: Optional[Dict[str, str]] = None) -> str:
    """把 OCR 认出的频道名归一化成标准频道；认不出来返回空串。"""
    name = (raw or "").strip().strip(":：").replace(" ", "").replace("　", "")
    if not name:
        return ""
    table = DEFAULT_ALIASES if aliases is None else aliases
    if name in table:
        return table[name]
    for token, channel in _FUZZY_RULES:
        if token in name:
            return channel
    return ""


def _repair_missing_open_bracket(line: str) -> str:
    """OCR 丢掉左括号的情况：'小队）Sckham: hi' → '(小队）Sckham: hi'。"""
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
    match = SPEAKER_RE.match(rest)
    if not match:
        return "", rest
    return match.group("name"), match.group("body")


def _strip_leading_prefixes(line: str, aliases) -> str:
    spans = _leading_prefixes(line)
    return (line[spans[-1][1]:] if spans else line).strip()


def split_merged_line(line: str, aliases=None) -> List[str]:
    """把"OCR 把两条消息塞进一个框"的行拆回多条。

    真实遇到的样子（文字全挤在一起、空格丢失）：
        (小队):[小队]Dorqeth:gi(小队):[小队]Dorqeth:eliteright?
    判断依据：行内出现新的"频道前缀"，且它前面那一段已经是一条完整消息
    （含 `玩家名:` 或者是有中文的系统消息），就认为是下一条消息的开头。
    """
    matches = list(PREFIX_RE.finditer(line))
    if len(matches) < 2:
        return [line]

    segments: List[str] = []
    cursor = 0
    scan_from = 0
    while True:
        cut = None
        for match in PREFIX_RE.finditer(line, scan_from):
            if match.start() <= cursor:
                continue
            if not normalize_channel(match.group(1), aliases):
                continue                      # 括号里的不是频道名（可能是正文里的括号）
            head = _strip_leading_prefixes(line[cursor:match.start()], aliases)
            if not head:
                continue                      # 前面只有前缀，说明这是同一条消息的第二个前缀
            name, _body = _split_speaker(head)
            if name or textutil.has_cjk(head):
                cut = match.start()
                break
        if cut is None:
            break
        segments.append(line[cursor:cut].strip())
        cursor = cut
        scan_from = cut
    segments.append(line[cursor:].strip())
    return [segment for segment in segments if segment]


class ChatParser:
    """把一帧 OCR 结果解析成事件列表。"""

    def __init__(self, aliases: Optional[Dict[str, str]] = None,
                 infer_channel_from_previous: bool = True) -> None:
        self.aliases = dict(DEFAULT_ALIASES)
        if aliases:
            self.aliases.update(aliases)
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
                event = Event(KIND_CHAT, textutil.clean_body(body), channel, name,
                              raw_line, [s[2] for s in spans], prefix_text)
                events.append(event)
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
            body = textutil.clean_body(body)
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
