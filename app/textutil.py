"""文本工具：归一化、指纹、URL 保护、脏行判断。全部是纯函数，方便单测。"""
from __future__ import annotations

import re
import unicodedata
from typing import List, Tuple
import difflib

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
LATIN_RE = re.compile(r"[A-Za-z]")
WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'\-]*")

# 常见英文功能词：不进"待学习"候选，避免噪音
STOPWORDS = {
    "the", "and", "you", "for", "are", "was", "were", "but", "not", "with", "this",
    "that", "have", "has", "had", "from", "they", "them", "will", "would", "what",
    "your", "yours", "just", "can", "cant", "cannot", "get", "got", "all", "out",
    "one", "two", "now", "how", "why", "when", "where", "there", "here", "who",
    "dont", "does", "did", "im", "ive", "its", "it's", "youre", "you're", "your",
    "about", "into", "than", "then", "some", "any", "more", "most", "much", "very",
    "also", "been", "because", "before", "after", "again", "still", "only", "even",
    "like", "want", "need", "know", "think", "make", "made", "take", "took", "come",
    "coming", "going", "gone", "give", "back", "good", "well", "sure", "yeah", "yes",
    "okay", "ok", "nah", "thanks", "thank", "please", "sorry", "hey", "hello", "hi",
    "guys", "guy", "man", "dude", "bro", "let", "lets", "let's", "our", "our's",
    "their", "his", "her", "she", "him", "himself", "myself", "yourself", "me",
    "my", "we", "us", "he", "as", "at", "be", "by", "do", "go", "if", "in", "is",
    "it", "of", "on", "or", "so", "to", "up", "no", "an", "am", "new", "old",
}


def normalize(text: str) -> str:
    """全角转半角 + 去掉控制字符 + 合并空白。"""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(ch for ch in text if ord(ch) >= 32 or ch in "\n\t")
    return re.sub(r"\s+", " ", text).strip()


def fingerprint(text: str) -> str:
    """去重指纹：只保留字母/数字/汉字，去掉标点、空格、大小写差异。

    注意要保留汉字：玩家有时会打中文，如果指纹把汉字全过滤掉，
    这些消息会被当成空指纹直接丢弃（旧逻辑就踩过这个坑）。
    """
    return re.sub(r"[\W_]+", "", (text or "").lower(), flags=re.UNICODE)


def cjk_ratio(text: str) -> float:
    if not text:
        return 0.0
    hits = len(CJK_RE.findall(text))
    return hits / max(len(text.strip()), 1)


def has_cjk(text: str) -> bool:
    return bool(CJK_RE.search(text or ""))


def has_latin(text: str) -> bool:
    return bool(LATIN_RE.search(text or ""))


def detect_direction(text: str) -> str:
    """判断这段话该往哪边翻：'zh2en'（中文→英文）或 'en2zh'（英文→中文）。

    给"手动翻译"窗口用：用户不用先选方向，粘进来直接翻。
    规则故意做得简单可预期：
      * 一个汉字都没有 → 当英文（拼音、缩写也算，翻成中文正好）；
      * 有汉字 → 汉字数量不少于拉丁字母的 1/3 就当"中文为主"（"我要 go to 市场" 也算中文）；
      * 汉字很少、字母很多（"omw 马上"）→ 当英文。
    """
    body = text or ""
    cjk = len(CJK_RE.findall(body))
    if cjk == 0:
        return "en2zh"
    latin = len(LATIN_RE.findall(body))
    return "zh2en" if cjk * 3 >= latin else "en2zh"


def resolve_direction(text: str, mode: str = "auto") -> str:
    """按"手动翻译"窗口的设置决定方向：mode = auto / zh2en / en2zh。

    `zh2en` 表示"翻成英文"、`en2zh` 表示"翻成中文"（用户可以在窗口里强制指定）。
    """
    if mode in ("zh2en", "en2zh"):
        return mode
    return detect_direction(text)


def has_latin_outside_marks(text: str) -> bool:
    """判断"去掉占位符之后"还剩不剩英文字母。

    {{TERM_0}} 里本身有 TERM 这几个字母，直接用 has_latin 会误判，
    导致"整句都命中术语表"的情况还是要白跑一次接口。
    """
    return bool(LATIN_RE.search(strip_leftover_marks(text or "")))


def words(text: str) -> List[str]:
    return WORD_RE.findall(text or "")


def is_noise(text: str) -> bool:
    """OCR 碎片：没有字母也没有汉字，或者只剩 1 个字符。"""
    stripped = (text or "").strip()
    if len(stripped) <= 1:
        return True
    return not has_latin(stripped) and not has_cjk(stripped)


