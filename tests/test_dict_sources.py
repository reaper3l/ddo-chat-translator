"""多词典源：官方源 + 用户自己加的源（本地文件 / 网地址）。

要守住的三条：
1. 默认只有一个官方源 —— 用户不主动加，别的源一律不生效；
2. 只做加法：同名先到的赢（官方 > 用户加的源），用户自己的词永远最大；
3. 一个源坏掉不影响别的源。
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import tempfile
import time
from pathlib import Path

from app import ed25519, glossary, paths, public_dict, update


def _keypair():
    seed = secrets.token_bytes(32)
    return seed, ed25519.publickey(seed).hex()


def _payload(terms, batch=1, updated="2026-10-03 09:00"):
    return json.dumps({"version": 1, "batch": batch, "updated": updated,
                       "terms": terms}, ensure_ascii=False).encode("utf-8")


def _signed(payload: bytes, seed) -> str:
    digest = hashlib.sha256(payload).hexdigest()
    signature = ed25519.sign(public_dict.signature_message(1, digest), seed)
    return "\n".join([public_dict.SIGNATURE_MARK, "version: 1",
                      "file: public.json", "sha256: %s" % digest,
                      "sig: %s" % base64.b64encode(signature).decode("ascii"), ""])


def _fetcher(payload: bytes, sig_text: str = ""):
    """模拟收件端：没有签名时 `.sig` 返回 404（和真实情况一致）。"""
    def fetch(url: str) -> bytes:
        if url.endswith(".sig"):
            if not sig_text:
                raise OSError("404 no signature")
            return sig_text.encode("utf-8")
        return payload
    return fetch


def _unsigned_fetcher(payload: bytes):
    return _fetcher(payload, "")


class _TempEnv:
    """把缓存和状态都挪到临时目录，别碰用户真实的 data/。"""

    def __init__(self, pubkey: str = ""):
        self.pubkey = pubkey

    def __enter__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ddo-src-test-"))
        self.old = (public_dict.CACHE_PATH, public_dict.CACHE_SIG_PATH,
                    public_dict.NEGATIVE_PATH, public_dict.SOURCE_DIR,
                    public_dict.SOURCE_STATE_PATH)
        self.old_pubkeys = update.pubkeys
        public_dict.CACHE_PATH = self.tmp / "official.json"
        public_dict.CACHE_SIG_PATH = self.tmp / "official.json.sig"
        public_dict.NEGATIVE_PATH = self.tmp / "negatives.json"
        public_dict.SOURCE_DIR = self.tmp / "sources"
        public_dict.SOURCE_STATE_PATH = public_dict.SOURCE_DIR / "state.json"
        if self.pubkey:
            update.pubkeys = lambda text=None: [self.pubkey]
        public_dict.invalidate()
        public_dict._last_attempt = 0.0
        return self.tmp

    def __exit__(self, *_exc):
        (public_dict.CACHE_PATH, public_dict.CACHE_SIG_PATH,
         public_dict.NEGATIVE_PATH, public_dict.SOURCE_DIR,
         public_dict.SOURCE_STATE_PATH) = self.old
        update.pubkeys = self.old_pubkeys
        public_dict.invalidate()


def _official(seed, pub, terms, config=None):
    payload = _payload(terms)
    return public_dict.sync(config or {"public_dict_enabled": True},
                            fetcher=_fetcher(payload, _signed(payload, seed)),
                            force=True, pubkey=pub)


def _file_source(tmp: Path, name: str, terms, source_id: str = "mine",
                 ack: bool = True, enabled: bool = True) -> dict:
    path = tmp / ("%s.json" % name)
    path.write_bytes(_payload(terms))
    return {"id": source_id, "name": name, "kind": "file", "path": str(path),
            "enabled": enabled, "ack": ack}


# ------------------------------------------------------------ 源列表本身
def test_default_config_has_only_the_official_source():
    items = public_dict.sources({})
    assert len(items) == 1
    assert items[0]["kind"] == public_dict.KIND_OFFICIAL
    assert items[0]["enabled"] is True
    assert items[0]["name"] == public_dict.OFFICIAL_NAME


def test_legacy_public_dict_url_migrates_to_official_source():
    """老配置只填了 public_dict_url（mirror 之类）→ 不能丢。"""
    items = public_dict.sources({"public_dict_url": "https://example.org/dict.json"})
    assert len(items) == 1
    assert items[0]["url"] == "https://example.org/dict.json"
    assert public_dict.effective_url({}) == public_dict.DEFAULT_URL


def test_official_source_stays_first_and_ids_are_unique():
    config = {"dict_sources": [
        {"id": "same", "name": "用户源 A", "kind": "url", "url": "https://a.example/x.json"},
        {"id": "same", "name": "用户源 B", "kind": "url", "url": "https://b.example/x.json"},
    ]}
    items = public_dict.sources(config)
    assert [item["kind"] for item in items] == [
        public_dict.KIND_OFFICIAL, public_dict.KIND_URL, public_dict.KIND_URL]
    assert len({item["id"] for item in items}) == 3      # 撞 id 自动改名


def test_broken_source_entries_are_dropped():
    config = {"dict_sources": [
        {"name": "没有地址的网源", "kind": "url"},
        {"name": "没有路径的文件源", "kind": "file"},
        {"name": "不认识的类型", "kind": "magic", "url": "https://x.example/y.json"},
        "字符串不是字典",
    ]}
    assert len(public_dict.sources(config)) == 1          # 只剩官方源


def test_source_id_is_sanitized():
    """id 会被当成缓存文件名，必须挡掉路径字符（配置文件手改也不该写到别处）。"""
    items = public_dict.sources({"dict_sources": [
        {"id": "../../evil", "name": "坏 id", "kind": "url",
         "url": "https://x.example/a.json"}]})
    assert items[1]["id"] == "evil"
    assert "/" not in items[1]["id"] and "\\" not in items[1]["id"]


# ------------------------------------------------------------ 合并与优先级
def test_minimal_terms_file_works_as_a_source():
    """玩家自己做源只需要一段 terms —— 这是"格式是什么"的答案，钉住它。"""
    with _TempEnv() as tmp:
        path = tmp / "最简源.json"
        path.write_text('{"terms": {"rez plz": "复活我"}}', encoding="utf-8")
        item = {"id": "simple", "name": "手写的源", "kind": "file",
                "path": str(path), "enabled": True, "ack": True}
        assert public_dict.source_terms(item) == {"rez plz": "复活我"}


def test_glossary_export_file_works_as_a_source():
    """词典窗口「只导出我的…」出来的 JSON 能直接当源用（界面文案就是这么说的）。"""
    from app import glossary_io

    with _TempEnv() as tmp:
        text = glossary_io.dump_terms({"omw": "马上到", "rez plz": "复活我"})
        path = tmp / "导出.json"
        path.write_text(text, encoding="utf-8")
        item = {"id": "export", "name": "我的导出", "kind": "file",
                "path": str(path), "enabled": True, "ack": True}
        assert public_dict.source_terms(item) == {"omw": "马上到", "rez plz": "复活我"}


def test_source_template_is_loadable_and_self_explanatory():
    """模板要能当源用，而且里面得写清楚怎么用（玩家最容易卡在这里）。"""
    from app import paths

    with _TempEnv() as tmp:
        payload = public_dict.source_template({"my term": "我的词"})
        assert payload["terms"] == {"my term": "我的词"}
        assert payload["_说明"] and any("terms" in line for line in payload["_说明"])
        path = tmp / "模板.json"
        assert paths.write_json(path, payload) is True
        item = {"id": "tpl", "name": "模板", "kind": "file", "path": str(path),
                "enabled": True, "ack": True}
        assert public_dict.source_terms(item) == {"my term": "我的词"}

        # 没给词就用示例词，而且示例词是"客户端真会用到"的那种
        empty = public_dict.source_template()
        assert empty["terms"] == public_dict.TEMPLATE_EXAMPLES
        from app import replay

        for term, zh in empty["terms"].items():          # 示例词要"客户端真的会用"
            assert replay.client_usable(term, zh), (term, zh)


def test_local_file_source_is_merged():
    seed, pub = _keypair()
    with _TempEnv(pub) as tmp:
        _official(seed, pub, {"brandnew": "全新词"})
        config = {"public_dict_enabled": True,
                  "dict_sources": [_file_source(tmp, "我的词表", {"mydict": "我的词"})]}
        terms = public_dict.load_terms(config)
        assert terms["brandnew"] == "全新词"
        assert terms["mydict"] == "我的词"


def test_editing_a_local_file_takes_effect_right_away():
    """本地文件源是直接读文件的：改了文件再更新一次，术语表必须跟着变。

    （回归：合并结果以前按"源列表"缓存，文件内容变了但源没变 → 改了也不生效。）
    """
    with _TempEnv() as tmp:
        item = _file_source(tmp, "我的词表", {"a": "甲"})
        config = {"public_dict_enabled": True, "dict_sources": [item]}
        public_dict.sync_source(public_dict.sources(config)[1], config, force=True)
        assert public_dict.load_terms(config) == {"a": "甲"}

        Path(item["path"]).write_bytes(_payload({"a": "甲", "b": "乙"}))
        public_dict.sync_source(public_dict.sources(config)[1], config, force=True)
        assert public_dict.load_terms(config) == {"a": "甲", "b": "乙"}


def test_official_wins_when_user_source_has_the_same_key():
    """只做加法：官方源排第一，用户源不能覆盖它。"""
    seed, pub = _keypair()
    with _TempEnv(pub) as tmp:
        _official(seed, pub, {"brandnew": "官方译"})
        config = {"public_dict_enabled": True,
                  "dict_sources": [_file_source(tmp, "别人的词表",
                                                {"brandnew": "别人译"})]}
        assert public_dict.load_terms(config)["brandnew"] == "官方译"


def test_disabled_source_is_not_loaded():
    seed, pub = _keypair()
    with _TempEnv(pub) as tmp:
        _official(seed, pub, {"brandnew": "全新词"})
        config = {"public_dict_enabled": True,
                  "dict_sources": [_file_source(tmp, "关掉的源", {"mydict": "我的词"},
                                                enabled=False)]}
        assert "mydict" not in public_dict.load_terms(config)


def test_user_terms_still_win_over_every_source():
    """合并进术语表后，用户自己的词优先级最高（公共层不覆盖它）。"""
    tmp = Path(tempfile.mkdtemp(prefix="ddo-src-layer-"))
    base = tmp / "glossary.json"
    base.write_text(json.dumps({"terms": {"rez plz": "官方旧译"}},
                               ensure_ascii=False), encoding="utf-8")
    old_paths = (paths.GLOSSARY_PATH, paths.GLOSSARY_EXTRA_PATH)
    old_loader = public_dict.load_terms
    paths.GLOSSARY_PATH, paths.GLOSSARY_EXTRA_PATH = base, tmp / "missing.json"
    public_dict.load_terms = lambda config=None: {"rez plz": "公共译", "newone": "新词"}
    try:
        built = glossary.build_glossary({"use_glossary": True,
                                         "use_extra_glossary": False},
                                        memory=_FakeMemory({"rez plz": "我自己的译法"})).terms()
        assert built["rez plz"] == "我自己的译法"     # 用户词赢
        assert built["newone"] == "新词"              # 公共层补空缺
    finally:
        paths.GLOSSARY_PATH, paths.GLOSSARY_EXTRA_PATH = old_paths
        public_dict.load_terms = old_loader


class _FakeMemory:
    def __init__(self, terms):
        self.data = {"terms": {key: {"text": key, "zh": zh}
                               for key, zh in terms.items()}}


# ------------------------------------------------------------ 信任与失败隔离
def test_unsigned_url_source_needs_confirmation():
    with _TempEnv():
        payload = _payload({"other": "别人的词"})
        item = {"id": "s1", "name": "别人的源", "kind": "url",
                "url": "https://s.example/dict.json", "enabled": True, "ack": False}
        result = public_dict.sync_source(item, {}, _unsigned_fetcher(payload), force=True)
        assert not result["ok"] and "确认" in result["reason"]
        assert public_dict.load_terms({"dict_sources": [item]}) == {}

        item["ack"] = True
        result = public_dict.sync_source(item, {}, _unsigned_fetcher(payload), force=True)
        assert result["ok"] and result["updated"], result
        assert public_dict.load_terms({"dict_sources": [item]}) == {"other": "别人的词"}


def test_user_source_with_its_own_key_is_verified():
    """用户源带自己的公钥 + 签名：验过才装；内容被改就丢。"""
    with _TempEnv():
        seed, pub = _keypair()
        payload = _payload({"other": "别人的词"})
        item = {"id": "s2", "name": "签名源", "kind": "url", "pubkey": pub,
                "url": "https://s.example/dict.json", "enabled": True, "ack": True}
        ok = public_dict.sync_source(item, {}, _fetcher(payload, _signed(payload, seed)),
                                     force=True)
        assert ok["ok"] and ok["updated"], ok

        tampered = _payload({"other": "被改过的词"})
        bad = public_dict.sync_source(item, {}, _fetcher(tampered, _signed(payload, seed)),
                                      force=True)
        assert not bad["ok"]
        assert public_dict.load_terms({"dict_sources": [item]}) == {"other": "别人的词"}


def test_signed_user_source_with_other_key_is_rejected():
    with _TempEnv():
        _seed, pub = _keypair()
        other_seed, _other = _keypair()
        payload = _payload({"other": "别人的词"})
        item = {"id": "s3", "name": "冒名源", "kind": "url", "pubkey": pub,
                "url": "https://s.example/dict.json", "enabled": True, "ack": True}
        result = public_dict.sync_source(item, {},
                                         _fetcher(payload, _signed(payload, other_seed)),
                                         force=True)
        assert not result["ok"]


def test_one_broken_source_does_not_break_the_others():
    with _TempEnv():
        good = {"id": "good", "name": "好源", "kind": "url", "ack": True,
                "url": "https://good.example/dict.json", "enabled": True}
        bad = {"id": "bad", "name": "坏源", "kind": "url", "ack": True,
               "url": "https://bad.example/dict.json", "enabled": True}
        payload = _payload({"mine": "我的词"})

        def fetch(url: str) -> bytes:
            if "bad.example" in url:
                raise OSError("连不上")
            if url.endswith(".sig"):
                raise OSError("404 no signature")
            return payload

        config = {"public_dict_enabled": True, "dict_sources": [good, bad]}
        result = public_dict.sync(config, fetcher=fetch, force=True)
        assert not result["ok"]                      # 坏源确实失败了
        assert "坏源" in result["reason"]
        rows = {row["id"]: row for row in result["sources"]}
        assert rows["good"]["ok"] and rows["good"]["updated"]
        assert not rows["bad"]["ok"]
        assert public_dict.load_terms(config)["mine"] == "我的词"


def test_official_failure_does_not_block_a_user_source():
    with _TempEnv() as tmp:
        seed, pub = _keypair()
        official_payload = _payload({"brandnew": "全新词"})
        mine = _file_source(tmp, "我的词表", {"mydict": "我的词"})
        config = {"public_dict_enabled": True, "dict_sources": [mine]}

        def fetch(_url: str) -> bytes:
            raise OSError("官方地址打不开")

        result = public_dict.sync(config, fetcher=fetch, force=True)
        assert not result["ok"] and "官方" in result["reason"]
        terms = public_dict.load_terms(config)
        assert terms.get("mydict") == "我的词"        # 本地源照样生效
        assert "brandnew" not in terms


def test_status_lists_every_source_with_counts():
    seed, pub = _keypair()
    with _TempEnv(pub) as tmp:
        _official(seed, pub, {"brandnew": "全新词"})
        config = {"public_dict_enabled": True,
                  "dict_sources": [_file_source(tmp, "我的词表",
                                                {"mydict": "我的词", "other": "另一个"})]}
        public_dict.sync_source(public_dict.sources(config)[1], config, force=True)
        status = public_dict.status(config)
        assert status["terms"] == 3
        rows = {row["name"]: row for row in status["sources"]}
        assert rows[public_dict.OFFICIAL_NAME]["terms"] == 1
        assert rows["我的词表"]["terms"] == 2
        assert rows["我的词表"]["kind"] == "file"


def test_source_state_remembers_errors():
    with _TempEnv():
        item = {"id": "s9", "name": "坏源", "kind": "url", "ack": True,
                "url": "https://x.example/dict.json", "enabled": True}

        def boom(_url: str) -> bytes:
            raise OSError("断网")

        public_dict.sync_source(item, {}, boom, force=True)
        status = public_dict.status({"dict_sources": [item]})
        row = [r for r in status["sources"] if r["id"] == "s9"][0]
        assert row["last_try"] > 0 and "断网" in row["error"]

        # 刚失败过的源在 30 分钟内不再重试（除非强制）
        again = public_dict.sync_source(item, {}, boom, force=False)
        assert again["ok"] and not again["checked"]
        assert "过一会儿" in again["reason"] or "还没到" in again["reason"]


def test_needs_sync_sees_a_new_source():
    seed, pub = _keypair()
    with _TempEnv(pub) as tmp:
        config = {"public_dict_enabled": True, "public_dict_interval_hours": 6}
        public_dict.sync_source(public_dict.sources(config)[0], config,
                                _fetcher(_payload({"a": "甲"}), _signed(_payload({"a": "甲"}), seed)),
                                force=True)
        # 官方源刚成功过 → 不该再同步
        assert public_dict.needs_sync(config, now=time.time() + 60) is False
        config["dict_sources"] = [_file_source(tmp, "我的词表", {"b": "乙"})]
        assert public_dict.needs_sync(config, now=time.time() + 60) is True
