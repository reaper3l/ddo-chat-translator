"""发行说明合并 / 校验的纯逻辑测试（不联网）。"""
import tempfile
from pathlib import Path

from app import ed25519, release_notes, update


def _signed_notes(version="3.0.21", filename="DDO翻译助手_v3.0.21.zip",
                  content=b"package bytes", seed=b"\x44" * 32):
    """造一份带签名块的"发行说明"，返回 (说明文本, 包路径, 公钥hex)。"""
    tmp = Path(tempfile.mkdtemp(prefix="ddo_notes_test_"))
    package = tmp / filename
    package.write_bytes(content)
    digest = update.sha256_file(package)
    block = update.make_signature_block(version, filename, digest,
                                        ed25519.sign(update.signature_message(
                                            version, filename, digest), seed))
    return "## 更新说明\n\n* 修了某某问题\n\n" + block + "\n", package, \
        ed25519.publickey(seed).hex()


def test_merge_body_keeps_signature_block_last():
    notes, _package, _key = _signed_notes()
    merged = release_notes.merge_body(notes, "### 补充说明\n\n这段话要加进去。")
    assert "补充说明" in merged
    # 签名块必须还在，而且是最后一段（程序解析的就是它）
    assert merged.count(update.SIGNATURE_MARK) == 1
    assert merged.rstrip().split(update.SIGNATURE_MARK)[-1].strip().startswith("version:")
    assert merged.index("补充说明") < merged.index(update.SIGNATURE_MARK)
    assert update.parse_signature(merged) is not None


def test_merge_body_without_signature_just_appends():
    merged = release_notes.merge_body("普通说明", "追加内容")
    assert merged.strip() == "普通说明\n\n追加内容"
    # extra 为空时只规范化换行，不加多余空行
    assert release_notes.merge_body("a\n\n\n") == "a\n"


def test_merge_body_does_not_duplicate_same_note():
    """同一段说明重复提交不会插两遍（省得手滑点两次）。"""
    once = release_notes.merge_body("正文", "这是一段说明")
    twice = release_notes.merge_body(once, "这是一段说明")
    assert twice.count("这是一段说明") == 1


def test_validate_accepts_good_notes():
    notes, package, _key = _signed_notes()
    ok, reason = release_notes.validate(notes, version="v3.0.21", package=package)
    assert ok, reason
    # 只比文件名、不算 sha256（包还没打出来时用）
    ok, reason = release_notes.validate(notes, version="3.0.21", package=package,
                                        check_digest=False)
    assert ok, reason


def test_validate_rejects_broken_or_mismatched():
    notes, package, _key = _signed_notes()
    # ① 没有签名块
    ok, reason = release_notes.validate("就是一段普通说明", version="3.0.21")
    assert not ok and "没有签名块" in reason
    # ② 版本号对不上
    ok, reason = release_notes.validate(notes, version="3.0.22")
    assert not ok and "版本号" in reason
    # ③ 文件名对不上
    ok, reason = release_notes.validate(notes, package=package.with_name("别的包.zip"))
    assert not ok and "文件名" in reason
    # ④ 包里内容改了（重打包没重新签名）
    package.write_bytes(b"repacked without re-signing")
    ok, reason = release_notes.validate(notes, version="3.0.21", package=package)
    assert not ok and "sha256" in reason


def test_find_package_matches_tag():
    tmp = Path(tempfile.mkdtemp(prefix="ddo_dist_test_"))
    (tmp / "DDO翻译助手_v3.0.20.zip").write_bytes(b"old")
    target = tmp / "DDO翻译助手_v3.0.21.zip"
    target.write_bytes(b"new")
    assert release_notes.find_package("v3.0.21", tmp) == target
    assert release_notes.find_package("3.0.21", tmp) == target    # 带不带 v 都行
    assert release_notes.find_package("v9.9.9", tmp) is None
    assert release_notes.find_package("", tmp) is None
