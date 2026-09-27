"""流水线端到端测试：不碰界面、不联网、不需要 OCR。

用假引擎替换真引擎，把"术语保护 → 翻译 → 还原 → 记忆/缓存"整条链路跑一遍。
"""
import queue
import tempfile
from pathlib import Path

from app.config import DEFAULT_CONFIG
from app.engines import BaseEngine, TranslationResult
from app.glossary import Glossary
from app.pipeline import Pipeline
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
    pipeline._handle_lines(["(队伍)Bob: a fresh sentence here"])
    item = pipeline._process(pipeline._jobs.get_nowait())
    assert engine.calls == 1          # 命中缓存
    assert item.note == "缓存"


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
