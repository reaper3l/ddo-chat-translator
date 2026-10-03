"""语料回放自检（app/replay.py + tools/build_public_dict.py 的闸门）。"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from app import replay

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tools" / "corpus" / "chat_samples.txt"


def _tmp(name: str, text: str) -> Path:
    path = Path(tempfile.mkdtemp(prefix="ddo_replay_")) / name
    path.write_text(text, encoding="utf-8")
    return path


def test_read_corpus_skips_comments_and_blanks():
    lines = replay.read_corpus(_tmp("c.txt", "# 注释\n\nomw\n  rez plz  \n#x\n"))
    assert lines == ["omw", "rez plz"]


def test_read_corpus_json_list():
    lines = replay.read_corpus(_tmp("c.json", json.dumps({"lines": ["a b", "c d"]})))
    assert lines == ["a b", "c d"]
    lines = replay.read_corpus(_tmp("c2.json", json.dumps(["x", " y "])))
    assert lines == ["x", "y"]


def test_memory_lines_pulls_sentences_from_every_section():
    path = _tmp("memory.json", json.dumps({
        "phrases": {"fp": {"source": "on my way", "zh": "路上"}},
        "candidates": {"orb": {"samples": ["got the orb?", "where is the orb"]}},
        "corrections": [{"source": "rez me plz", "before": "x", "after": "y"}],
    }, ensure_ascii=False))
    lines = replay.memory_lines(path)
    assert "on my way" in lines and "got the orb?" in lines and "rez me plz" in lines


def test_collect_lines_dedupes_and_keeps_order():
    a = _tmp("a.txt", "one\ntwo\n")
    b = _tmp("b.txt", "two\nthree\n")
    assert replay.collect_lines([a, b], use_memory=False) == ["one", "two", "three"]


def test_missing_corpus_file_is_skipped():
    lines = replay.collect_lines([Path("Z:/nope/missing.txt")], use_memory=False)
    assert lines == []


def test_niche_term_passes():
    lines = ["omw", "in", "ty all", "rez plz", "got the orb?"]
    report = replay.compare({"rez": "复活"}, {"pop": "位面监狱"}, lines)
    assert report.ok and report.new_terms == 1
    assert report.hits["pop"] == 0                      # 语料里没出现 → 只是信息
    assert report.no_hits == ["pop"]


def test_over_reach_term_is_blocked():
    lines = ["on my way"] * 6 + ["omw", "ty"]
    report = replay.compare({}, {"on my way": "马上到"}, lines)
    assert not report.ok
    assert report.over_reach and report.over_reach[0][0] == "on my way"
    assert report.hits["on my way"] == 6
    assert "过度泛化" in replay.format_report(report)


def test_ratio_rule_also_blocks():
    lines = ["wait plz", "help plz", "come plz"] + ["ty"] * 7
    report = replay.compare({}, {"plz": "请"}, lines, max_ratio=0.2)
    assert not report.ok                                # 3/10 = 30% 且 >= 3 行
    assert report.hits["plz"] == 3


def test_two_hits_under_ratio_is_not_blocked():
    lines = ["rez plz", "buff plz"] + ["ty"] * 28
    report = replay.compare({}, {"plz": "请"}, lines)
    assert report.ok                                    # 2 行 < 3 行起算
    assert report.hits["plz"] == 2


def test_low_ratio_is_not_blocked():
    """命中行数多、但语料本身很大时不该拦（实测 pop 命中 5/226，是正常用法）。"""
    lines = ["hi, pop side", "pop", "pop?", "pop plz", "pop now"] + ["ty"] * 221
    report = replay.compare({}, {"pop": "位面监狱"}, lines)
    assert report.hits["pop"] == 5
    assert report.ok                                    # 5/226 ≈ 2%


def test_empty_corpus_reports_no_effect():
    report = replay.compare({}, {"pop": "位面监狱"}, [])
    assert report.ok and report.lines == 0
    assert "语料是空的" in replay.format_report(report)


def test_no_new_terms_is_a_no_op():
    report = replay.compare({"pop": "位面监狱"}, {}, ["pop"])
    assert report.ok and report.new_terms == 0
    assert "不用回放" in replay.format_report(report)


def test_client_would_ignore_common_single_words():
    """the / you 这种词客户端根本不拿来当保护词，不该当成"改坏"来拦。"""
    report = replay.compare({}, {"the": "这", "you": "你"}, ["pet for the door?", "you go"])
    assert report.ok and report.new_terms == 0
    assert report.ignored == ["the", "you"]
    assert "客户端不会采用" in replay.format_report(report)


def test_too_short_or_non_chinese_terms_are_ignored():
    report = replay.compare({}, {"aa": "啊啊", "pop": "pop side"}, ["aa pop"])
    assert report.ok and report.new_terms == 0
    assert report.ignored == ["aa", "pop"]


def test_client_usable_matches_glossary_rules():
    assert replay.client_usable("rez plz", "复活我")
    assert replay.client_usable("pop", "位面监狱")
    assert not replay.client_usable("the", "这")          # 单个常用词
    assert not replay.client_usable("om", "马上")          # 太短
    assert not replay.client_usable("pop", "pop side")     # 译文没中文
    assert not replay.client_usable("", "啊")


def test_replayed_lines_are_listed_for_review():
    lines = ["got the orb?", "ty"]
    report = replay.compare({}, {"orb": "宝珠"}, lines)
    assert report.ok
    assert report.changed and report.changed[0][0] == "got the orb?"
    text = replay.format_report(report)
    assert "之前：" in text and "之后：" in text and "结论：通过" in text


def test_builtin_fixture_corpus_is_real():
    lines = replay.read_corpus(FIXTURE)
    assert len(lines) >= 50                             # 语料太少就失去意义
    assert len(lines) == len(set(lines))                # 不许有重复行
    assert all(line.strip() == line for line in lines)
    report = replay.compare({}, {"omw": "在路上"}, lines)
    assert report.ok
