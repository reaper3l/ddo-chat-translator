"""流水线端到端测试：不碰界面、不联网、不需要 OCR。

用假引擎替换真引擎，把"术语保护 → 翻译 → 还原 → 记忆/缓存"整条链路跑一遍。
"""
import queue
import tempfile
from pathlib import Path

from app.config import DEFAULT_CONFIG
from app import channels
from app.engines import BaseEngine, TranslationResult
from app.glossary import Glossary
from app.parser import ChatParser
from app.pipeline import DisplayItem, Pipeline
from app.store import MemoryStore


class EchoEngine(BaseEngine):
    """把输入原样加个前缀返回，这样能验证占位符有没有被正确还原。"""

    name = "echo"

    def __init__(self) -> None:
        self.calls = 0
        self.last_messages = None

    def available(self) -> bool:
        return True

    def describe(self) -> str:
        return "echo"

    def translate(self, text, messages=None, timeout: float = 20.0):
        self.calls += 1
        self.last_messages = messages
        return TranslationResult("中:" + text, True, self.name)


class FailEngine(BaseEngine):
    name = "fail"

    def available(self) -> bool:
        return True

    def describe(self) -> str:
        return "fail"

    def translate(self, text, messages=None, timeout: float = 20.0):
        return TranslationResult(text, False, self.name, "接口挂了")


class RefusalEngine(BaseEngine):
    """模拟"模型插话"：不给译文，回一句"（看不清楚）"。"""

    name = "refusal"

    def available(self) -> bool:
        return True

    def describe(self) -> str:
        return "refusal"

    def translate(self, text, messages=None, timeout: float = 20.0):
        return TranslationResult("（看不清楚）", True, self.name)


class BatchEngine(BaseEngine):
    """会按「[n] 译文」一次回多条的引擎（用来验证"一次请求翻多条"）。

    `broken=True` 时对批量请求故意回一句不带行号的话 —— 模拟模型不守格式，
    这时管线应该**逐条重试**，而不是把译文串行错位。
    """

    name = "batch"

    def __init__(self, broken: bool = False) -> None:
        self.calls = 0
        self.batch_sizes = []
        self.broken = broken

    def available(self) -> bool:
        return True

    def describe(self) -> str:
        return "batch"

    def translate(self, text, messages=None, timeout: float = 20.0):
        self.calls += 1
        lines = [line for line in str(text or "").splitlines() if line.strip()]
        self.batch_sizes.append(len(lines))
        if len(lines) == 1:                      # 逐条翻译：直接回译文
            body = lines[0].split("] ", 1)[-1]
            return TranslationResult("中:" + body, True, self.name)
        if self.broken:                          # 批量：故意不守格式
            return TranslationResult("抱歉，我一次只能翻一条。", True, self.name)
        return TranslationResult(
            "\n".join("[%d] 中:%s" % (index, line.split("] ", 1)[-1])
                      for index, line in enumerate(lines, 1)),
            True, self.name)


def make_pipeline(engine) -> Pipeline:
    config = dict(DEFAULT_CONFIG)
    config["engine"] = "offline"
    config["channels_enabled"] = {"小队": True, "常规": True, "队伍": True, "公会": True}
    folder = Path(tempfile.mkdtemp(prefix="ddo_pipe_"))
    memory = MemoryStore(folder / "memory.json")
    glossary = Glossary({"need heals": "需要治疗", "shroud": "幽影堡", "omw": "马上到"})
    pipeline = Pipeline(config, memory, glossary, queue.Queue())
    pipeline.engine = engine
    return pipeline


def make_english_pipeline(engine=None) -> Pipeline:
    """模拟英文客户端的玩家：把游戏里的英文频道名加进「设置 → 频道」。"""
    pipeline = make_pipeline(engine or EchoEngine())
    config = pipeline.config
    config["channels"] = [{"name": name, "color": "#8b949e", "enabled": True}
                          for name in ("Guild", "Party", "Tell", "Standard", "Error")]
    pipeline.parser = ChatParser(channels.alias_table(config))
    return pipeline


