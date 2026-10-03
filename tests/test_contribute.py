"""贡献回传测试：只收"用户确认过的"、必须先脱敏、闸门要挡得住单人刷量。"""
from __future__ import annotations

import tempfile
from pathlib import Path

from app import contribute


class _FakeMemory:
    """最小可用的学习库替身。"""

    def __init__(self, terms=None, phrases=None):
        self._terms = terms or []
        self.data = {"phrases": phrases or {}}

    def term_list(self):
        return self._terms


class _TempState:
    """把匿名 ID 和已贡献台账换到临时目录。"""

    def __enter__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ddo-contrib-"))
        self.old = (contribute.INSTALL_ID_PATH, contribute.STATE_PATH)
        contribute.INSTALL_ID_PATH = self.tmp / "install_id.txt"
        contribute.STATE_PATH = self.tmp / "contribute_state.json"
        return self.tmp

    def __exit__(self, *_exc):
        contribute.INSTALL_ID_PATH, contribute.STATE_PATH = self.old
        return False


# ------------------------------------------------------------------ 脱敏
def test_scrub_removes_links_emails_digits_handles():
    dirty = "看 https://evil.example.com/x 找我 qq 12345678 mail a@b.com @somebody"
    clean = contribute.scrub(dirty)
    for leaked in ("evil.example.com", "12345678", "a@b.com", "@somebody"):
        assert leaked not in clean, clean
    assert "[链接]" in clean and "[数字]" in clean and "[邮箱]" in clean


def test_clean_term_keeps_only_looks_like_terms():
    assert contribute.clean_term("rez plz", "复活我") == ("rez plz", "复活我")
    assert contribute.clean_term("von3", "冯3") == ("von3", "冯3")
    for bad in (("see", "看"),                      # 译文里有中文但源词是普通词？这里只测形态
                ("a term with way too many words here", "太长"),
                ("rez", "abc"),                     # 译文没有中文
                ("rez", "复活 https://x.com"),        # 译文带链接（脱敏后含 [链接]）
                ("rez", "复活 12345678")):
        result = contribute.clean_term(*bad)
        assert result is None, (bad, result)


def test_clean_phrase_rejects_chat_lines_and_dirty_text():
    assert contribute.clean_phrase("need heals fast", "快点治疗") == (
        "need heals fast", "快点治疗")
    # 带"名字:"的聊天行不贡献（原文里往往夹着玩家名）
    assert contribute.clean_phrase("Dorgeth: need heals", "需要治疗") is None
    assert contribute.clean_phrase("(小队): [小队] Ize: omw", "我马上到") is None
    assert contribute.clean_phrase("go to https://x.com now", "现在去") is None
    assert contribute.clean_phrase("call me 13800138000", "打我电话") is None
    assert contribute.clean_phrase("omw", "我马上到") is None        # 单词走术语那条路
    assert contribute.clean_phrase("出发 走了", "出发") is None       # 原文含中文


def test_uid_is_stable_and_anonymous():
    with _TempState():
        first = contribute.install_uid()
        assert first == contribute.install_uid()          # 稳定
        assert len(first) == 12 and all(c in "0123456789abcdef" for c in first)
        raw = contribute.INSTALL_ID_PATH.read_text(encoding="utf-8").strip()
        assert raw not in first                           # 不是原文，反推不出


# ------------------------------------------------------------ 贡献码编解码
def test_code_round_trip_and_find_in_text():
    with _TempState():
        items = {"terms": [{"kind": "term", "key": "k1", "source": "rez", "zh": "复活"}],
                 "phrases": []}
        payload = contribute.build_payload(items, version="3.0.30")
        code = contribute.encode_code(payload)
        assert code.startswith(contribute.CODE_PREFIX)
        back = contribute.decode_code(code)
        assert back and back["terms"] == [{"t": "rez", "z": "复活"}]
        # 贴进聊天时后面跟着别的话，也要能解出来
        noisy = "这是我今天的贡献码 %s 谢谢" % code
        found = contribute.find_codes(noisy)
        assert len(found) == 1 and found[0]["terms"][0]["z"] == "复活"


def test_decode_rejects_garbage():
    assert contribute.decode_code("") is None
    assert contribute.decode_code("DDO1:not-base64!!") is None
    assert contribute.find_codes("随便一段没有贡献码的话") == []


def test_preview_lists_what_will_be_sent():
    with _TempState():
        payload = contribute.build_payload(
            {"terms": [{"kind": "term", "key": "k", "source": "rez", "zh": "复活"}],
             "phrases": [{"kind": "phrase", "key": "p", "source": "need heals",
                          "zh": "需要治疗"}]})
        text = contribute.preview_text(payload)
        assert "rez = 复活" in text and "need heals → 需要治疗" in text
        assert "没有聊天原文" in text


# ---------------------------------------------------------------- 采集
def test_collect_items_only_takes_confirmed_data():
    with _TempState():
        memory = _FakeMemory(
            terms=[{"text": "rez", "zh": "复活", "count": 3},
                   {"text": "Dorgeth: rez", "zh": "复活", "count": 1}],   # 带名字 → 丢
            phrases={
                "a": {"source": "need heals fast", "zh": "快点治疗", "count": 3,
                      "stable": True},
                "b": {"source": "i never die", "zh": "我从没死过", "count": 1,
                      "stable": False},                                   # 不稳定 → 不贡献
            })
        items = contribute.collect_items(memory)
        assert [i["source"] for i in items["terms"]] == ["rez"]
        assert [i["source"] for i in items["phrases"]] == ["need heals fast"]
        assert items["skipped"]["invalid"] >= 1


