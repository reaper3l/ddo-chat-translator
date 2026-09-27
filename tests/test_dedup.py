"""去重测试。"""
from app.dedup import Deduper


def test_same_text_is_duplicate():
    deduper = Deduper()
    assert deduper.check("hellothereworld") is False
    assert deduper.check("hellothereworld") is True


def test_prefix_extension_is_duplicate():
    deduper = Deduper()
    assert deduper.check("hellotheremyname") is False
    assert deduper.check("hellotheremynameisbob") is True


def test_short_prefix_is_not_duplicate():
    deduper = Deduper()
    assert deduper.check("hello") is False
    assert deduper.check("helloworld") is False


def test_single_character_ocr_jitter_is_duplicate():
    deduper = Deduper()
    assert deduper.check("abcdefghij") is False
    assert deduper.check("abcdefghix") is True


def test_ttl_expires():
    deduper = Deduper(ttl_seconds=10)
    assert deduper.check("abcdefghij", now=1000.0) is False
    assert deduper.check("abcdefghij", now=1005.0) is True
    assert deduper.check("abcdefghij", now=1020.0) is False


def test_different_text_is_new():
    deduper = Deduper()
    assert deduper.check("firstmessage") is False
    assert deduper.check("completelydifferent") is False


def test_clear():
    deduper = Deduper()
    deduper.check("abcdefghij")
    deduper.clear()
    assert deduper.check("abcdefghij") is False