def test_translate_path_restores_terms_and_urls():
    engine = EchoEngine()
    pipeline = make_pipeline(engine)
    pipeline._handle_lines(["(小队):[小队] Sckham: need heals for shroud"])
    job = pipeline._jobs.get_nowait()
    item = pipeline._process(job)
    # 冒号会被润色成全角（跟在汉字后面）
    assert item.translated == "中：需要治疗 for 幽影堡", item.translated
    assert engine.calls == 1
    sent = engine.last_messages[-1]["content"]
    assert "{{TERM_" in sent
    assert "shroud" not in sent


def test_url_is_protected_and_restored():
    engine = EchoEngine()
    pipeline = make_pipeline(engine)
    pipeline._handle_lines(["(常规)Alice: check https: //store.steampowered.com now"])
    item = pipeline._process(pipeline._jobs.get_nowait())
    assert "{{URL_0}}" in engine.last_messages[-1]["content"]
    assert "https://store.steampowered.com" in item.translated


def test_learned_phrase_short_circuits_api():
    engine = EchoEngine()
    pipeline = make_pipeline(engine)
    pipeline.memory.set_phrase("omw", "马上到")
    pipeline._handle_lines(["(小队):[小队] Sckham: omw"])
    item = pipeline._process(pipeline._jobs.get_nowait())
    assert item.translated == "马上到"
    assert item.note == "记忆命中"
    assert engine.calls == 0


def test_fully_covered_sentence_skips_api():
    engine = EchoEngine()
    pipeline = make_pipeline(engine)
    pipeline._handle_lines(["(小队):[小队] Sckham: need heals"])
    item = pipeline._process(pipeline._jobs.get_nowait())
    assert item.translated == "需要治疗"
    assert item.note == "词典直译"
    assert engine.calls == 0


def test_unknown_words_are_collected_for_learning():
    engine = EchoEngine()
    pipeline = make_pipeline(engine)
    pipeline._handle_lines(["(常规)Alice: where is the raider camp"])
    pipeline._process(pipeline._jobs.get_nowait())
    tokens = [item["token"] for item in pipeline.memory.candidates(min_count=1)]
    assert "raider" in tokens
    assert "camp" in tokens


def test_engine_error_is_reported_and_keeps_original():
    pipeline = make_pipeline(FailEngine())
    pipeline._handle_lines(["(常规)Alice: something to translate"])
    item = pipeline._process(pipeline._jobs.get_nowait())
    assert item.error == "接口挂了"
    assert item.translated == "something to translate"


def test_chinese_message_is_not_sent_to_engine():
    engine = EchoEngine()
    pipeline = make_pipeline(engine)
    pipeline._handle_lines(["(常规)Alice: 你好世界"])
    item = pipeline._process(pipeline._jobs.get_nowait())
    assert item.note == "原文非英文"
    assert engine.calls == 0


def test_cache_avoids_second_api_call():
    engine = EchoEngine()
    pipeline = make_pipeline(engine)
    pipeline._handle_lines(["(常规)Alice: a fresh sentence here"])
    pipeline._process(pipeline._jobs.get_nowait())
    assert engine.calls == 1
    # 同一句在去重时间窗内会被直接拦掉，这里模拟"过了时间窗又出现一次"
    pipeline.deduper.clear()
    pipeline.forget_recent()
    # 换一个"表里确实存在、且开着"的频道（默认表里没有"队伍"这个频道）
    pipeline._handle_lines(["(公会)Bob: a fresh sentence here"])
    item = pipeline._process(pipeline._jobs.get_nowait())
    assert engine.calls == 1          # 命中缓存
    assert item.note == "缓存"


def test_cache_survives_glossary_change():
    """加了一个**跟这句话无关**的词之后，翻译缓存不该整份作废。

    以前缓存键里带着"术语表签名 + 记忆条数 + 术语条数"，于是每加一个词、每次公共
    词典更新，之前翻过的句子全部作废 —— 同样的句子又去调一次接口，白花钱。
    现在键只看"打完占位符的句子 + 引擎 + 翻译模式"：没受影响的句子照样命中。
    """
    engine = EchoEngine()
    pipeline = make_pipeline(engine)
    pipeline._handle_lines(["(常规)Alice: a fresh sentence here"])
    pipeline._process(pipeline._jobs.get_nowait())
    assert engine.calls == 1

    pipeline.glossary = Glossary({"someotherthing": "别的东西"})   # 词典变了
    pipeline.deduper.clear()
    pipeline.forget_recent()
    pipeline._handle_lines(["(公会)Bob: a fresh sentence here"])
    item = pipeline._process(pipeline._jobs.get_nowait())
    assert engine.calls == 1, "加了无关的词不该让缓存失效"
    assert item.note == "缓存"


