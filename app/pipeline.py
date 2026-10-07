"""后台流水线：截图 → OCR → 解析 → 去重 →（术语保护）→ 翻译 → 交回界面。

线程模型（比旧版干净，也更容易查问题）：

* 采集线程：只做截图 / OCR / 解析 / 去重，然后把任务放进队列。
* 翻译线程：只做术语保护 / 记忆查询 / 调 API / 还原，然后把结果放进"给界面的队列"。
* 界面线程：定时从队列取结果并渲染，**从不**从后台线程碰 Tk 控件。

停止时用 Event + join，且每次启动都是全新的线程对象，不会像旧版那样
反复启停后冒出好几个线程抢同一个队列。
"""
from __future__ import annotations

import hashlib
import queue
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple

from . import capture, channels, frame, paths, textutil
from .dedup import Deduper
from .engines import BaseEngine, OfflineEngine, create_engine
from .glossary import Glossary
from .ocr import OcrEngine, group_rows, rows_to_lines as ocr_rows_to_lines
from .parser import KIND_CHAT, KIND_SYSTEM, ChatParser
from .prompt import build_messages, build_system_prompt


@dataclass
class Job:
    seq: int
    channel: str
    speaker: str
    source: str
    prefix: str = ""
    kind: str = "chat"        # chat | system（英文客户端的系统提示也要翻译）
    created: float = field(default_factory=time.time)


@dataclass
class DisplayItem:
    seq: int
    kind: str            # chat | system
    channel: str
    speaker: str
    source: str
    translated: str
    note: str = ""       # 记忆命中 / 词典直译 / 缓存 / 离线 ...
    error: str = ""
    prefix: str = ""     # 游戏里的原始前缀（如 "(小队): [小队] "），显示时 1:1 还原