def clean_body(text: str) -> str:
    """清理正文：去掉首尾多余标点/包裹符号，但保留内部标点。"""
    text = normalize(text)
    text = text.strip(" \t:：-—·.,;，。")
    text = text.rstrip("([{（【<")
    return text.strip()


def truncate(text: str, limit: int) -> str:
    if text is None:
        return ""
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def collapse_doubled(text: str) -> str:
    """去掉"整条被贴了两遍"的重复（实测 OCR 会给出"位面监狱位面监狱"）。

    只处理"前后两半一字不差"的情况，所以 "ok ok"（有空格）、"hahaha" 这类
    正常口语不会被误伤。
    """
    stripped = (text or "").strip()
    size = len(stripped)
    if size >= 4 and size % 2 == 0:
        half = stripped[: size // 2]
        if half == stripped[size // 2:] and len(half.strip()) >= 2:
            return half.strip()
    return text


# --------------------------------------------------------------------------
# 模型"没看懂"的兜底：它有时会把说明当译文回给我们（"（看不清楚）"）。
# 光看"看不清楚"会误伤正常译文（"can't see clearly" 本来就可以翻成"看不清楚"），
# 所以必须再加一个强信号：要么带括号（模型在插话），要么明说"无法翻译/乱码"。
# --------------------------------------------------------------------------
# 注意别把正常译文误判成"插话"："sorry"确实该翻成"抱歉"、"I don't understand"
# 该翻成"我不懂"，所以强信号只认"无法翻译/乱码"这种模型才说得出来的话。
_REFUSAL_CORE = re.compile(r"(看\s*不\s*清|看不到|无法(?:识别|翻译|确定|理解|看清)|乱码)")
_REFUSAL_STRONG = re.compile(
    r"(无法(?:识别|翻译|确定|理解|看清)|乱码|原文不清|看不清原文|不是(?:文字|文本))")
_WRAPPED_RE = re.compile(r"^\s*[（(\[【]\s*.{0,18}?\s*[）)\]】]\s*[。.!！~～]*$")


def is_refusal(text: str) -> bool:
    """模型是不是在"插话"（说明看不懂），而不是给出译文。"""
    stripped = (text or "").strip()
    if not stripped or len(stripped) > 24:
        return False
    if _REFUSAL_STRONG.search(stripped):
        return True
    return bool(_WRAPPED_RE.match(stripped) and _REFUSAL_CORE.search(stripped))


# --------------------------------------------------------------------------
# URL 保护：OCR 常把 https:// 识别成 "https: //" 或 "https//"
# --------------------------------------------------------------------------
URL_RE = re.compile(r"(?:https?|ftp)\s*:?\s*//\s*\S+|www\s*\.\s*\S+\.\S+", re.IGNORECASE)


def protect_urls(text: str, start_index: int = 0) -> Tuple[str, List[str]]:
    """把 URL 换成 {{URL_n}} 记号，返回 (新文本, url 列表)。"""
    urls: List[str] = []

    def repl(match: "re.Match[str]") -> str:
        raw = match.group(0)
        cleaned = re.sub(r"\s+", "", raw)
        cleaned = cleaned.replace("://", "://")
        if cleaned.lower().startswith("www") and "://" not in cleaned:
            cleaned = "https://" + cleaned
        urls.append(cleaned)
        return "{{URL_%d}}" % (start_index + len(urls) - 1)

    return URL_RE.sub(repl, text), urls


LEFT_MARK_RE = re.compile(r"\{\{\s*(?:URL|TERM)\s*_?\s*(\d+)\s*\}\}|\[\s*(?:URL|TERM)\s*_?\s*(\d+)\s*\]", re.I)
URL_MARK_RE = re.compile(r"\{\{\s*URL\s*_?\s*(\d+)\s*\}\}", re.I)


def restore_urls(text: str, urls: List[str]) -> str:
    """把 {{URL_n}} 换回真实链接。"""
    if not text or not urls:
        return text or ""

    def repl(match: "re.Match[str]") -> str:
        index = int(match.group(1))
        return urls[index] if 0 <= index < len(urls) else ""

    return URL_MARK_RE.sub(repl, text)


def find_leftover_marks(text: str) -> List[str]:
    """找出模型输出里还没被还原、或者多余的记号（用于日志/自检）。"""
    return [m.group(0) for m in LEFT_MARK_RE.finditer(text or "")]


def strip_leftover_marks(text: str) -> str:
    return LEFT_MARK_RE.sub("", text or "")


def similar(first: str, second: str) -> float:
    """字符集合相似度（0~1）。用于识别"OCR 每次略有差异的同一行"。

    比 difflib 快得多，对"同一行被识别成七八种写法"这种情况足够有效。

    注意：**只适合中文**。英文只有 26 个字母，长句子的字符集合几乎一样，
    集合相似度会把两句完全不同的话判成同一条 —— 英文请用 ocr_similar()。
    """
    a, b = (first or "").strip(), (second or "").strip()
    if not a or not b:
        return 0.0
    set_a, set_b = set(a), set(b)
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / float(len(union))


def ocr_similar(first: str, second: str) -> float:
    """两段文本的相似度（0~1），按**字符顺序**比较，适合英文。

    OCR 每次读同一行都会差一点（"lgotone" / "|gotone" / "Igotone"），
    精确指纹拦不住；集合相似度又会因为英文字母太少而误判，
    所以这里用 difflib 的序列相似度（字符串都很短，开销可以忽略）。
    """
    a = re.sub(r"\s+", "", (first or "").lower())
    b = re.sub(r"\s+", "", (second or "").lower())
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


# 判成"同一条消息"的最低相似度；低于这个值还可能是同一条的条件见下
SAME_MESSAGE_RATIO = 0.85
SAME_MESSAGE_MIN_LENGTH = 6
# 长度接近的两个串之间，允许几个字认错（按编辑距离，够长的串才生效）
OCR_SAME_EDIT_RATIO = 0.25
OCR_SAME_EDIT_MIN_LENGTH = 10


def _edit_distance(first: str, second: str) -> int:
    """两个字符串的编辑距离（都是短串，滚动数组的 DP 就够）。"""
    if first == second:
        return 0
    if not first or not second:
        return len(first) or len(second)
    previous = list(range(len(second) + 1))
    for i, char_a in enumerate(first, 1):
        current = [i] + [0] * len(second)
        for j, char_b in enumerate(second, 1):
            current[j] = min(previous[j] + 1, current[j - 1] + 1,
                             previous[j - 1] + (char_a != char_b))
        previous = current
    return previous[-1]


def _whole_word_replaced(first: str, second: str) -> bool:
    """两句长度相同、词数相同，但某**一个整词**被换掉了？

    用来区分"意思真的不同"和"OCR 认花几个字母"：
      * "Medics has logged on" vs "Medics has logged off" → 换了一整个词 → 不是同一条；
      * "lgotone-shotbyittoday" vs "Igotone-shotbyittoday" → 只有一个字母不同 → 是同一条。
    英文客户端上线/下线提示只差 on/off，字符相似度高达 0.9，不特判就会被去重掉一条。
    """
    words_a = re.findall(r"[a-z0-9']+", (first or "").lower())
    words_b = re.findall(r"[a-z0-9']+", (second or "").lower())
    if len(words_a) != len(words_b) or len(words_a) < 2:
        return False
    for left, right in zip(words_a, words_b):
        if left == right or len(left) < 2 or len(right) < 2:
            continue
        # 只在两个词**长度接近**时才算"换词"：长度差很多说明是"长句被读短了"
        # （"...thisinArtofWar" vs "his"），那种要按同一条处理。
        if abs(len(left) - len(right)) > 2:
            continue
        if difflib.SequenceMatcher(None, left, right, autojunk=False).ratio() < 0.6:
            return True
    return False


def same_ocr_message(first: str, second: str) -> bool:
    """两段 OCR 文本是不是**同一条消息**的不同读法。

    两种典型情况：
      1. 少数几个字认花：`lgotone-shotbyittoday` / `Igotone-shotbyittoday`
      2. 长句被读短了：`there is something like his` 是
         `there is something like thisinArtofWar` 的开头部分
    """
    a = re.sub(r"\s+", "", (first or "").lower())
    b = re.sub(r"\s+", "", (second or "").lower())
    if not a or not b:
        return False
    if a == b:
        return True
    if _whole_word_replaced(first, second):
        return False        # 换的是整个词 → 两条不同的消息（"logged on" / "logged off"）
    if min(len(a), len(b)) < SAME_MESSAGE_MIN_LENGTH:
        return False
    if ocr_similar(a, b) >= SAME_MESSAGE_RATIO:
        return True
    # 长度接近、只是几个字母认错：difflib 对短串给分偏低（实测
    # "pls1slot.firstd" / "pis1siot.tirstd" 只有 0.80），这里按编辑距离补一刀。
    # 只对够长的串生效，而且要求长度差 ≤2 —— "need heals" / "need heals fast"
    # 这种真·追加了内容（长度差 4）不受影响。
    limit = max(2, int(min(len(a), len(b)) * OCR_SAME_EDIT_RATIO))
    if (abs(len(a) - len(b)) <= 2
            and min(len(a), len(b)) >= OCR_SAME_EDIT_MIN_LENGTH
            and _edit_distance(a, b) <= limit):
        return True
    # 被读短的那一版应该和长的那版开头几乎一致（差 6 个字符以上才算，避免
    # 把"need heals"和"need heals fast"这种真·追加内容当成重复）
    short, long_text = (a, b) if len(a) < len(b) else (b, a)
    if len(long_text) - len(short) >= 6:
        return ocr_similar(short, long_text[: len(short)]) >= 0.9
    # 反过来：短的那版是长的那版的**结尾** —— OCR 有时把行首的频道前缀
    # 漏读/多读（实测 "小队):Secondd已断线" / "Secondd已断线"）。只允许
    # 长出 ≤6 个字符，免得把"真·说了一句更长的话"当成重复。
    if len(long_text) - len(short) <= 6 and long_text.endswith(short):
        return True
    return False


def _name_key(name: str) -> str:
    """玩家名归一化：小写、去掉空格和符号（保留数字，数字是区分人的）。"""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def same_player_name(first: str, second: str) -> bool:
    """两个玩家名是不是**同一个人**的两种 OCR 读法。

    为什么单独一个函数：消息文本用的 `same_ocr_message` 有"最短 6 个字符"的门槛，
    而玩家名常常只有 3~5 个字母 —— 门槛一拦，名字被读花就会当成"另一个人"，
    同一条消息于是又显示一遍。实测反馈（v3.0.29）：OCR 把 `[小队] Kyiae:` 读成
    `[小队jKyiae:`，说话人变成 `jKyiae`，同一条 "because they respawn?" 显示了两次。

    名字里的数字（游戏给重名玩家加的 -1 / -2）代表**不同的人**，一律不合并：
    "Huzi-2" 和 "Huzi" 是两个人，"Huzi-2" 和 "Medics" 也是两个人。
    """
    a = _name_key(first)
    b = _name_key(second)
    if not a or not b:
        return False
    if a == b:
        return True
    if any(ch.isdigit() for ch in a) or any(ch.isdigit() for ch in b):
        return False
    if min(len(a), len(b)) < 3:
        return False
    if _edit_distance(a, b) <= 1:
        return True                  # 粘上一个字符，或者认错一个字母
    if min(len(a), len(b)) >= 6 and _edit_distance(a, b) <= 2:
        return True                  # 长一点的名字允许两个字母认错（Sinoke / Snioke）
    return False


# --------------------------------------------------------------------------
# 译文润色：让中文读起来像中文（模型被占位符切开后常留下多余空格）
# --------------------------------------------------------------------------
_CJK = r"\u3400-\u4dbf\u4e00-\u9fff"
_CJK_PUNCT = r"\u3000-\u303f\uff00-\uffef"


def polish_translation(text: str) -> str:
    """润色译文：去汉字间空格、标点全角、清理零碎空格。"""
    if not text:
        return ""
    result = normalize(text)

    # 1) 汉字/中文标点之间的空格全部去掉（中文不用空格分词）
    result = re.sub(r"(?<=[%s%s])\s+(?=[%s%s])" % (_CJK, _CJK_PUNCT, _CJK, _CJK_PUNCT),
                    "", result)
    # 2) 中文标点两侧的空格去掉
    result = re.sub(r"\s+([，。！？：；、）】》])", r"\1", result)
    result = re.sub(r"([（【《])\s+", r"\1", result)
    # 3) 跟在汉字后面的半角标点改成全角（不动 :) 这类表情和网址）
    result = re.sub(r"(?<=[%s]),(?=\s|[%s]|$)" % (_CJK, _CJK), "，", result)
    result = re.sub(r"(?<=[%s])\.(?=\s|[%s]|$)" % (_CJK, _CJK), "。", result)
    result = re.sub(r"(?<=[%s])\?(?=\s|[%s]|$)" % (_CJK, _CJK), "？", result)
    result = re.sub(r"(?<=[%s])!(?=\s|[%s]|$)" % (_CJK, _CJK), "！", result)
    result = re.sub(r"(?<=[%s]):(?=\s|[%s]|$)" % (_CJK, _CJK), "：", result)
    result = re.sub(r"(?<=[%s]);(?=\s|[%s]|$)" % (_CJK, _CJK), "；", result)
    # 4) 收尾：合并多余空格
    result = re.sub(r"[ \t]{2,}", " ", result)
    return result.strip()