def _three_lines():
    return ["(常规)Alice: one two three",
            "(常规)Bob: four five six",
            "(常规)Carol: seven eight nine"]


def test_batch_translate_uses_one_call_for_several_lines():
    """一屏同时来三条新消息 → 只调一次接口（省调用次数）。"""
    engine = BatchEngine()
    pipeline = make_pipeline(engine)
    pipeline._handle_lines(_three_lines())
    assert pipeline._jobs.qsize() == 3
    pipeline._stop.set()                 # 让翻译线程把队列干完就退出
    pipeline._translate_loop()
    shown = _drain_display(pipeline)
    assert engine.calls == 1, engine.batch_sizes
    assert engine.batch_sizes == [3]
    assert [item.source for item in shown] == [
        "one two three", "four five six", "seven eight nine"]
    assert all("中：" in item.translated or "中:" in item.translated for item in shown)
    assert pipeline.stats["batched"] == 3


def test_batch_translate_falls_back_to_one_by_one_when_reply_is_broken():
    """模型不守格式（不带行号）→ 逐条重试，绝不把译文串行错位。"""
    engine = BatchEngine(broken=True)
    pipeline = make_pipeline(engine)
    pipeline._handle_lines(_three_lines())
    pipeline._stop.set()
    pipeline._translate_loop()
    shown = _drain_display(pipeline)
    assert [item.source for item in shown] == [
        "one two three", "four five six", "seven eight nine"]
    # 1 次批量（失败）+ 3 次逐条重试
    assert engine.calls == 4, engine.batch_sizes
    assert pipeline.stats["batched"] == 0


def test_batch_translate_can_be_turned_off():
    engine = BatchEngine()
    pipeline = make_pipeline(engine)
    pipeline.config["batch_translate"] = False
    pipeline._handle_lines(_three_lines())
    pipeline._stop.set()
    pipeline._translate_loop()
    _drain_display(pipeline)
    assert engine.calls == 3, engine.batch_sizes


def test_batch_only_includes_lines_that_need_the_api():
    """一屏里"词典直译"的和要调接口的混在一起时，只有后者进批量。"""
    engine = BatchEngine()
    pipeline = make_pipeline(engine)
    pipeline._handle_lines(["(常规)Alice: need heals",      # 术语全覆盖 → 本地出
                            "(常规)Bob: one two three",
                            "(常规)Carol: four five six"])
    pipeline._stop.set()
    pipeline._translate_loop()
    shown = _drain_display(pipeline)
    assert engine.calls == 1 and engine.batch_sizes == [2]
    notes = {item.source: item.note for item in shown}
    assert notes["need heals"] == "词典直译"
    assert [item.source for item in shown] == [
        "need heals", "one two three", "four five six"]


def test_system_events_reach_model_context():
    engine = EchoEngine()
    pipeline = make_pipeline(engine)
    pipeline._handle_lines(["(小队) Grelik加入了你的队伍"])
    pipeline._drain_system_events()
    assert list(pipeline._system_events)
    pipeline._handle_lines(["(常规)Alice: a fresh sentence here"])
    pipeline._process(pipeline._jobs.get_nowait())
    joined = " ".join(m["content"] for m in engine.last_messages)
    assert "Grelik加入了你的队伍" in joined


def test_duplicate_line_is_not_queued_twice():
    engine = EchoEngine()
    pipeline = make_pipeline(engine)
    lines = ["(常规)Alice: the same sentence appears twice"]
    pipeline._handle_lines(lines)
    pipeline._handle_lines(lines)
    assert pipeline._jobs.qsize() == 1


def test_not_running_before_start():
    """刚创建（还没点监听）时不能算运行中，否则按钮会显示成"停止"。"""
    pipeline = make_pipeline(EchoEngine())
    assert pipeline.running is False
    assert pipeline.status()["running"] is False


def test_running_flag_follows_start_and_stop():
    pipeline = make_pipeline(EchoEngine())
    pipeline.config["region"] = None          # 不真的截图，只验证状态位
    pipeline.start()
    assert pipeline.running is True
    pipeline.stop(timeout=1.0)
    assert pipeline.running is False


