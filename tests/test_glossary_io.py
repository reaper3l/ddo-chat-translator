"""术语表导出 / 导入的纯逻辑测试（不碰界面）。"""
import json

from app import glossary_io


def test_json_round_trip():
    terms = {"reaper": "死神", "omw": "马上到", "PoP": "位面监狱"}
    text = glossary_io.dump_terms(terms, "json")
    payload = json.loads(text)
    assert payload["format"] == glossary_io.FORMAT
    assert payload["count"] == 3 and payload["exported_at"]
    assert payload["terms"] == terms
    assert glossary_io.load_terms(text) == terms


def test_csv_round_trip_and_excel_friendly():
    terms = {"reaper": "死神", "loot, box": "宝箱"}      # 术语里带逗号也要能读回来
    text = glossary_io.dump_terms(terms, "csv")
    lines = text.strip().splitlines()
    assert lines[0] == "英文术语,中文"                    # 带表头，方便 Excel 里看
    assert glossary_io.load_terms(text) == terms


def test_load_accepts_plain_dict_and_memory_style():
    assert glossary_io.load_terms('{"omw": "马上到"}') == {"omw": "马上到"}
    memory_style = json.dumps({"omw": {"text": "omw", "zh": "马上到", "count": 3}})
    assert glossary_io.load_terms(memory_style) == {"omw": "马上到"}


def test_load_accepts_tsv_and_semicolon():
    assert glossary_io.load_terms("reaper\t死神\nomw\t马上到") == {
        "reaper": "死神", "omw": "马上到"}
    assert glossary_io.load_terms("英文;中文\nreaper;死神") == {"reaper": "死神"}


def test_load_reports_bad_files():
    for bad in ("", "   ", "不是术语表的乱码", "[]", '{"terms": {}}', "标题只有一列"):
        try:
            glossary_io.load_terms(bad)
        except ValueError:
            continue
        raise AssertionError("这份内容应该被拒绝：%r" % bad)


def test_plan_import_only_writes_changes():
    current = {"reaper": "死神", "omw": "马上到"}
    incoming = {"reaper": "死神", "omw": "这就来", "newterm": "新词"}
    to_write, unchanged = glossary_io.plan_import(current, incoming)
    assert to_write == {"omw": "这就来", "newterm": "新词"}
    assert unchanged == 1


def test_plan_import_is_case_insensitive_about_terms():
    to_write, unchanged = glossary_io.plan_import({"Reaper": "死神"}, {"reaper": "死神"})
    assert to_write == {} and unchanged == 1
    to_write, _ = glossary_io.plan_import({"Reaper": "死神"}, {"reaper": "收割者"})
    assert to_write == {"reaper": "收割者"}
