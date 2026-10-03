"""公共词典测试：签名必须验过才用、失败要退回缓存、只做加法。

签名用**临时生成的密钥对**（不碰作者的真实私钥），缓存路径换到临时目录
（不碰用户真实的 data/）。
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


def _payload(terms=None, batch=1):
    return json.dumps({
        "version": 1,
        "batch": batch,
        "updated": "2026-10-03 09:00",
        "terms": terms if terms is not None else {"brandnew": "全新词"},
    }, ensure_ascii=False).encode("utf-8")


def _signed(payload: bytes, seed) -> str:
    digest = hashlib.sha256(payload).hexdigest()
    signature = ed25519.sign(public_dict.signature_message(1, digest), seed)
    return "\n".join([
        public_dict.SIGNATURE_MARK,
        "version: 1",
        "file: public.json",
        "sha256: %s" % digest,
        "sig: %s" % base64.b64encode(signature).decode("ascii"),
        "",
    ])


class _TempCache:
    """隔离测试环境：缓存换到临时目录，公钥换成测试用的那把。

    生产逻辑里缓存和下载内容都**只认作者的公钥**（这里用临时的密钥代替），
    所以测试也必须把 update.pubkeys() 一起换掉，否则缓存会被正确地拒掉。
    """

    def __init__(self, pubkey: str = ""):
        self.pubkey = pubkey

    def __enter__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ddo-dict-test-"))
        self.old = (public_dict.CACHE_PATH, public_dict.CACHE_SIG_PATH,
                    public_dict.NEGATIVE_PATH)
        self.old_pubkeys = update.pubkeys
        public_dict.CACHE_PATH = self.tmp / "public_glossary.json"
        public_dict.CACHE_SIG_PATH = self.tmp / "public_glossary.json.sig"
        public_dict.NEGATIVE_PATH = self.tmp / "negatives.json"
        if self.pubkey:
            update.pubkeys = lambda text=None: [self.pubkey]
        public_dict.invalidate()
        public_dict._last_attempt = 0.0
        return self.tmp

    def __exit__(self, *_exc):
        (public_dict.CACHE_PATH, public_dict.CACHE_SIG_PATH,
         public_dict.NEGATIVE_PATH) = self.old
        update.pubkeys = self.old_pubkeys
        public_dict.invalidate()
        public_dict._last_attempt = 0.0
        return False


def _fetcher(payload: bytes, sig_text: str):
    def fetch(url: str) -> bytes:
        return sig_text.encode("utf-8") if url.endswith(".sig") else payload
    return fetch


# ------------------------------------------------------------------ 签名
def test_signature_message_binds_version_and_digest():
    first = public_dict.signature_message(1, "abc")
    assert first == public_dict.signature_message("1", "ABC")
    assert first != public_dict.signature_message(2, "abc")
    assert first != public_dict.signature_message(1, "abd")


def test_parse_signature_rejects_incomplete_block():
    assert public_dict.parse_signature("") is None
    assert public_dict.parse_signature("随便一段话") is None
    assert public_dict.parse_signature(
        public_dict.SIGNATURE_MARK + "\nversion: 1\nsha256: x") is None


def test_verify_accepts_good_signature():
    seed, pub = _keypair()
    payload = _payload()
    ok, reason = public_dict.verify(payload, _signed(payload, seed), pubkey=pub)
    assert ok, reason


def test_verify_rejects_tampered_payload():
    """内容被改过（签名没变）必须拒绝 —— 这个功能的安全底线。"""
    seed, pub = _keypair()
    sig_text = _signed(_payload(), seed)
    tampered = _payload({"brandnew": "被改过的词"})
    ok, reason = public_dict.verify(tampered, sig_text, pubkey=pub)
    assert not ok and "sha256" in reason


def test_verify_rejects_other_peoples_key():
    """别人拿自己的私钥签一份"词典"，同样不能通过。"""
    _seed, pub = _keypair()
    other_seed, _other_pub = _keypair()
    payload = _payload()
    ok, _reason = public_dict.verify(payload, _signed(payload, other_seed), pubkey=pub)
    assert not ok


def test_verify_rejects_empty_or_broken_block():
    seed, pub = _keypair()
    payload = _payload()
    assert not public_dict.verify(b"", _signed(payload, seed), pubkey=pub)[0]
    assert not public_dict.verify(payload, "没有签名块", pubkey=pub)[0]
    assert not public_dict.verify(payload, _signed(payload, seed)[:-8], pubkey=pub)[0]


# ------------------------------------------------------------------ 同步
def test_sync_downloads_and_caches():
    seed, pub = _keypair()
    payload = _payload({"brandnew": "全新词", "another": "另一个词"})
    with _TempCache(pub):
        result = public_dict.sync({"public_dict_enabled": True},
                                  fetcher=_fetcher(payload, _signed(payload, seed)),
                                  force=True, pubkey=pub)
        assert result["ok"] and result["updated"], result
        assert result["terms"] == 2
        assert public_dict.load_terms() == {"brandnew": "全新词", "another": "另一个词"}


def test_sync_keeps_cache_when_download_fails():
    seed, pub = _keypair()
    good = _payload({"brandnew": "全新词"})
    with _TempCache(pub):
        public_dict.sync({"public_dict_enabled": True},
                         fetcher=_fetcher(good, _signed(good, seed)),
                         force=True, pubkey=pub)

        def broken(_url: str) -> bytes:
            raise OSError("网络断了")

        result = public_dict.sync({"public_dict_enabled": True},
                                  fetcher=broken, force=True, pubkey=pub)
        assert not result["ok"] and "下载失败" in result["reason"]
        assert public_dict.load_terms() == {"brandnew": "全新词"}


def test_sync_rejects_bad_signature_and_keeps_cache():
    seed, pub = _keypair()
    good = _payload({"brandnew": "全新词"})
    with _TempCache(pub):
        public_dict.sync({"public_dict_enabled": True},
                         fetcher=_fetcher(good, _signed(good, seed)),
                         force=True, pubkey=pub)
        bad = _payload({"brandnew": "恶意词"})
        result = public_dict.sync({"public_dict_enabled": True},
                                  fetcher=_fetcher(bad, _signed(good, seed)),
                                  force=True, pubkey=pub)
        assert not result["ok"]
        assert public_dict.load_terms() == {"brandnew": "全新词"}


def test_sync_is_skipped_when_disabled():
    with _TempCache():
        called = []

        def fetch(url: str) -> bytes:
            called.append(url)
            return b"{}"

        result = public_dict.sync({"public_dict_enabled": False},
                                  fetcher=fetch, force=True)
        assert not result["ok"] and not called


def test_sync_falls_back_to_mirror_when_primary_fails():
    """Gitee 原始地址打不开时，自动改用 GitHub 镜像地址（内容同样要验签）。"""
    seed, pub = _keypair()
    payload = _payload({"brandnew": "全新词"})
    sig_text = _signed(payload, seed)

    def fetch(url: str) -> bytes:
        if public_dict.MIRROR_URL in url:
            return sig_text.encode("utf-8") if url.endswith(".sig") else payload
        raise OSError("gitee raw is down")

    with _TempCache(pub):
        result = public_dict.sync({"public_dict_enabled": True}, fetcher=fetch,
                                  force=True, pubkey=pub)
        assert result["ok"] and result["updated"], result
        assert public_dict.load_terms() == {"brandnew": "全新词"}


def test_needs_sync_respects_interval_and_off_switch():
    with _TempCache():
        config = {"public_dict_enabled": True, "public_dict_interval_hours": 6}
        # 用"很久以后"的时间点，避开"刚失败过 30 分钟内不重试"的退避
        assert public_dict.needs_sync(config, now=10 ** 9) is True
        assert public_dict.needs_sync({"public_dict_enabled": False}) is False


def test_corrupted_cache_is_ignored():
    """缓存文件被改过（签名对不上）→ 当作没有，退回内置表。"""
    seed, pub = _keypair()
    payload = _payload({"brandnew": "全新词"})
    with _TempCache(pub) as tmp:
        public_dict.sync({"public_dict_enabled": True},
                         fetcher=_fetcher(payload, _signed(payload, seed)),
                         force=True, pubkey=pub)
        assert public_dict.load_terms()
        (tmp / "public_glossary.json").write_bytes(_payload({"brandnew": "改了"}))
        public_dict.invalidate()
        assert public_dict.load_terms() == {}


# ---------------------------------------------------------------- 灰度发布
def test_rollout_gate_defers_outsiders_then_adopts_after_window():
    """灰度：不在首批的用户先不动，观察窗口结束后自动采用。"""
    data = {"batch": 3, "updated": "2026-10-03 12:00",
            "rollout": {"percent": 5, "hold_hours": 48}}
    start = time.mktime(time.strptime("2026-10-03 12:00", "%Y-%m-%d %H:%M"))
    old_bucket = public_dict._install_bucket
    try:
        public_dict._install_bucket = lambda batch: 50      # 不在前 5% 里
        adopted, why = public_dict._rollout_gate(data, now=start + 60)
        assert not adopted and "灰度中" in why
        adopted, _why = public_dict._rollout_gate(data, now=start + 49 * 3600)
        assert adopted
        public_dict._install_bucket = lambda batch: 1       # 属于首批
        adopted, _why = public_dict._rollout_gate(data, now=start + 60)
        assert adopted
        # 没写 rollout（或者 100%）→ 一律立刻采用
        assert public_dict._rollout_gate({"batch": 3}, now=start)[0]
        assert public_dict._rollout_gate(
            {"rollout": {"percent": 100, "hold_hours": 48}}, now=start)[0]
    finally:
        public_dict._install_bucket = old_bucket


# ------------------------------------------------------------ 本机自保护
def test_negative_votes_disable_a_public_term_locally():
    """用户把公共词改成自己的译法 → 本机记票，攒够了就在本机停用它。"""
    seed, pub = _keypair()
    payload = _payload({"rez plz": "复活我", "pop": "位面监狱"})
    with _TempCache(pub) as tmp:
        public_dict.NEGATIVE_PATH = tmp / "negatives.json"
        public_dict.sync({"public_dict_enabled": True},
                         fetcher=_fetcher(payload, _signed(payload, seed)),
                         force=True, pubkey=pub)
        assert set(public_dict.load_terms()) == {"rez plz", "pop"}

        assert public_dict.record_negative("rez plz", "快救我") == 1
        assert "rez plz" in public_dict.load_terms()          # 一次还不至于停用
        assert public_dict.record_negative("rez plz", "快救我") == 2
        assert "rez plz" not in public_dict.load_terms()      # 达到门槛 → 本机停用
        assert public_dict.negatives()["rez plz"] == 2

        # 译法和公共库一样、或者根本不是公共词 → 不记票
        assert public_dict.record_negative("pop", "位面监狱") == 0
        assert public_dict.record_negative("never seen", "随便") == 0


# ------------------------------------------------------------------ 分层
def test_public_layer_only_adds_never_overrides():
    """公共词典只补空缺：不覆盖内置精选表，也不收普通英文词/短噪音。"""
    tmp = Path(tempfile.mkdtemp(prefix="ddo-dict-layer-"))
    base = tmp / "glossary.json"
    base.write_text(json.dumps({"terms": {"tr": "真轮回", "elite": "精英难度"}},
                               ensure_ascii=False), encoding="utf-8")
    extra = tmp / "extra.json"
    extra.write_text(json.dumps({"shroud": "幽影堡"}), encoding="utf-8")
    old_paths = (paths.GLOSSARY_PATH, paths.GLOSSARY_EXTRA_PATH)
    old_loader = public_dict.load_terms
    paths.GLOSSARY_PATH, paths.GLOSSARY_EXTRA_PATH = base, extra
    public_dict.load_terms = lambda: {
        "tr": "缠根",             # 和内置冲突 → 不许覆盖
        "elite": "精英",          # 同上
        "brandnew": "全新词",      # 内置没有 → 收下
        "will": "意志",            # 普通英文词（NEVER_PROTECT）→ 拒收
        "ok": "好",                # 太短 → 拒收
        "noCjk": "abc",            # 译文没有中文 → 拒收
    }
    try:
        built = glossary.build_glossary({"use_glossary": True,
                                         "use_extra_glossary": True}).terms()
        assert built["tr"] == "真轮回"
        assert built["elite"] == "精英难度"
        assert built["shroud"] == "幽影堡"
        assert built["brandnew"] == "全新词"
        for rejected in ("will", "ok", "noCjk"):
            assert rejected not in built, rejected
    finally:
        paths.GLOSSARY_PATH, paths.GLOSSARY_EXTRA_PATH = old_paths
        public_dict.load_terms = old_loader


def test_public_layer_can_be_switched_off():
    tmp = Path(tempfile.mkdtemp(prefix="ddo-dict-off-"))
    base = tmp / "glossary.json"
    base.write_text(json.dumps({"terms": {"tr": "真轮回"}}), encoding="utf-8")
    old_paths = (paths.GLOSSARY_PATH, paths.GLOSSARY_EXTRA_PATH)
    old_loader = public_dict.load_terms
    paths.GLOSSARY_PATH, paths.GLOSSARY_EXTRA_PATH = base, tmp / "missing.json"
    public_dict.load_terms = lambda: {"brandnew": "全新词"}
    try:
        on = glossary.build_glossary({"use_glossary": True,
                                      "use_extra_glossary": False,
                                      "public_dict_enabled": True}).terms()
        off = glossary.build_glossary({"use_glossary": True,
                                       "use_extra_glossary": False,
                                       "public_dict_enabled": False}).terms()
        assert "brandnew" in on and "brandnew" not in off
    finally:
        paths.GLOSSARY_PATH, paths.GLOSSARY_EXTRA_PATH = old_paths
        public_dict.load_terms = old_loader