def test_changed_frame_is_read_from_a_band_without_a_second_screen_grab():
    """监听时的抓屏次数：每帧只抓一次。

    抓屏在这台机器上固定要等一个刷新周期（实测 ~16.6ms，抓 8×8 也一样），
    所以"只识别变化的那几行"必须**从已经抓到的那一帧上裁**，不能为它再抓一次屏
    —— 否则每条新消息都要多打扰一次桌面合成器，游戏就会卡。
    """
    from PIL import Image, ImageDraw

    pipeline = make_pipeline(EchoEngine())
    pipeline.config["region"] = [0, 0, 100, 300]      # 只作参数，_capture_frame 已被替换
    pipeline.config["interval_ms"] = 300              # 让循环跑得快一点

    def make_frame(mark):
        # 变化发生在**最上面那一段**：这正是"聊天框框得偏上"的实况 ——
        # 日志一滚动就是从第 0 行开始变。旧版要求起点 ≥ 第 6 行，于是这里会被
        # 判成整帧重来；现在照样只认变化到的那一块。
        # 画的是几根细线，不是一整块白色：真实聊天框背后是半透明的，程序先做
        # "背景压平"再看差异，只有细笔画那样的相对明暗才表示"多了/少了字"。
        image = Image.new("RGB", (100, 300), (0, 0, 0))
        if mark:
            draw = ImageDraw.Draw(image)
            for top in (4, 20, 36, 52, 68, 84):
                draw.rectangle([0, top, 99, top + 1], fill=(255, 255, 255))
        return image

    frames = [make_frame(False), make_frame(True)]
    grabs = []

    def fake_capture(region):
        grabs.append(tuple(region))
        if len(grabs) >= 2:
            pipeline._stop.set()                       # 抓完第二帧就收工
        return frames[min(len(grabs), len(frames)) - 1]

    pipeline._capture_frame = fake_capture
    seen = []

    class FakeOcr:
        @staticmethod
        def available():
            return True

        @staticmethod
        def recognize(image, scale=1.0):
            seen.append(image.size)
            box = [[0, 0], [60, 0], [60, 10], [0, 10]]
            return [("(小队):[小队] Sckham: omw", box)]

    pipeline.ocr = FakeOcr()
    pipeline._capture_loop()

    assert len(grabs) == 2, grabs                 # 两帧两次抓屏，没有"为条带再抓一次"
    assert seen[0] == (100, 300)                  # 第一帧没有上一帧可比 → 整帧
    assert seen[1][0] == 100 and seen[1][1] < 300  # 第二帧只认变化到的那一块
    assert pipeline.stats["band_ocr"] == 1
    assert pipeline.stats["full_ocr"] == 1


def test_display_order_matches_game_order():
    """系统消息不用翻译会先就绪，但必须等它前面的玩家发言翻完再显示，
    否则窗口里的顺序和游戏聊天框对不上。"""
    engine = EchoEngine()
    pipeline = make_pipeline(engine)
    pipeline._handle_lines([
        "(小队): [小队] Dorqeth: elite right?",
        "(小队): [小队]    Kendra Estleton 加入了你的队伍。",
        "(小队): [小队] Ize: can shayes",
    ])
    while not pipeline._jobs.empty():
        pipeline._collect_ready()
        pipeline._flush_display()
        job = pipeline._jobs.get_nowait()
        pipeline._pending_display[job.seq] = pipeline._process(job)
        pipeline._flush_display()
    pipeline._collect_ready()
    pipeline._flush_display(force=True)

    rendered = []
    while not pipeline.ui_queue.empty():
        event = pipeline.ui_queue.get_nowait()
        if event.get("type") == "message":
            rendered.append(event["item"])

    assert [item.kind for item in rendered] == ["chat", "system", "chat"]
    assert rendered[0].speaker == "Dorqeth"
    assert rendered[1].channel == "小队"
    assert rendered[0].prefix == "(小队): [小队] "


