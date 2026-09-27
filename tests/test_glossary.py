"""术语保护测试（含旧版占位符串位的回归测试）。"""
from app.glossary import Glossary


def make_glossary() -> Glossary:
    return Glossary({
        "healer": "治疗",
        "shroud": "幽影堡",
        "elite": "精英难度",
        "need heals": "需要治疗",
    })


def test_protect_and_restore():
    glossary = make_glossary()
    masked, mapping, _unknown = glossary.protect("need heals for shroud on elite")
    assert "{{TERM_" in masked
    assert glossary.restore(masked, mapping) == "需要治疗 for 幽影堡 on 精英难度"


def test_mapping_is_per_message():
    """旧版把映射挂在全局单例上，两条消息并发时会串位；这里必须各自独立。"""
    glossary = make_glossary()
    first, first_map, _ = glossary.protect("need heals")
    second, second_map, _ = glossary.protect("elite")
    assert glossary.restore(first, first_map) == "需要治疗"
    assert glossary.restore(second, second_map) == "精英难度"


def test_unknown_tokens_collected():
    glossary = make_glossary()
    _masked, _mapping, unknown = glossary.protect("where is the raider camp")
    assert "raider" in unknown
    assert "camp" in unknown
    assert "where" not in unknown      # 停用词不进候选
    assert "the" not in unknown


def test_case_insensitive():
    glossary = make_glossary()
    masked, mapping, _ = glossary.protect("SHROUD run?")
    assert glossary.restore(masked, mapping) == "幽影堡 run?"


def test_dotted_term_uses_regex():
    glossary = Glossary({"store.steampowered.com": "Steam商店"})
    masked, mapping, _ = glossary.protect("check store.steampowered.com now")
    assert glossary.restore(masked, mapping) == "check Steam商店 now"


def test_whole_sentence_covered():
    glossary = make_glossary()
    masked, mapping, _ = glossary.protect("need heals")
    assert "{{TERM_" in masked
    assert glossary.restore(masked, mapping) == "需要治疗"


def test_restore_tolerates_model_whitespace():
    glossary = make_glossary()
    _masked, mapping, _ = glossary.protect("elite")
    assert glossary.restore("结果是 {{ TERM_0 }} 难度", mapping) == "结果是 精英难度 难度"


def test_legacy_common_words_are_not_protected():
    """旧版大词典里的普通英文词（will/die/aa…）不能进保护表，
    否则模型只能逐词填空，会出现"意志 只是在 死亡 驯服中"这种碎句。"""
    import json
    import tempfile
    from pathlib import Path

    from app import glossary as glossary_module
    from app import paths

    folder = Path(tempfile.mkdtemp(prefix="ddo_glossary_"))
    legacy = folder / "legacy.json"
    legacy.write_text(json.dumps({
        "will": "意志", "die": "死亡", "normal": "普通", "aa": "替代升级",
        "shroud": "幽影堡", "need heals": "需要治疗", "tr": "缠根",
    }, ensure_ascii=False), encoding="utf-8")
    base = folder / "base.json"
    base.write_text(json.dumps({"terms": {"tr": "真轮回", "elite": "精英难度"}},
                               ensure_ascii=False), encoding="utf-8")

    old_extra, old_base = paths.GLOSSARY_EXTRA_PATH, paths.GLOSSARY_PATH
    paths.GLOSSARY_EXTRA_PATH, paths.GLOSSARY_PATH = legacy, base
    try:
        glossary = glossary_module.build_glossary(
            {"use_glossary": True, "use_extra_glossary": True})

        masked, mapping, _ = glossary.protect("he will die in taming")
        assert glossary.restore(masked, mapping) == "he will die in taming"

        masked, mapping, _ = glossary.protect("need heals for shroud")
        restored = glossary.restore(masked, mapping)
        assert "需要治疗" in restored and "幽影堡" in restored

        masked, mapping, _ = glossary.protect("tr")
        assert glossary.restore(masked, mapping) == "真轮回"   # 精选表压过旧表
    finally:
        paths.GLOSSARY_EXTRA_PATH, paths.GLOSSARY_PATH = old_extra, old_base
