"""翻译服务门面（app/service.py）的纯逻辑测试：不联网、不碰界面、不写用户数据。

覆盖：英→中（术语保护 / 缓存共用 / 记账）、中→英、反馈学习（下次不花钱）、
状态与能力声明、超长文本截断。
"""
import tempfile
from pathlib import Path

from app.config import DEFAULT_CONFIG
from app.engines import BaseEngine, TranslationResult
from app.glossary import Glossary
from app.service import MAX_TEXT_CHARS, TranslatorService
from app.store import MemoryStore


class EchoEngine(BaseEngine):
    name = "echo"
    supports_chat = True

    def __init__(self):
        self.calls = 0
        self.last_messages = None

    def available(self):
        return True

    def describe(self):
        return "echo"

    def translate(self, text, messages=None, timeout=20.0):
        self.calls += 1
        self.last_messages = messages
        return TranslationResult("中:" + text, True, self.name)


def make_service(engine=None, glossary=None):
    config = dict(DEFAULT_CONFIG)
    config["engine"] = "offline"
    config["dict_sources"] = []          # 别把用户真实词典源带进来
    config["public_dict_enabled"] = False
    folder = Path(tempfile.mkdtemp(prefix="ddo_service_"))
    memory = MemoryStore(folder / "memory.json")
    service = TranslatorService(
        config, memory=memory, engine=engine or EchoEngine(),
        glossary=glossary if glossary is not None else Glossary({"shroud": "幽影堡"}),
        cache_path=folder / "cache.json")
    return service, memory


def test_translate_en2zh_and_cache():
    engine = EchoEngine()
    service, _memory = make_service(engine)
    first = service.translate("need heals for shroud")
    assert first["ok"] is True
    assert first["direction"] == "en2zh"
    assert "幽影堡" in first["zh"]           # 术语被保护 + 还原
    assert first["api_calls"] == 1
    calls = engine.calls
    second = service.translate("need heals for shroud")
    assert second["ok"] is True
    assert engine.calls == calls, "同样的句子第二次不该再调接口"
    assert second["cached"] is True or second["note"] == "缓存"
    assert service.status()["cache"] >= 1


def test_fully_covered_sentence_never_calls_engine():
    engine = EchoEngine()
    service, _memory = make_service(engine)
    result = service.translate("shroud")
    assert result["ok"] is True
    assert result["zh"] == "幽影堡"
    assert engine.calls == 0, "整句都在术语表里就不该调接口"
    assert result["api_calls"] == 0


def test_zh2en_uses_its_own_prompt_and_cache():
    engine = EchoEngine()
    service, _memory = make_service(engine)
    first = service.translate("马上到", direction="zh2en")
    assert first["ok"] is True and first["direction"] == "zh2en"
    calls = engine.calls
    service.translate("马上到", direction="zh2en")
    assert engine.calls == calls, "中译英也该命中缓存"


def test_feedback_learns_and_next_time_is_free():
    engine = EchoEngine()
    service, memory = make_service(engine)
    service.translate("pop side")             # 先翻一次（会花钱）
    calls = engine.calls
    result = service.feedback("pop side", "流行音乐那边", "位面监狱那边")
    assert result["ok"] is True
    again = service.translate("pop side")     # 学会了 → 不该再调接口
    assert again["ok"] is True
    assert again["zh"] == "位面监狱那边"
    assert again["note"] == "记忆命中"
    assert engine.calls == calls, "纠错之后同样的句子必须走本地记忆"
    assert memory.phrase("pop side") == "位面监狱那边"


def test_batch_translate_returns_every_line():
    engine = EchoEngine()
    service, _memory = make_service(engine)
    result = service.translate_many(["hello there", "shroud", "omw"])
    assert result["ok"] is True
    assert len(result["results"]) == 3
    assert result["results"][1]["zh"] == "幽影堡"


def test_add_term_takes_effect_immediately():
    engine = EchoEngine()
    service, _memory = make_service(engine)
    added = service.add_term("fod", "冲突基地")
    assert added["ok"] is True
    got = service.lookup("FOD")
    assert got["found"] is True and got["zh"] == "冲突基地"
    result = service.translate("fod")
    assert result["zh"] == "冲突基地"
    assert engine.calls == 0


def test_status_and_capabilities():
    service, _memory = make_service()
    status = service.status()
    assert status["ok"] is True
    assert status["version"]
    assert set(status["capabilities"]["directions"]) == {"en2zh", "zh2en", "auto"}
    assert status["capabilities"]["max_text_chars"] == MAX_TEXT_CHARS


def test_long_text_is_truncated_with_flag():
    service, _memory = make_service()
    result = service.translate("a" * (MAX_TEXT_CHARS + 50))
    assert result.get("truncated") is True


def test_empty_input_is_rejected():
    service, _memory = make_service()
    assert service.translate("")["ok"] is False
    assert service.translate_many([])["ok"] is False
    assert service.feedback("", "", "")["ok"] is False