def test_system_message_is_not_repeated_every_frame():
    """系统消息会在聊天框里停留很久，不能每帧都重新显示一遍（否则滚屏）。"""
    engine = EchoEngine()
    pipeline = make_pipeline(engine)
    frame = [
        "(小队):你加入了Longdd的队伍",
        "(小队):[小队] Guihuo: 嗨,马上到",
    ]
    # 连续三帧画面基本没变（系统消息还在屏幕上）
    for _ in range(3):
        pipeline._handle_lines(frame)
        pipeline._collect_ready()
        pipeline._flush_display()
    pipeline._collect_ready()
    pipeline._flush_display(force=True)

    rendered = []
    while not pipeline.ui_queue.empty():
        event = pipeline.ui_queue.get_nowait()
        if event.get("type") == "message":
            rendered.append(event["item"])
    texts = [item.translated for item in rendered]
    assert texts.count("你加入了Longdd的队伍") == 1, texts
    # 上下文中也只应出现一次
    assert list(pipeline._system_events).count("你加入了Longdd的队伍") == 0  # 还没进上下文
    pipeline._drain_system_events()
    assert list(pipeline._system_events).count("你加入了Longdd的队伍") == 1


# ---------------------------------------------------------------------------
# 用户实测日志回归（2026-09-27）：战利品/宝箱面板必须一条都不显示，
# 而 OCR 读花的玩家发言必须照样显示 —— 这两条要同时成立才算修好。
# ---------------------------------------------------------------------------

REAL_FRAME = [
    "(聊天): 战利品:vyarzar舟",
    "(聊天): (战利品:你舟iron1Key从玉相取正",
    "(聊天): 或利品:你付7金以玉相取山。 (战利品):你将JeweledKey从宝箱取出",
    "(聊天): (战利丽:imoke舟ialesorvaior从玉相中取正",
    "(利a):im1oke付+4vatcn PhysicalResistance5从宝箱",
    "(聊天): 玉相你寸里且时间:0天19时2刀5/秒",
    "(聊天): 废",
    "(小队):[小 (小队):[小 1.tor 2.投入3.同人4.偷人5",
    "(小队):[小队]Sinoke: 鬼火,你进红门",
    "(小队):[小队] Guihuo: 走神了",
    "(小队):小队]V Warzar:petforthedoor?",
    "(小队)):[小队]Sinoke:yes",
    "(小队):[小队]S Sinoke:guihuo,nikyireddoor (小队)",
    "(小队):[小队]Sinoke: 位面监狱位面监狱",
]


def _drain_jobs(pipeline):
    jobs = []
    while not pipeline._jobs.empty():
        jobs.append(pipeline._jobs.get_nowait())
    return jobs


def _drain_display(pipeline):
    pipeline._collect_ready()
    pipeline._flush_display(force=True)
    items = []
    while not pipeline.ui_queue.empty():
        event = pipeline.ui_queue.get_nowait()
        if event.get("type") == "message":
            items.append(event["item"])
    return items


def test_real_frame_shows_only_real_chat():
    engine = EchoEngine()
    pipeline = make_pipeline(engine)
    pipeline._handle_lines(REAL_FRAME)

    chats = _drain_jobs(pipeline)
    assert [(job.speaker, job.source) for job in chats] == [
        ("Sinoke", "鬼火,你进红门"),
        ("Guihuo", "走神了"),
        ("Warzar", "petforthedoor?"),
        ("Sinoke", "yes"),
        ("Sinoke", "guihuo,nikyireddoor"),
        ("Sinoke", "位面监狱"),
    ]
    # 系统消息里只允许"对话有用"的提示，面板文字一条都不能漏出来
    for item in _drain_display(pipeline):
        assert not Pipeline.is_panel_text(item.source), item.source
    # 过滤器确实在干活（过滤掉的行数 > 0）
    assert pipeline.stats["filtered"] > 0


def test_loot_line_never_reaches_the_translator():
    engine = EchoEngine()
    pipeline = make_pipeline(engine)
    pipeline._handle_lines(["(聊天): 战利品:你舟5unsone从玉相取正"])
    assert _drain_jobs(pipeline) == []
    while not pipeline._jobs.empty():
        pipeline._jobs.get_nowait()
    pipeline._collect_ready()
    pipeline._flush_display(force=True)
    assert pipeline.ui_queue.empty()
    assert engine.calls == 0


