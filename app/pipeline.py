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
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple

from . import capture, frame, paths, textutil
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
    def __init__(self, config: dict, memory, glossary: Glossary,
                 ui_queue: "queue.Queue[dict]") -> None:
        self.config = config
        self.memory = memory
        self.glossary = glossary
        self.ui_queue = ui_queue

        self.ocr = OcrEngine()
        self.parser = ChatParser(config.get("prefix_aliases"))
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
        self._frames_since_full = 0
        self._history: Deque[Tuple[str, str]] = deque(maxlen=12)
        self._system_events: Deque[str] = deque(maxlen=6)
        self._cache: Dict[str, str] = {}
        self._cache_dirty = False
        self._last_notice = 0.0
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
        self._pending_display.clear()
        self._next_display_seq = self._seq + 1
        self._capture_thread = threading.Thread(
            target=self._capture_loop, name="capture", daemon=True)
        self._translate_thread = threading.Thread(
            target=self._translate_loop, name="translate", daemon=True)
        self._capture_thread.start()
        self._translate_thread.start()
        self._lower_thread_priority(self._capture_thread)
        note = "（%s）" % self.engine_note if self.engine_note else ""
        self._emit_status("已开始监听 · 引擎 %s%s" % (self.engine.describe(), note), "info")

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
        if self.ocr.available():
            return True
        return self.ocr.load(self._ocr_threads())

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
        self.parser = ChatParser(self.config.get("prefix_aliases"))
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
        image = capture.grab(region, self.screen_size)
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

    # ------------------------------------------------------------ 缓存
    def _load_cache(self) -> None:
        raw = paths.read_json(paths.CACHE_PATH, {})
        if isinstance(raw, dict) and isinstance(raw.get("items"), dict):
            self._cache = {k: v for k, v in raw["items"].items() if isinstance(v, str)}

    def flush_cache(self) -> None:
        if not self._cache_dirty:
            return
        items = list(self._cache.items())[-800:]
        if paths.write_json(paths.CACHE_PATH, {"version": 1, "items": dict(items)}):
            self._cache_dirty = False

    def _cache_key(self, masked: str) -> str:
        parts = [
            masked,
            self.engine.describe(),
            str(self.config.get("translate_mode", "quality")),
            self.glossary.signature,
            str(len(self.memory.data.get("phrases", {}))),
            str(len(self.memory.data.get("terms", {}))),
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

            image = capture.grab(region, self.screen_size)
            if image is None:
                self._notice("截图失败（区域是否在屏幕内？）", "error", min_gap=10.0)
                self._stop.wait(1.0)
                continue

            # 先用"缩略图比对"判断画面有没有变化：聊天框多数时间是静止的，
            # 没变就整帧跳过 OCR（这是监听时最省 CPU 的一招）。
            frame_signature = capture.frame_signature(image)
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
                band = capture.grab(band_region, self.screen_size)
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
        enabled = self.config.get("channels_enabled", {}) or {}

        for event in events:
            if event.kind == KIND_SYSTEM:
                if event.text:
                    # 系统消息也要去重：它会在聊天框里停留很久，每帧重新识别一遍
                    # 就会把同一条消息无限重复地打到屏幕上（也会灌满上下文）。
                    system_fp = textutil.fingerprint(event.text)
                    if system_fp and self.deduper.check(system_fp):
                        continue
                    # 系统消息照样进上下文（翻译时有用），但是否显示听用户的
                    self._system_events_queue.put(event.text)
                    if self.config.get("show_system", True) and (
                            not event.channel or enabled.get(event.channel, True)):
                        self._ready_queue.put(DisplayItem(
                            seq=self._next_seq(), kind="system", channel=event.channel,
                            speaker="", source=event.text, translated=event.text,
                            note="", prefix=event.prefix_text))
                continue

            if event.kind != KIND_CHAT:
                continue
            if event.channel and not enabled.get(event.channel, True):
                continue
            fingerprint = textutil.fingerprint(event.text)
            if not fingerprint or self.deduper.check(fingerprint):
                continue

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
                item = DisplayItem(job.seq, "chat", job.channel, job.speaker,
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
            return DisplayItem(job.seq, "chat", job.channel, job.speaker,
                               source, source, note="原文非英文", prefix=job.prefix)

        # 1) 句子记忆：你纠正过的句子，直接给结果，不花 API
        remembered = self.memory.phrase(source)
        if remembered:
            self.stats["memory_hits"] += 1
            return DisplayItem(job.seq, "chat", job.channel, job.speaker,
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
            if cache_key in self._cache:
                translated = self._cache[cache_key]
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
                    self._cache[cache_key] = translated
                    self._cache_dirty = True
                    if len(self._cache) > 1200:
                        self._cache = dict(list(self._cache.items())[-600:])

        translated = textutil.restore_urls(translated, urls)
        translated = self.glossary.restore(translated, mapping)
        translated = self._post_process(translated)
        if not translated:
            translated = source

        if not error:
            self._history.append((source, translated))
            self.stats["translated"] += 1
            if not textutil.has_cjk(translated) and textutil.has_latin(translated):
                # 模型原样返回英文 = 没翻出来，记一笔供"学习中心"分析
                self.stats["untranslated"] += 1
                note = note or "未翻译"

        if unknown:
            problem = bool(error) or (
                not textutil.has_cjk(translated) and textutil.has_latin(translated))
            self.memory.observe(unknown, source, problem=problem)

        return DisplayItem(job.seq, "chat", job.channel, job.speaker,
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
        self.ui_queue.put({"type": "message", "item": item})

    def _emit_status(self, text: str, level: str = "info") -> None:
        self.ui_queue.put({"type": "status", "text": text, "level": level})

    def _notice(self, text: str, level: str = "info", min_gap: float = 5.0) -> None:
        now = time.time()
        if now - self._last_notice < min_gap:
            return
        self._last_notice = now
        self._emit_status(text, level)