def test_collect_items_does_not_resend():
    with _TempState():
        memory = _FakeMemory(terms=[{"text": "rez", "zh": "复活", "count": 2}])
        first = contribute.collect_items(memory)
        assert len(first["terms"]) == 1
        contribute.mark_sent(first["terms"])
        second = contribute.collect_items(memory)
        assert second["terms"] == []
        assert second["skipped"]["sent"] == 1


def test_send_without_endpoint_is_a_friendly_failure():
    ok, reason = contribute.send({"v": 1}, "")
    assert not ok and "接收地址" in reason


def test_send_uses_injected_poster():
    seen = {}

    def poster(url, body):
        seen["url"], seen["body"] = url, body
        return True

    ok, _reason = contribute.send({"v": 1, "terms": []}, "https://example.invalid/c",
                                 poster=poster)
    assert ok and seen["url"].endswith("/c")


# -------------------------------------------------------------- 自动闸门
def _record(uid, terms=None, phrases=None):
    return {"v": 1, "uid": uid,
            "terms": [{"t": t, "z": z} for t, z in (terms or [])],
            "phrases": [{"t": t, "z": z} for t, z in (phrases or [])]}


def test_aggregate_requires_several_distinct_people():
    """一个人提交 20 遍不算共识。"""
    solo = [_record("aaaa", [("rez plz", "复活我")]) for _ in range(20)]
    result = contribute.aggregate(solo)
    assert result["terms"] == {}
    assert any("不同用户数" in "；".join(r["why"]) for r in result["rejected"])


def test_aggregate_accepts_consensus():
    records = [_record("u%d" % i, [("rez plz", "复活我")]) for i in range(8)]
    records += [_record("u99", [("rez plz", "复活吧")])]
    result = contribute.aggregate(records)
    assert result["terms"] == {"rez plz": "复活我"}
    assert result["contributors"] == 9


def test_aggregate_reports_inconsistent_translations():
    records = []
    for i in range(3):
        records.append(_record("a%d" % i, [("pop", "位面监狱")]))
    for i in range(3):
        records.append(_record("b%d" % i, [("pop", "传送门")]))
    result = contribute.aggregate(records)
    assert result["terms"] == {}                      # 一致率不够，先不收
    assert any("一致率" in "；".join(r["why"]) for r in result["rejected"])


def test_aggregate_skips_terms_already_built_in():
    records = [_record("u%d" % i, [("rez plz", "复活我")]) for i in range(5)]
    result = contribute.aggregate(records, known_terms=["Rez Plz"])
    assert result["terms"] == {}


def test_aggregate_rejects_dirty_rows_from_clients():
    """客户端可能被改过：作者端必须自己再筛一遍。"""
    records = [_record("u%d" % i, [("visit https://x.com", "来看我")]) for i in range(6)]
    records += [_record("v%d" % i, [("rez plz 13800138000", "复活 13800138000")])
                for i in range(6)]
    result = contribute.aggregate(records)
    assert result["terms"] == {}


def test_aggregate_alerts_on_flood():
    # 40 条**都能过闸门**的新词 → 单批新增异常多，必须报警
    records = []
    for index in range(40):
        for user in range(8):
            records.append(_record("u%d_%d" % (index, user),
                                   [("term%d x" % index, "术语%d" % index)]))
    result = contribute.aggregate(records)
    assert result["alerts"], "异常暴涨必须要报警"
    assert any("超过上限" in item for item in result["alerts"])


def test_report_text_mentions_gates_and_alerts():
    records = [_record("u%d" % i, [("rez plz", "复活我")]) for i in range(8)]
    result = contribute.aggregate(records)
    text = contribute.report_text(result)
    assert "公共词典候选报告" in text
    assert "rez plz = 复活我" in text
    assert "门槛" in text


# ------------------------------------------------------- 否定票（自保护闭环）
def test_payload_carries_negative_votes():
    """本机给公共词投过的否定票要跟着贡献一起发出去。"""
    with _TempState():
        from app import public_dict

        old_path = public_dict.NEGATIVE_PATH
        try:
            public_dict.NEGATIVE_PATH = contribute.STATE_PATH.parent / "neg.json"
            public_dict._terms_cache = {"rez plz": "复活我"}
            public_dict.record_negative("rez plz", "快救我")
            payload = contribute.build_payload({"terms": [], "phrases": []})
            assert payload["negatives"] == ["rez plz"]
            code = contribute.encode_code(payload)
            assert contribute.decode_code(code)["negatives"] == ["rez plz"]
        finally:
            public_dict.NEGATIVE_PATH = old_path
            public_dict._terms_cache = None


def test_aggregate_reports_terms_to_retire():
    """多个不同用户改了同一个公共词 → 建议下架。"""
    records = []
    for i in range(3):
        row = _record("u%d" % i)
        row["negatives"] = ["rez plz"]
        records.append(row)
    records.append({**_record("u9"), "negatives": ["bad word!"]})   # 形态非法 → 不收
    result = contribute.aggregate(records)
    assert result["retire"] == {"rez plz": 3}
    text = contribute.report_text(result)
    assert "建议下架" in text and "rez plz" in text


def test_aggregate_ignores_lone_negative_vote():
    """只有一个人改过 → 不改公共库（可能是他自己的口癖）。"""
    result = contribute.aggregate([{**_record("solo"), "negatives": ["rez plz"]}])
    assert result["retire"] == {}