def test_whisper_reaches_translator():
    """悄悄话要当玩家发言送翻译（以前被当成系统消息，直接不翻）。"""
    engine = EchoEngine()
    pipeline = make_pipeline(engine)
    pipeline._handle_lines(["(私聊): Rockok告诉你: need heals for shroud"])
    job = pipeline._jobs.get_nowait()
    assert job.channel == "悄悄话"
    assert job.speaker == "Rockok告诉你"
    assert job.source == "need heals for shroud"
    item = pipeline._process(job)
    assert "需要治疗" in item.translated        # 术语表照样生效


def test_whisper_outgoing_reaches_translator():
    engine = EchoEngine()
    pipeline = make_pipeline(engine)
    pipeline._handle_lines(["(私聊): 你对 Rockok说，omw"])
    job = pipeline._jobs.get_nowait()
    assert job.channel == "悄悄话"
    assert job.speaker == "你对 Rockok说"
    assert job.source == "omw"


def test_english_client_system_notice_is_translated():
    """英文客户端：能看懂的系统提示（上线/离线/队伍）也要翻译，不再是原样丢出来。"""
    pipeline = make_english_pipeline()
    pipeline._handle_lines([
        "(Guild:): Medics has logged on.",
        "(Party:): A party chat room has been created!",
        "(Guild:): The guild's Message of the Day is:",   # 没用的提示照样过滤
    ])
    jobs = _drain_jobs(pipeline)
    assert [(job.kind, job.source) for job in jobs] == [
        ("system", "Medics has logged on"),
        ("system", "A party chat room has been created!"),
    ]
    item = pipeline._process(jobs[0])
    assert item.kind == "system"                 # 显示样式仍是系统消息
    assert item.translated.startswith("中:")     # 但内容已经翻译过了


def test_english_client_chat_and_whisper_reach_translator():
    pipeline = make_english_pipeline()
    pipeline._handle_lines([
        "(Guild:): [Guild] Huzi-2: liao ge zhen zao",
        "(Tell): Huzi-2 tells you, 'halo nihao'",
    ])
    jobs = _drain_jobs(pipeline)
    # 频道名就该是玩家自己配的那个（Guild / Tell），不会被改名成中文频道
    assert [(job.kind, job.channel, job.speaker) for job in jobs] == [
        ("chat", "Guild", "Huzi-2"),
        ("chat", "Tell", "Huzi-2告诉你"),
    ]


def test_panel_text_glued_to_chat_body_is_dropped():
    """面板文字被 OCR 粘到玩家正文后面时不显示（翻出来一定是垃圾）。"""
    pipeline = make_pipeline(EchoEngine())
    pipeline._handle_lines(["(小队):[小队]Sinoke: 宝箱掠夺重置时间:1周"])
    assert _drain_jobs(pipeline) == []


def test_system_whitelist_can_be_turned_off():
    pipeline = make_pipeline(EchoEngine())
    pipeline.config["channels_enabled"] = {"小队": True, "战利品": True}
    pipeline.config["system_whitelist"] = False
    # 关掉白名单后，非面板的系统提示会显示出来
    pipeline._handle_lines(["(小队): 某某某开始了新手教程"])
    shown = _drain_display(pipeline)
    assert [item.kind for item in shown] == ["system"]
    # 但面板特征文字永远不显示
    pipeline._handle_lines(["(战利品): Dorqeth 将 Jeweled Key 从 宝箱 中取出。"])
    assert _drain_display(pipeline) == []


def test_model_refusal_falls_back_to_original():
    pipeline = make_pipeline(RefusalEngine())
    pipeline._handle_lines(["(小队):[小队] Sckham: onuina gribble"])
    item = pipeline._process(pipeline._jobs.get_nowait())
    assert item.translated == "onuina gribble"
    assert "原文" in item.note


# ---------------------------------------------------------------------------
# 用户实测（第二轮反馈）："还是有重复刷屏"
# 根因是同一句每帧被 OCR 读得略有不同，精确指纹拦不住。
# ---------------------------------------------------------------------------

def test_ocr_variants_of_same_line_are_shown_once():
    pipeline = make_pipeline(EchoEngine())
    pipeline._handle_lines([
        "(小队):[小队]Guihuo:lgotone-shotbyittoday",
        "(小队):[小队]Guihuo:|gotone-shotbyittoday",
        "(小队):[小队]Guihuo:Igotone-shotbyittoday",
    ])
    jobs = _drain_jobs(pipeline)
    assert [job.source for job in jobs] == ["lgotone-shotbyittoday"]


