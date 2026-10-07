"""学习库测试。"""
import json
import tempfile
from pathlib import Path

from app.store import MemoryStore


def _store() -> MemoryStore:
    folder = Path(tempfile.mkdtemp(prefix="ddo_test_"))
    return MemoryStore(folder / "memory.json")


def test_correction_becomes_phrase_rule():
    store = _store()
    assert store.phrase("I need heals") is None
    result = store.learn_correction("I need heals", "我需要治疗", "帮我治疗")
    assert result["count"] == 1
    assert store.phrase("i need HEALS!!") == "帮我治疗"
    assert store.stats["corrections"] == 1


def test_repeated_correction_becomes_stable():
    store = _store()
    store.learn_correction("gg wp", "打得好", "干得漂亮", min_count=2)
    assert store.prompt_phrases() == []
    store.learn_correction("gg wp", "打得好", "干得漂亮", min_count=2)
    stable = store.prompt_phrases()
    assert stable and stable[0]["zh"] == "干得漂亮"


def test_correction_with_remember_off_is_history_only():
    """纠错窗口里取消勾选「记住这句」= 只记进纠错历史，不记住这句话。

    （以前那个勾选框只改提示文字，句子照样被记住 —— 等于骗用户。）
    """
    store = _store()
    result = store.learn_correction("that was a fluke", "那是侥幸", "那是运气",
                                    remember=False)
    assert store.phrase("that was a fluke") is None      # 没记住这句
    assert store.stats["corrections"] == 1               # 但历史里留着
    assert store.data["corrections"][-1]["after"] == "那是运气"
    assert result["exact_hit"] is False and result["stable"] is False
    # 下一次出现还是走正常翻译（不会被这条特例钉死）
    assert store.prompt_phrases() == []


def test_corrected_source_is_kept_and_matching_stays_on_the_original():
    """用户把英文原文改对了（OCR 读错时）：改动记进历史，但记忆仍按原句匹配。

    为什么：下次 OCR 读成同样的错样子时，只有"原句"这个 key 才能命中。
    """
    store = _store()
    store.learn_correction("eliteright?", "精英对?", "精英难度对吧？",
                           fixed_source="elite right?")
    record = store.data["corrections"][-1]
    assert record["fixed_source"] == "elite right?"
    assert record["source"] == "eliteright?"
    # 记忆的 key 还是原来读到的那句 → 下次同样的 OCR 结果能直接命中
    assert store.phrase("eliteright?") == "精英难度对吧？"


def test_candidates_and_ignore():
    store = _store()
    for _ in range(3):
        store.observe(["raider", "camp"], "a raider in the camp")
    candidates = store.candidates(min_count=3)
    tokens = [item["token"] for item in candidates]
    assert "raider" in tokens and "camp" in tokens
    store.ignore_candidate("raider")
    tokens = [item["token"] for item in store.candidates(min_count=3)]
    assert "raider" not in tokens
    assert "camp" in tokens


def test_terms_persist_to_disk():
    folder = Path(tempfile.mkdtemp(prefix="ddo_test_"))
    path = folder / "memory.json"
    first = MemoryStore(path)
    first.set_term("shroud", "幽影堡")
    assert first.flush(force=True)
    second = MemoryStore(path)
    terms = second.term_list()
    assert terms and terms[0]["text"] == "shroud"
    assert terms[0]["zh"] == "幽影堡"


def test_export_import_roundtrip():
    source = _store()
    source.set_term("reaper", "死神难度")
    source.learn_correction("omw", "马上到", "在路上，马上到")
    export_path = Path(tempfile.mkdtemp(prefix="ddo_test_")) / "export.json"
    assert source.export_to(export_path)
    assert json.loads(export_path.read_text(encoding="utf-8"))["terms"]

    target = _store()
    added = target.import_from(export_path)
    assert added["terms"] == 1
    assert target.term_list()[0]["text"] == "reaper"
    assert target.phrase("omw") == "在路上，马上到"


def test_summary_counts():
    store = _store()
    store.set_term("tr", "真轮回")
    summary = store.summary()
    assert summary["术语记忆"] == 1
    assert summary["句子记忆"] == 0