class Pipeline:
    # ------------------------------------------------------------------
    # 显示过滤：只显示"可信的聊天行 / 有用的系统提示"。
    #
    # 为什么不是关键词黑名单：战利品/宝箱/任务面板会被 OCR 反复读成各种错字
    # （"宝箱"→"玉相"、"战利品"→"或利品/战利丽"），黑名单永远列不全；而
    # 关键词白名单又会把 OCR 读花的玩家发言一起丢掉（实测丢过
    # "(小队):[小队]S Sinoke:guihuo,nikyireddoor"）。所以这里按"结构"判断：
    #
    #   玩家发言 = 频道前缀 + 拉丁名字 + 冒号 + 正文（名字允许前面有几个杂质字符）
    #   系统提示 = 频道前缀 + 中文提示，且提示里含"组队/生死/队长"这类关键动作
    #   其它      = 面板文字、OCR 碎片，一律不显示
    # ------------------------------------------------------------------

    # 翻译缓存的容量。聊天里"重复出现"的句子很多（ty / omw / 同一句被 OCR 读成
    # 好几个版本），缓存越大、白交的接口钱越少。一条记录只有几十字节，
    # 放宽到几千条也就几百 KB —— 比多调一次接口便宜得多。
    CACHE_MAX_ITEMS = 4000          # 内存里最多留这么多条
    CACHE_TRIM_KEEP = 2500          # 超了就只留最新的这么多
    CACHE_PERSIST_ITEMS = 3000      # 落盘保留多少（下次启动还能直接命中）

    # 面板特征：命中任意一条就认为不是聊天内容（数字+天/时、拾取次数、重置时间…）。
    # 这些是"面板长得什么样"，所以对错字有免疫力 —— 不管 OCR 把"宝箱"读成什么，
    # "被拾取次数 / 重置时间 / X天Y时" 这些结构都还在。
    PANEL_PATTERNS = (
        re.compile(r"\d+\s*天\s*\d+\s*[时小]"),            # 0天19时2分57秒
        re.compile(r"拾取次数"),
        re.compile(r"重置时间"),
        re.compile(r"掠夺"),
        re.compile(r"任务名称"),
        re.compile(r"宝箱信息"),
        re.compile(r"战利品信息"),
        re.compile(r"宝箱已禁用"),
        re.compile(r"从.{0,4}(宝|玉|箱|相).{0,4}取"),      # 从宝箱中取出 / 从玉相取正
    )

    # 有用的系统提示里的"动作词"。命中任意一个就显示，其余系统消息不显示。
    # 只用短词（不用整句）是为了容忍 OCR 错字："已断线"读成"己断线"也能命中"断线"。
    # 注意：**不要**放"队伍/小队/公会"这种频道名 —— 面板碎片里也常有这几个字，
    # 放了会把 "(小队):[小 1.tor 2.投入…" 这种乱码当成有用提示显示出来。
    NOTICE_TOKENS = (
        "加入", "已加入", "离开", "退出", "移出", "踢出", "解散", "邀请",
        "死亡", "阵亡", "断线", "掉线", "离线", "上线", "复活", "重连",
        "队长", "锁定", "难度", "组队",
    )
    # 英文客户端：系统提示也是英文（"Medics has logged on."），给一组对应的关键词。
    # 匹配时对英文大小写不敏感（见 is_useful_notice）。
    NOTICE_TOKENS_EN = (
        "logged on", "logged off", "has died", "have died", "you died",
        "has joined", "has left", "left the party", "joined the party",
        "you invite", "invites you", "party chat room", "party leader",
        "you are now the", "has disconnected", "connection lost",
        "you have joined", "has been removed", "removed from the party",
    )
    # 英文系统提示超过这个长度就不送翻译了：正常一条提示没那么长，
    # 多半是 OCR 把好几行挤成一条（真送过去，模型会把里面的频道名也一起翻掉）。
    SYSTEM_TRANSLATE_MAX = 240


    def __init__(self, config: dict, memory, glossary: Glossary,
                 ui_queue: "queue.Queue[dict]") -> None:
        self.config = config
        self.memory = memory
        self.glossary = glossary
        self.ui_queue = ui_queue

        self.ocr = OcrEngine()
        self.parser = ChatParser(channels.alias_table(config))
        self.deduper = Deduper(ttl_seconds=float(config.get("dedup_ttl_seconds", 90)))
        self.engine: BaseEngine = OfflineEngine()
        self.engine_note = ""
        self.reload_engine()

        self._jobs: "queue.Queue[Job]" = queue.Queue(maxsize=int(config.get("max_queue", 30)))
        self._system_events_queue: "queue.Queue[str]" = queue.Queue()
        # 已就绪、只等按序号显示的内容（系统消息不用翻译，先放这里排队）
        self._ready_queue: "queue.Queue[DisplayItem]" = queue.Queue()
        self._pending_display: Dict[int, DisplayItem] = {}
        self._next_display_seq = 1
        self._skipped_seqs = set()
        self._skip_lock = threading.Lock()
        self._stop = threading.Event()
        self._running = False        # 是否真的已经启动过（不能用 Event 状态代替）
        self._capture_thread: Optional[threading.Thread] = None
        self._translate_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._seq = 0
        self._last_frame_signature = ""
        self._idle_frames = 0
        self._last_lines: List[Tuple[float, float, str]] = []
        # 最近一次识别到的画面（只给「反馈问题」打包当证据用；就是框选的那一小块）
        self.last_frame_image = None
        self._frames_since_full = 0
        self._fast_capture_ok = None      # None=还没校验, True/False=已确定
        # 最近显示过的内容（原文 + 译文），用于"模糊去重"和"别把自己显示的内容又识别一遍"。
        # OCR 每帧会把同一句读得略有不同（lgotone / |gotone / Igotone），精确指纹拦不住；
        # 而主窗口又贴在游戏上，被 OCR 读回来的自己的译文也要挡住，否则会自我循环。
        self._recent: Deque[Tuple[float, str, str]] = deque(maxlen=80)   # (时间, 原文, 说话人)
        self._recent_lock = threading.Lock()
        self._history: Deque[Tuple[str, str]] = deque(maxlen=12)
        # 给"根据上下文推荐回复"用的：带说话人的最近聊天
        self._recent_chat: Deque[Tuple[str, str, str]] = deque(maxlen=20)
        self._system_events: Deque[str] = deque(maxlen=6)
        # 翻译缓存：key → {"src": 原文, "zh": 译文}
        # 存原文是为了两件事：① 排查；② 「挖掘高频短语」要拿"英文 → 中文"的真实数据
        # （以前只存哈希，历史翻译就白白丢掉了）
        self._cache: Dict[str, dict] = {}
        self._cache_dirty = False
        self._last_notice = 0.0
        self._unknown_channels: Dict[str, float] = {}   # 没登记的频道 → 最近一次出现时间
        self.screen_size = None      # Tk 认为的屏幕尺寸，由界面线程写入
        self.stats = {
            "frames": 0,
            "ocr_lines": 0,
            "messages": 0,
            "translated": 0,
            "memory_hits": 0,
            "cache_hits": 0,
            "dict_hits": 0,
            "api_calls": 0,
            "api_errors": 0,
            "dropped": 0,
            "filtered": 0,
            "untranslated": 0,
            "skipped_frame": 0,
            "band_ocr": 0,
        }
        self._load_cache()

    # ------------------------------------------------------------ 生命周期
    @property
    def running(self) -> bool:
        # 注意：不能写成 not self._stop.is_set()。新建的 Event 本来就是未设置状态，
        # 那样会导致"没点监听也显示成正在运行"。
        return self._running

    def start(self) -> None:
        if self._capture_thread and self._capture_thread.is_alive():
            return
        self._stop.clear()
        self._running = True
        self._last_frame_signature = ""
        self._last_lines = []
        self._frames_since_full = 0
        # 显示队列接着上一个序号继续，避免重启监听后卡在等一个永远不来的序号
        with self._skip_lock:
            self._skipped_seqs.clear()
        self.forget_recent()          # 重新开始监听后，旧内容允许重新显示
        self._pending_display.clear()
        self._next_display_seq = self._seq + 1
        self._capture_thread = threading.Thread(
            target=self._capture_loop, name="capture", daemon=True)
        self._translate_thread = threading.Thread(
            target=self._translate_loop, name="translate", daemon=True)
        self._capture_thread.start()
        self._translate_thread.start()
        self._lower_thread_priority(self._capture_thread)
        # 引擎名不上状态栏：那一行本来就挤，引擎（含模型名）放在底栏的悬停提示里，
        # 设置 → 翻译 也能看到。这里只在"引擎被回退"时补一句提醒。
        note = "（%s）" % self.engine_note if self.engine_note else ""
        self._emit_status("已开始监听%s" % note, "info")

    @staticmethod
    def _lower_thread_priority(thread) -> None:
        """把后台线程也降到低于正常（Windows），进一步给游戏让路。"""
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            kernel32.OpenThread.restype = ctypes.c_void_p
            kernel32.OpenThread.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
            kernel32.SetThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
            handle = kernel32.OpenThread(
                0x0040 | 0x0200, False, thread.ident)     # QUERY | SET_INFORMATION
            if handle:
                kernel32.SetThreadPriority(handle, -1)                 # BELOW_NORMAL
                kernel32.CloseHandle(handle)
        except Exception:
            pass

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        self._running = False
        for thread in (self._capture_thread, self._translate_thread):
            if thread and thread.is_alive():
                thread.join(timeout=timeout)
        self._capture_thread = None
        self._translate_thread = None
        self.deduper.clear()
        self._emit_status("已停止监听", "info")

    def shutdown(self) -> None:
        self.stop()
        self.flush_cache()
        self.ocr.close()

    # ------------------------------------------------------------ 配置/引擎
    def reload_engine(self) -> None:
        engine, note = create_engine(self.config)
        self.engine = engine
        self.engine_note = note

    def set_screen_size(self, width: int, height: int) -> None:
        """界面把 Tk 的屏幕尺寸告诉流水线，用于换算截图坐标（只在界面线程调用）。"""
        try:
            width, height = int(width), int(height)
        except Exception:
            return
        if width > 0 and height > 0:
            self.screen_size = (width, height)

    def _ocr_threads(self) -> int:
        """OCR 推理线程数（默认 2，别吃满核心，否则游戏会卡）。"""
        try:
            return max(0, min(8, int(self.config.get("ocr_threads", 2) or 0)))
        except Exception:
            return 2

    def _ensure_ocr(self) -> bool:
        # 置信度门槛跟着设置走（背景花时把"像字其实是噪点"的行丢掉）
        try:
            self.ocr.set_min_score(float(self.config.get("ocr_min_score", 0.5)))
        except Exception:
            pass
        # 半透明背景压平也跟着设置走
        self.ocr.set_flatten(self._flatten_enabled())
        # 检测尺寸限幅（窄条不再被放大十几倍）
        self.ocr.set_det_cap(bool(self.config.get("ocr_det_cap", True)))
        if self.ocr.available():
            return True
        return self.ocr.load(self._ocr_threads())

    def _flatten_enabled(self) -> bool:
        """是否把聊天框的半透明背景压平（识别和指纹都用这个开关）。"""
        return bool(self.config.get("flatten_background", True))

    def _capture_frame(self, region):
        """抓一帧画面。

        默认走"只抓指定区域"的快速方式（GDI BitBlt），比 Pillow 先抓整屏再裁剪省得多。
        第一次会拿它和系统截图比对：不一致就永久回退 —— 宁可不快，也不能抓到错图。
        """
        if str(self.config.get("capture_backend", "auto")).lower() == "pillow" \
                or self._fast_capture_ok is False:
            return capture.grab(region, self.screen_size)

        fast = capture.grab_fast(region, self.screen_size)
        if fast is None:
            self._fast_capture_ok = False
            self._emit_status("快速截图不可用，已回退到系统截图", "warn")
            return capture.grab(region, self.screen_size)

        if self._fast_capture_ok is None:
            # 校验快速截图是否和系统截图一致。比对多次：游戏画面在动的时候
            # 单次比对可能刚好不巧（两边差一点），所以只要有一次对得上就采用。
            for _attempt in range(3):
                reference = capture.grab(region, self.screen_size)
                if reference is None:
                    continue
                if capture.images_similar(fast, reference):
                    self._fast_capture_ok = True
                    self._emit_status("已启用快速截图（只抓区域，更省 CPU）", "info")
                    return fast
                fast = capture.grab_fast(region, self.screen_size) or fast
            self._fast_capture_ok = False
            self._emit_status("快速截图与系统截图不一致，已回退到系统截图", "warn")
            return capture.grab(region, self.screen_size) or fast
        return fast

    def _current_interval(self, idle: bool = False) -> float:
        """两次截图之间的间隔；一直没新内容时自动放慢（省 CPU）。"""
        try:
            base = max(0.3, float(self.config.get("interval_ms", 1200)) / 1000.0)
        except Exception:
            base = 1.2
        if idle and self.config.get("idle_backoff", True) and self._idle_frames >= 5:
            return min(base * 2.0, 3.0)
        return base

    def reload_glossary(self, glossary: Glossary) -> None:
        self.glossary = glossary
        self.invalidate_cache()

    def invalidate_cache(self) -> None:
        self._cache.clear()
        self._cache_dirty = True

    def apply_config(self) -> None:
        """配置改变后调用（阈值、术语、引擎等）。"""
        self.deduper.ttl = float(self.config.get("dedup_ttl_seconds", 90))
        self.parser = ChatParser(channels.alias_table(self.config))
        self._last_lines = []          # 区域/参数变了，缓存的行作废
        self._frames_since_full = 0
        self.reload_engine()

    # ------------------------------------------------------------ 对外接口
    def test_capture(self) -> Tuple[object, List[str]]:
        """手动测试：抓一帧 + 识别，返回 (图片, 识别到的文本行)。"""
        region = self.config.get("region")
        if not region:
            return None, []
        if self.screen_size:
            capture.measure_scale(self.screen_size, force=True)
        image = self._capture_frame(region)
        if image is None:
            return None, []
        self._ensure_ocr()
        items = self.ocr.recognize(image, self._upscale_for(image))
        if self.config.get("merge_same_row", True):
            items = group_rows(items)
        return image, [text for text, _box in items]

    def status(self) -> Dict[str, object]:
        with self._lock:
            stats = dict(self.stats)
        return {
            "running": self.running,
            "pending": self._jobs.qsize(),
            "queue_size": self._jobs.maxsize,
            "engine": self.engine.describe(),
            "engine_note": self.engine_note,
            "cache_size": len(self._cache),
            "ocr_ready": self.ocr.available(),
            "dedup_size": len(self.deduper),
            "stats": stats,
        }

    # ------------------------------------------------- 给"推荐回复"用的上下文
    def recent_context(self, limit: int = 8) -> List[Tuple[str, str, str]]:
        """最近几条玩家发言：[(说话人, 英文原文, 中文译文), ...]（从早到晚）。"""
        items = list(self._recent_chat)
        return items[-max(1, int(limit)):] if items else []

    def recent_system_events(self, limit: int = 4) -> List[str]:
        """最近的系统提示（组队/生死这类），作为推荐回复的补充上下文。"""
        self._drain_system_events()
        items = list(self._system_events)
        return items[-max(1, int(limit)):]

    # ------------------------------------------------------------ 缓存
    def _load_cache(self) -> None:
        raw = paths.read_json(paths.CACHE_PATH, {})
        if isinstance(raw, dict) and isinstance(raw.get("items"), dict):
            items = {}
            for key, value in raw["items"].items():
                if isinstance(value, str):          # 老格式：只存了译文
                    items[key] = {"src": "", "zh": value}
                elif isinstance(value, dict) and isinstance(value.get("zh"), str):
                    items[key] = {"src": str(value.get("src") or ""),
                                  "zh": value["zh"]}
            self._cache = items

    def flush_cache(self) -> None:
        if not self._cache_dirty:
            return
        items = list(self._cache.items())[-self.CACHE_PERSIST_ITEMS:]
        if paths.write_json(paths.CACHE_PATH, {"version": 1, "items": dict(items)}):
            self._cache_dirty = False

    def cached_pairs(self) -> List[Tuple[str, str]]:
        """缓存里"翻译过的 (原文, 译文)" —— 给「挖掘高频短语」当原料（只读）。"""
        from . import phrases as phrases_module

        try:
            return phrases_module.pairs_from_cache(self._cache)
        except Exception:                          # noqa: BLE001
            return []

    def _cache_key(self, masked: str) -> str:
        """缓存键**只**看：这句打完占位符后的样子 + 引擎 + 翻译模式。

        以前这里还带了"术语表签名 + 记忆条数 + 术语条数"—— 那是多余的：术语一变，
        句子里真的受影响的那些句子，占位符本身就变了（masked 不同 → 键不同 → 不会
        命中）；没受影响的句子 masked 一模一样，旧译文照样是对的。
        带上它们的后果是：**每次词典更新、每加一个词都会把整份翻译缓存作废**，
        之前翻过的句子又去调用一遍接口 —— 白花钱。实测改掉后加词不再清缓存。
        """
        parts = [
            masked,
            self.engine.describe(),
            str(self.config.get("translate_mode", "quality")),
        ]
        return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()

    # ------------------------------------------------------------ 采集线程
    def _capture_loop(self) -> None:
        announced_ocr = False
        self._calibrate_once()
        while not self._stop.is_set():
            started = time.time()
            interval = self._current_interval()

            region = self.config.get("region")
            if not region:
                self._notice("还没有框选聊天区域", "warn", min_gap=10.0)
                self._stop.wait(0.5)
                continue

            image = self._capture_frame(region)
            if image is None:
                self._notice("截图失败（区域是否在屏幕内？）", "error", min_gap=10.0)
                self._stop.wait(1.0)
                continue

            # 先用"缩略图比对"判断画面有没有变化：聊天框多数时间是静止的，
            # 没变就整帧跳过 OCR（这是监听时最省 CPU 的一招）。
            frame_signature = capture.frame_signature(image, flatten=self._flatten_enabled())
            changed, first_row, last_row = frame.analyse_frame(
                frame_signature, self._last_frame_signature)
            if self.config.get("skip_identical_frame", True) and not changed:
                self._idle_frames += 1
                self.stats["skipped_frame"] = self.stats.get("skipped_frame", 0) + 1
                self._stop.wait(self._current_interval(idle=True))
                continue
            self._last_frame_signature = frame_signature
            self._idle_frames = 0

            if not self.ocr.available():
                if not announced_ocr:
                    self._emit_status("正在加载 OCR 模型（第一次会慢几秒）…", "info")
                    announced_ocr = True
                if not self._ensure_ocr():
                    self._emit_status("OCR 加载失败：%s" % self.ocr.error, "error")
                    self._stop.wait(5.0)
                    continue
                self._emit_status("OCR 模型已就绪", "info")

            self.stats["frames"] += 1
            scale = self._upscale_for(image)
            self.last_frame_image = image      # 留一张给"反馈问题"当证据（只有框选区域）

            # 默认只重新识别"变了的那几行"：上面没变的部分直接沿用上一帧的识别结果。
            # 前提是变化发生在中下部、且不是整屏大改；每隔若干帧或变化太大时
            # 做一次整帧识别来校准，避免滚动/淡出导致的偏差累积。
            use_band = bool(
                self.config.get("band_ocr", True)
                and self._last_lines
                and first_row >= 6
                and (last_row - first_row + 1) <= 30
                and self._frames_since_full < 20
            )
            band_image, offset = image, 0.0
            if use_band:
                y_start, _y_end = frame.band_pixels((first_row, last_row), image.height)
                offset = self._pixel_offset_to_tk(region, image, y_start)
                band_region = [region[0], int(region[1] + offset), region[2], region[3]]
                band = self._capture_frame(band_region)
                if band is not None and band.height > 4:
                    band_image = band
                else:
                    use_band, offset = False, 0.0

            items = self.ocr.recognize(band_image, scale)
            if self.config.get("merge_same_row", True):
                items = group_rows(items)
            fresh_lines = ocr_rows_to_lines(items, scale, offset)
            if use_band:
                all_lines = frame.merge_lines(self._last_lines, fresh_lines, offset)
                self._frames_since_full += 1
                self.stats["band_ocr"] = self.stats.get("band_ocr", 0) + 1
            else:
                all_lines = fresh_lines
                self._frames_since_full = 0
            self._last_lines = all_lines
            lines = [text for _top, _bottom, text in all_lines]
            self.stats["ocr_lines"] = len(lines)
            if lines:
                self._handle_lines(lines)

            elapsed = time.time() - started
            self._stop.wait(max(0.2, interval - elapsed))

    @staticmethod
    def _pixel_offset_to_tk(region, image, y_pixels: float) -> float:
        """把"截图像素里的 y 偏移"换算成 Tk 坐标偏移（两者可能因 DPI 差一个比例）。"""
        try:
            tk_height = float(region[3]) - float(region[1])
            if image.height <= 0 or tk_height <= 0:
                return float(y_pixels)
            return float(y_pixels) * tk_height / float(image.height)
        except Exception:
            return float(y_pixels)

    def _calibrate_once(self) -> None:
        """启动时实测一次"截图空间 / Tk 空间"的比例，不一致就自动校正并提示。"""
        if not self.screen_size:
            return
        try:
            sx, sy = capture.measure_scale(self.screen_size)
        except Exception:
            return
        if abs(sx - 1.0) > 0.01 or abs(sy - 1.0) > 0.01:
            space = capture.capture_space()
            self._emit_status(
                "截图坐标已自动校正 x%.3f/y%.3f（Tk 屏幕 %s，截图空间 %s）"
                % (sx, sy, self.screen_size, space), "warn")

    def _handle_lines(self, lines: List[str]) -> None:
        events = self.parser.parse(lines)
        # 只显示"频道表里有、而且开着"的频道；认不出来的频道一律不显示
        # （玩家删掉的频道不该再冒出来）
        enabled = channels.enabled_map(self.config)

        for event in events:
            if event.kind == KIND_SYSTEM:
                if event.text:
                    text = event.text
                    # 1) 战利品/宝箱信息面板：对理解聊天没帮助，而且会被反复识别成错字版本
                    if self.is_panel_text(text):
                        self._count_filtered()
                        continue
                    # 2) 只显示与队伍/生死/队长相关的提示，其余系统消息不显示
                    if self.config.get("system_whitelist", True) and \
                            not self.is_useful_notice(text):
                        self._count_filtered()
                        continue
                    # 3) 系统消息也要去重：它会在聊天框里停留很久，每帧重新识别一遍
                    #    就会把同一条消息无限重复地打到屏幕上（也会灌满上下文）。
                    system_fp = textutil.fingerprint(text)
                    if not system_fp or self.deduper.check(system_fp):
                        continue
                    if self._seen_recently(text):
                        continue
                    self._remember(text)      # 立刻记下，后面几帧的错字版本就能被拦住
                    # 系统消息照样进上下文（翻译时有用），但是否显示听用户的
                    self._system_events_queue.put(text)
                    if self.config.get("show_system", True) and (
                            not event.channel or enabled.get(event.channel, True)):
                        if textutil.has_cjk(text) or len(text) > self.SYSTEM_TRANSLATE_MAX:
                            # 中文客户端的系统提示本来就是中文 → 原样显示，不花接口
                            # （太长的英文提示也不翻：多半是 OCR 把多行挤成一条，
                            #   翻了会把频道名一起翻掉，宁可原样显示）
                            self._ready_queue.put(DisplayItem(
                                seq=self._next_seq(), kind="system",
                                channel=event.channel, speaker="", source=text,
                                translated=text, note="", prefix=event.prefix_text))
                        else:
                            # 英文客户端（游戏语言是英文）：系统提示也是英文，用户看不懂 →
                            # 和聊天一样送翻译；显示仍按"系统消息"的样式（kind 不变）
                            self.stats["messages"] += 1
                            job = Job(seq=self._next_seq(), channel=event.channel,
                                      speaker="", source=text,
                                      prefix=event.prefix_text, kind="system")
                            try:
                                self._jobs.put_nowait(job)
                            except queue.Full:
                                try:
                                    self._jobs.get_nowait()
                                    self._jobs.put_nowait(job)
                                except Exception:
                                    pass
                continue

            if event.kind != KIND_CHAT:
                continue
            # 识别与过滤都跟着「设置 → 频道」那张表走：
            #   * 表里开着 → 正常翻译显示
            #   * 表里关掉 → 静默跳过（是玩家自己关的）
            #   * 表里没有（含"有括号前缀但认不出频道"的）→ 不显示，但要计数 +
            #     隔一会儿提示一次"去频道页加一行"，免得游戏更新后聊天悄悄消失
            if not self._channel_ok(event, enabled):
                continue
            # OCR 有时把面板文字粘在玩家正文后面（"堡垒? 错误):你的队友已经…"），
            # 这种正文翻出来一定是垃圾，直接不显示。
            if self.is_panel_text(event.text):
                self._count_filtered()
                continue
            # 指纹带上说话人（放前面）：**不同的人说同一句话要各显示一条**
            # （"ty"、"ok" 这种短句很常见），但同一个人被 OCR 读花/读短时照样去重
            # —— Deduper 的"前缀相同""只差一个字"两条规则都是按前缀比的，
            # 说话人放在前面正好不影响它们。
            fingerprint = textutil.fingerprint(event.text)
            if event.speaker:
                fingerprint = "%s|%s" % (textutil.fingerprint(event.speaker),
                                         fingerprint)
            if not fingerprint or self.deduper.check(fingerprint):
                continue
            # 文字没变但 OCR 每帧读得略有不同时，精确指纹拦不住，用模糊比对补一刀
            # （实测：同一句 "I got one-shot by it today" 被读成三种写法，窗口里显示三遍）
            if self._seen_recently(event.text, event.speaker):
                self._count_filtered()
                continue
            # 立刻记下来（不等翻译完）：同一句在翻译这一两秒里还会被 OCR 读到好几次
            self._remember(event.text, speaker=event.speaker)

            self.stats["messages"] += 1
            job = Job(seq=self._next_seq(), channel=event.channel,
                      speaker=event.speaker, source=event.text,
                      prefix=event.prefix_text)
            try:
                self._jobs.put_nowait(job)
            except queue.Full:
                try:
                    dropped = self._jobs.get_nowait()
                    with self._skip_lock:
                        self._skipped_seqs.add(dropped.seq)
                    self.stats["dropped"] += 1
                except queue.Empty:
                    pass
                try:
                    self._jobs.put_nowait(job)
                except queue.Full:
                    pass

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _count_filtered(self) -> None:
        """记一笔"被过滤掉的噪音行"，界面上能看到过滤器确实在干活。"""
        self.stats["filtered"] = self.stats.get("filtered", 0) + 1

    def _channel_ok(self, event, enabled: dict) -> bool:
        """这条玩家发言的频道该不该处理（判断依据就是 设置 → 频道 那张表）。"""
        channel = getattr(event, "channel", "") or ""
        if channel:
            if channel in enabled:
                return bool(enabled[channel])
            # 表里没有这个频道 → 不显示。但提示一下，别让玩家以为程序坏了
            self._count_filtered()
            self._notice_unknown_channel(getattr(event, "prefix_text", "") or channel)
            return False
        if getattr(event, "prefix_text", ""):
            # 有括号前缀却认不出是哪个频道（OCR 读花，或者游戏新增了频道）
            self._count_filtered()
            self._notice_unknown_channel(event.prefix_text)
            return False
        return True          # 没前缀的（续行 / 丢了前缀的名字行）照旧处理

    def _notice_unknown_channel(self, label: str) -> None:
        """提示"有没登记的频道"，同一个频道 60 秒内只说一次。

        提示里给的是**收拾干净的名字**（"(Guild:): " → "Guild"），
        英文客户端的玩家可以直接照抄到 设置 → 频道 里加一行。
        """
        label = " ".join(str(label or "").split())
        if not label:
            return
        now = time.time()
        self._unknown_channels[label] = now
        for name in [k for k, when in self._unknown_channels.items()
                     if now - when > 60]:
            self._unknown_channels.pop(name, None)
        recent = sorted(self._unknown_channels)
        names = "、".join(self._clean_channel_label(item) or item
                         for item in recent[:3])
        self._notice("有没登记的频道：%s（设置 → 频道 里按这个名字加一行，就能显示/翻译）"
                     % names, "warn", min_gap=30.0)

    @staticmethod
    def _clean_channel_label(label: str) -> str:
        """把 "(Guild:): " 这种原始前缀收拾成能直接照抄的频道名 "Guild"。"""
        text = " ".join(str(label or "").split())
        if not text:
            return ""
        first = text.split(" ")[0]          # "(Guild:): [Guild]" → 取第一段
        return first.strip("()[]{}（）【】<>《》:：;；").strip()

    def _seen_recently(self, text: str, speaker: str = "") -> bool:
        """这条内容最近是不是已经出现过（允许 OCR 读花几个字）。

        比较对象包括**已经显示出去的原文和译文** —— 主窗口就贴在游戏上，
        万一被框选区域盖住，OCR 会把我们自己的译文读回来，那就是无限循环了。
        时间窗跟"同一句多久内不重复翻译"（dedup_ttl_seconds，默认 90 秒）一致。

        `speaker` 也参与判断：**不同的人说同一句话要各显示一条**
        （"Huzi-2告诉你: halo nihao" 和 "你对 Huzi-2说: halo nihao" 是两条）。
        说话人自己允许 OCR 读花，而且门槛比正文更宽松：名字短、又常被粘上相邻
        字符（实测 `[小队jKyiae:` → "jKyiae"），所以用 `same_player_name()` 比。
        """
        if not text or len(text.strip()) < 2:
            return False
        now = time.time()
        window = self._dedup_window()
        with self._recent_lock:
            while self._recent and now - self._recent[0][0] > window:
                self._recent.popleft()
            for _stamp, old, old_speaker in self._recent:
                if not textutil.same_ocr_message(text, old):
                    continue
                if speaker and old_speaker and speaker != old_speaker \
                        and not textutil.same_player_name(speaker, old_speaker):
                    continue          # 说话人明显不同 → 这是另一个人说的同一句话
                return True
        return False

    def _dedup_window(self) -> float:
        try:
            return max(5.0, float(self.config.get("dedup_ttl_seconds", 90)))
        except Exception:
            return 90.0

    def forget_recent(self) -> None:
        """忘掉"最近显示过什么"（重新开始监听、或测试时用）。"""
        with self._recent_lock:
            self._recent.clear()

    def _remember(self, *texts: str, speaker: str = "") -> None:
        """把"已经显示出去的内容"记下来，供 _seen_recently 比对。"""
        now = time.time()
        with self._recent_lock:
            for text in texts:
                if text and len(text.strip()) >= 2:
                    self._recent.append((now, text, speaker))

    @classmethod
    def is_panel_text(cls, text: str) -> bool:
        """是不是战利品/宝箱/任务面板那种"面板文字"（对理解聊天没帮助）。"""
        return any(pattern.search(text or "") for pattern in cls.PANEL_PATTERNS)

    @classmethod
    def is_useful_notice(cls, text: str) -> bool:
        """是不是值得显示的系统提示（组队/生死/队长…）。"""
        body = text or ""
        if any(token in body for token in cls.NOTICE_TOKENS):
            return True
        lowered = body.lower()          # 英文客户端：大小写不敏感
        return any(token in lowered for token in cls.NOTICE_TOKENS_EN)

    def _upscale_for(self, image) -> float:
        """聊天框很小时先把图放大再 OCR，识别质量更好。"""
        setting = self.config.get("ocr_upscale", "auto")
        try:
            if isinstance(setting, (int, float)):
                return max(1.0, min(4.0, float(setting)))
            text = str(setting).strip().lower()
            if text not in ("", "auto"):
                return max(1.0, min(4.0, float(text)))
        except Exception:
            return 1.0
        try:
            if image is None:
                return 1.0
            # 1.5 倍是"识别质量"和"CPU 占用"的折中：2 倍明显更吃 CPU，
            # 识别质量提升有限（真觉得漏字可以在设置里改回 2）。
            return 1.5 if image.width < 900 else 1.0
        except Exception:
            return 1.0

    # ------------------------------------------------------------ 翻译线程
    def _translate_loop(self) -> None:
        while not self._stop.is_set() or not self._jobs.empty():
            self._drain_system_events()
            self._collect_ready()
            self._flush_display()
            try:
                job = self._jobs.get(timeout=0.3)
            except queue.Empty:
                continue
            try:
                item = self._process(job)
            except Exception as exc:                     # 兜底：绝不让线程死掉
                item = DisplayItem(job.seq, job.kind, job.channel, job.speaker,
                                   job.source, job.source, error="内部错误：%s" % exc,
                                   prefix=job.prefix)
            self._pending_display[item.seq] = item
            self._flush_display()
        # 收尾：把还没发出去的按顺序发完
        self._collect_ready()
        self._flush_display(force=True)

    def _collect_ready(self) -> None:
        """把系统消息（不需要翻译）收进待显示表。"""
        while True:
            try:
                item = self._ready_queue.get_nowait()
            except queue.Empty:
                return
            self._pending_display[item.seq] = item

    def _flush_display(self, force: bool = False) -> None:
        """严格按序号显示，保证窗口里的顺序和游戏聊天框一致。

        系统消息不用翻译、立刻就能显示；玩家发言要等接口返回。谁先到就先显示的话
        顺序就乱了，所以统一在这里按 seq 排队发出。
        """
        if force:
            for seq in sorted(self._pending_display):
                self._push_display(self._pending_display.pop(seq))
            self._next_display_seq = self._seq + 1
            return

        while True:
            seq = self._next_display_seq
            item = self._pending_display.pop(seq, None)
            if item is not None:
                self._push_display(item)
                self._next_display_seq = seq + 1
                continue
            with self._skip_lock:
                skipped = seq in self._skipped_seqs
                if skipped:
                    self._skipped_seqs.discard(seq)
            if skipped:                        # 这条任务被丢弃了，跳到下一条
                self._next_display_seq = seq + 1
                continue
            break

    def _drain_system_events(self) -> None:
        while True:
            try:
                self._system_events.append(self._system_events_queue.get_nowait())
            except queue.Empty:
                return

    def _process(self, job: Job) -> DisplayItem:
        source = job.source

        # 0) 原文本来就不是英文（玩家说中文）→ 直接显示
        if not textutil.has_latin(source):
            return DisplayItem(job.seq, job.kind, job.channel, job.speaker,
                               source, source, note="原文非英文", prefix=job.prefix)

        # 1) 句子记忆：你纠正过的句子，直接给结果，不花 API
        remembered = self.memory.phrase(source)
        if remembered:
            self.stats["memory_hits"] += 1
            return DisplayItem(job.seq, job.kind, job.channel, job.speaker,
                               source, remembered, note="记忆命中", prefix=job.prefix)

        # 2) 先保护 URL，再做术语保护
        #    顺序不能反：反了的话链接里的 store/fire 这类词会被术语表替换掉，
        #    变成 "https://商城.steampowered.com/..." 这种结果。
        protected, urls = textutil.protect_urls(source)
        masked, mapping, unknown = self.glossary.protect(protected)

        note = ""
        error = ""
        translated = ""

        # 3) 整句都被术语表覆盖 → 不用调 API
        if not textutil.has_latin_outside_marks(masked):
            translated = textutil.restore_urls(masked, urls)
            self.stats["dict_hits"] += 1
            note = "词典直译"
        else:
            cache_key = self._cache_key(masked)
            cached = self._cache.get(cache_key)
            if isinstance(cached, dict) and cached.get("zh"):
                translated = str(cached["zh"])
                self.stats["cache_hits"] += 1
                note = "缓存"
            else:
                mode = str(self.config.get("translate_mode", "quality"))
                context_turns = max(0, int(self.config.get("context_turns", 5)))
                context = tuple(self._history)
                context = context[-context_turns * 2:] if context_turns else ()
                messages = build_messages(build_system_prompt(mode, self.memory),
                                          context, tuple(self._system_events), masked)
                timeout = float(self.config.get("timeout_seconds", 20))

                result = self.engine.translate(masked, messages, timeout=timeout)
                if isinstance(self.engine, OfflineEngine):
                    note = "离线"
                else:
                    self.stats["api_calls"] += 1
                if not result.ok:
                    self.stats["api_errors"] += 1
                    error = result.error or "翻译失败"
                translated = result.text or source
                if not error:
                    self._cache[cache_key] = {"src": source, "zh": translated}
                    self._cache_dirty = True
                    if len(self._cache) > self.CACHE_MAX_ITEMS:
                        self._cache = dict(
                            list(self._cache.items())[-self.CACHE_TRIM_KEEP:])

        translated = textutil.restore_urls(translated, urls)
        translated = self.glossary.restore(translated, mapping)
        translated = self._post_process(translated)
        if not translated:
            translated = source
        # 模型偶尔把"看不清/无法识别"这种说明当成译文回给我们（原文太乱时）。
        # 这时显示原文更有用 —— 至少知道玩家屏幕上打的是什么。
        if not error and textutil.is_refusal(translated) and textutil.has_latin(source):
            translated = source
            note = "模型没看懂（显示原文）"

        if not error:
            self._history.append((source, translated))
            # 带说话人的记录只留给"推荐回复"用（翻译提示词那条 history 结构别动）
            try:
                self._recent_chat.append((job.speaker or "", source, translated))
            except Exception:
                pass
            self.stats["translated"] += 1
            if not textutil.has_cjk(translated) and textutil.has_latin(translated):
                # 模型原样返回英文 = 没翻出来，记一笔供"学习中心"分析
                self.stats["untranslated"] += 1
                note = note or "未翻译"

        if unknown:
            problem = bool(error) or (
                not textutil.has_cjk(translated) and textutil.has_latin(translated))
            self.memory.observe(unknown, source, problem=problem)

        return DisplayItem(job.seq, job.kind, job.channel, job.speaker,
                           source, translated, note=note, error=error, prefix=job.prefix)

    @staticmethod
    def _post_process(translated: str) -> str:
        text = textutil.polish_translation(translated or "")
        text = text.strip().strip('"“”\'` ')
        for prefix in ("翻译：", "翻译:", "译文：", "译文:"):
            if text.startswith(prefix):
                text = text[len(prefix):]
                break
        return text.strip()

    # ------------------------------------------------------------ 界面通信
    def _push_display(self, item: DisplayItem) -> None:
        # 记住显示过的原文和译文：既用于模糊去重，也防止"自己的窗口被 OCR 读回来"
        self._remember(item.source, item.translated)
        self.ui_queue.put({"type": "message", "item": item})

    def _emit_status(self, text: str, level: str = "info") -> None:
        self.ui_queue.put({"type": "status", "text": text, "level": level})

    def _notice(self, text: str, level: str = "info", min_gap: float = 5.0) -> None:
        now = time.time()
        if now - self._last_notice < min_gap:
            return
        self._last_notice = now
        self._emit_status(text, level)