def test_trailing_channel_label_variant_shown_once():
    """同一条消息的"带频道标签 / 不带"两个 OCR 结果，只能显示一次。"""
    pipeline = make_pipeline(EchoEngine())
    pipeline._handle_lines(["(小队):[小队] Dreambarb: out 小队"])
    first = _drain_jobs(pipeline)
    assert [job.source for job in first] == ["out"]      # 尾巴上的"小队"已经被摘掉
    pipeline._handle_lines(["(小队):[小队] Dreambarb: out"])
    assert _drain_jobs(pipeline) == []                   # 去重后不再排一次


def test_same_sentence_from_two_players_is_shown_twice():
    """不同的人说同一句话（"ty"、"ok" 很常见），两条都要显示。"""
    pipeline = make_pipeline(EchoEngine())
    pipeline._handle_lines([
        "(小队):[小队] Huzi-2: ty",
        "(小队):[小队] Medics: ty",
    ])
    jobs = _drain_jobs(pipeline)
    assert [(job.speaker, job.source) for job in jobs] == [("Huzi-2", "ty"), ("Medics", "ty")]
    # 但同一个人重复说同一句，仍然只显示一次
    pipeline._handle_lines(["(小队):[小队] Huzi-2: ty"])
    assert _drain_jobs(pipeline) == []


def test_same_message_with_glued_speaker_is_shown_once():
    """实测反馈（v3.0.29）：OCR 把名字读成 "jKyiae"，同一条消息显示了两次。

    根因：说话人被粘上一个字符后，"jKyiae" 和 "Kyiae" 被当成两个不同的人，
    而去重（带上说话人的指纹 + 模糊比对）就放它过去了。
    """
    pipeline = make_pipeline(EchoEngine())
    pipeline._handle_lines(["(小队):[小队] Kyiae: because they respawn?"])
    pipeline._handle_lines(["(小队):[小队jKyiae: becausetheyrespawn"])
    assert [job.source for job in _drain_jobs(pipeline)] == ["because they respawn?"]


def test_ocr_noise_variants_of_a_short_phrase_are_shown_once():
    """同一个人、同一句，OCR 读成两个版本（错了 3 个字母）→ 只显示一条。"""
    pipeline = make_pipeline(EchoEngine())
    pipeline._handle_lines(["(小队):[小队] Sinoke: pls1slot.firstd"])
    pipeline._handle_lines(["(小队):[小队] Sinoke: pis1siot.tirstd"])
    assert [job.source for job in _drain_jobs(pipeline)] == ["pls1slot.firstd"]


def test_long_line_read_short_is_not_shown_twice():
    pipeline = make_pipeline(EchoEngine())
    pipeline._handle_lines(["(小队):[小队] Beruthiell:there is something like thisinArtofWar"])
    pipeline._handle_lines(["[小队]Beruthiell: there is something like his"])
    assert len(_drain_jobs(pipeline)) == 1


def test_different_short_messages_are_all_shown():
    """短消息（yes / in / omw / ty）不能被模糊去重吃掉。"""
    pipeline = make_pipeline(EchoEngine())
    pipeline._handle_lines(["(小队):[小队] Sinoke: yes",
                            "(小队):[小队] Sinoke: in",
                            "(小队):[小队] Guihuo: omw",
                            "(小队):[小队] Guihuo: ty"])
    assert len(_drain_jobs(pipeline)) == 4


def test_our_own_translation_read_back_is_ignored():
    """主窗口贴着游戏时，OCR 可能把我们自己显示的译文读回来 —— 不能再显示一遍。"""
    pipeline = make_pipeline(EchoEngine())
    pipeline._push_display(DisplayItem(1, "chat", "小队", "Guihuo",
                                       "lgotone-shotbyittoday", "我今天被它一下秒了"))
    # 假设 OCR 把自己窗口里的这一行读回来了（前缀/名字都在）
    pipeline._handle_lines(["(小队):[小队] Guihuo: 我今天被它一下秒了"])
    assert _drain_jobs(pipeline) == []
    assert pipeline.stats["filtered"] >= 1


def test_repeated_system_notice_is_shown_once():
    """系统提示也会被每帧读到，错字版本同样只能显示一次。"""
    pipeline = make_pipeline(EchoEngine())
    pipeline._handle_lines(["(小队):你的队友Beruthiell已死亡"])
    pipeline._handle_lines(["(小队): 你的队友 Beruthiell 己死亡"])
    shown = _drain_display(pipeline)
    assert [item.kind for item in shown] == ["system"]


def test_unregistered_channel_is_filtered_with_a_hint():
    """没登记的频道（游戏更新/OCR 读花）不显示，但要计数 + 提示去频道页加一行。"""
    pipeline = make_pipeline(EchoEngine())
    before = pipeline.stats["filtered"]
    pipeline._handle_lines(["(聊天): Sinoke: hi there"])
    assert _drain_jobs(pipeline) == []                    # 不翻译
    assert pipeline.stats["filtered"] == before + 1        # 但算进"过滤"
    notices = []
    while not pipeline.ui_queue.empty():
        event = pipeline.ui_queue.get_nowait()
        if event.get("type") == "status":
            notices.append(event.get("text", ""))
    assert any("没登记的频道" in text for text in notices), notices


def test_channel_switch_off_is_silent():
    """表里存在但被用户关掉的频道：静默跳过（不计入"过滤"，也不提示）。"""
    pipeline = make_pipeline(EchoEngine())
    config = pipeline.config
    items = channels.effective(config)
    for entry in items:
        if entry["name"] == "小队":
            entry["enabled"] = False
    channels.sync(config, items)
    before = pipeline.stats["filtered"]
    pipeline._handle_lines(["(小队):[小队] Sinoke: hi there"])
    assert _drain_jobs(pipeline) == []
    assert pipeline.stats["filtered"] == before


# ---------------------------------------------------------------------------
# 自动过滤某个人（用户要求：可以过滤某一个人说话，比如自己）
# ---------------------------------------------------------------------------

def test_muted_speaker_lines_are_dropped_before_translation():
    engine = EchoEngine()
    pipeline = make_pipeline(engine)
    pipeline.config["muted_speakers"] = ["Guihuo"]
    pipeline._handle_lines(["(小队):[小队] Guihuo: omw",
                            "(小队):[小队] Sinoke: omw"])
    jobs = _drain_jobs(pipeline)
    assert [(job.speaker, job.source) for job in jobs] == [("Sinoke", "omw")]
    assert pipeline.stats["muted"] == 1


def test_muting_matches_ocr_variants_and_name_suffix():
    """OCR 把名字读花一两个字母、或者游戏加了 -1/-2，也要挡得住。"""
    pipeline = make_pipeline(EchoEngine())
    pipeline.config["muted_speakers"] = ["Zhaoyang"]
    pipeline._handle_lines([
        "(小队):[小队] Zhaoyang: need heals",
        "(小队):[小队] Zhaoyang-2: need heals",
        "(小队):[小队] Zhaoyanq: need heals",          # 名字被读花一个字母
        "(小队):[小队] Bob: need heals",
    ])
    assert [job.speaker for job in _drain_jobs(pipeline)] == ["Bob"]
    assert pipeline.stats["muted"] == 3


def test_unmuting_restores_the_speaker():
    """名单是每行现读配置的：去掉就立刻恢复（不用重启，也不用重开监听）。"""
    pipeline = make_pipeline(EchoEngine())
    pipeline.config["muted_speakers"] = ["Guihuo"]
    pipeline._handle_lines(["(小队):[小队] Guihuo: omw"])
    assert _drain_jobs(pipeline) == []
    pipeline.config["muted_speakers"] = []
    pipeline._handle_lines(["(小队):[小队] Guihuo: omw"])
    assert [job.source for job in _drain_jobs(pipeline)] == ["omw"]


def test_muted_speaker_does_not_enter_model_context():
    """被过滤的话不该进"上文" —— 否则模型会照着被过滤的内容作答。"""
    pipeline = make_pipeline(EchoEngine())
    pipeline.config["muted_speakers"] = ["Guihuo"]
    pipeline._handle_lines(["(小队):[小队] Guihuo: pop side please"])
    assert list(pipeline._recent) == []
    assert list(pipeline._recent_chat) == []
