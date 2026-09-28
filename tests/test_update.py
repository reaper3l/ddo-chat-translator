"""自动检查更新 / 在线升级的纯逻辑测试（不联网：用假的 fetcher 和假的安装包）。"""
import base64
import json
import tempfile
import zipfile
from pathlib import Path

from app import ed25519
from app import update


def _release(tag="v9.9.9", body="更新内容", assets=()):
    return json.dumps({"tag_name": tag, "body": body,
                       "html_url": "https://example.invalid/releases/tag/%s" % tag,
                       "assets": list(assets)}).encode("utf-8")


def _asset(url, name):
    return {"browser_download_url": url, "name": name}


def test_parse_version_and_compare():
    assert update.parse_version("v3.0.20") == (3, 0, 20)
    assert update.parse_version("3.0.20.1") == (3, 0, 20, 1)
    assert update.parse_version("") == ()
    assert update.is_newer("v3.0.20", "3.0.19") is True
    assert update.is_newer("v3.0.19", "3.0.19") is False
    assert update.is_newer("v3.0.2", "3.0.19") is False
    assert update.is_newer("v3.1", "3.0.19") is True      # 位数不同也能比
    assert update.is_newer("", "3.0.19") is False


def test_check_finds_new_version_and_package():
    data = _release(assets=[
        _asset("https://gitee.com/x/archive/refs/tags/v9.9.9.zip", "v9.9.9.zip"),
        _asset("https://gitee.com/x/releases/download/v9.9.9/DDO.zip", "DDO.zip"),
    ])
    info = update.check("3.0.19", fetcher=lambda url: data)
    assert info is not None
    assert info.version == "9.9.9"
    assert info.asset_name == "DDO.zip"          # 源码包不算，要发行版附件
    assert "gitee.com" in info.asset_url
    assert info.page_url.endswith("v9.9.9")


def test_check_returns_none_when_up_to_date():
    info = update.check("3.0.19", fetcher=lambda url: _release("v3.0.19"))
    assert info is None


def test_check_returns_none_when_release_has_no_package():
    """附件被清理过的发行版：仍然提示有新版，但没有可下载的包（界面会引导去发行页）。"""
    info = update.check("3.0.19", fetcher=lambda url: _release("v9.9.9", assets=[]))
    assert info is not None and info.version == "9.9.9" and info.asset_url == ""


def test_check_survives_network_or_json_errors():
    def boom(url):
        raise OSError("network down")

    assert update.check("3.0.19", fetcher=boom) is None
    assert update.check("3.0.19", fetcher=lambda url: b"not json") is None
    assert update.check("3.0.19", fetcher=lambda url: b'{"tag_name": ""}') is None


def test_notes_brief_truncates():
    assert update.notes_brief("abc") == "abc"
    assert update.notes_brief("x" * 100, limit=10).endswith("……")


def test_prepare_update_extracts_package_and_builds_command():
    """造一个假的安装包，验证解压结果和"启动新版"的命令行（不真的启动它）。"""
    tmp = Path(tempfile.mkdtemp(prefix="ddo_upd_test_"))
    package = tmp / "DDO_v9.9.9.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("DDO翻译助手_v9.9.9/DDO翻译助手_v9.9.9.exe", "fake exe")
        archive.writestr("DDO翻译助手_v9.9.9/_internal/lib.dll", "fake dll")
        archive.writestr("DDO翻译助手_v9.9.9/data/config.json", "{}")   # 不该被覆盖过去
    target = tmp / "appdir"
    target.mkdir()

    prepared = update.prepare_update(package, "9.9.9", target_dir=target)
    assert str(prepared.target) == str(target)
    assert prepared.exe_name == "DDO翻译助手_v9.9.9.exe"
    # 解压出来的文件确实在（后面新版本就从这个目录把自己复制过去）
    assert (prepared.source / "_internal" / "lib.dll").exists()
    assert prepared.source.name == "DDO翻译助手_v9.9.9"
    # 启动命令：跑新版本的 exe，告诉它目标目录、临时目录、以及"等我退出"
    command = update.update_command(prepared)
    assert command[0] == str(prepared.source / prepared.exe_name)
    assert command[1] == update.APPLY_FLAG
    assert command[2] == str(target)
    assert str(prepared.tmp_dir) in command


def test_should_skip_keeps_user_data_and_logs():
    assert update.should_skip(Path("data") / "config.json") is True
    assert update.should_skip(Path("data") / "logs" / "app.log") is True
    assert update.should_skip(Path("ddo_update.log")) is True
    assert update.should_skip(Path("_internal") / "x.log") is True
    assert update.should_skip(Path("DDO翻译助手_v9.9.9.exe")) is False
    assert update.should_skip(Path("_internal") / "python311.dll") is False


def test_copy_tree_copies_files_and_keeps_user_data():
    """覆盖文件时：新版文件要到位、旧版多余文件不用管、**用户数据一个都不能动**。"""
    tmp = Path(tempfile.mkdtemp(prefix="ddo_copy_test_"))
    source = tmp / "new"
    target = tmp / "app"
    (source / "_internal").mkdir(parents=True)
    (source / "data").mkdir()
    (source / "DDO_v9.9.9.exe").write_text("new exe", encoding="utf-8")
    (source / "_internal" / "python311.dll").write_text("new dll", encoding="utf-8")
    (source / "data" / "config.json").write_text("{\"from\": \"package\"}",
                                                 encoding="utf-8")
    (source / "app.log").write_text("package log", encoding="utf-8")
    (target / "data").mkdir(parents=True)
    (target / "data" / "config.json").write_text("{\"mine\": true}", encoding="utf-8")
    (target / "DDO_v9.9.8.exe").write_text("old exe", encoding="utf-8")

    failures = update.copy_tree(source, target)
    assert failures == []
    assert (target / "DDO_v9.9.9.exe").read_text(encoding="utf-8") == "new exe"
    assert (target / "_internal" / "python311.dll").exists()
    # 用户数据没被包里的同名文件覆盖，日志也没被复制过来
    assert (target / "data" / "config.json").read_text(encoding="utf-8") == "{\"mine\": true}"
    assert not (target / "app.log").exists()
    assert (target / "DDO_v9.9.8.exe").exists()      # 旧 exe 由 apply_update 负责删


def test_copy_tree_reports_failures():
    """被占用/没权限的文件要如实报出来（apply_update 靠它决定是否重试、是否弹窗）。"""
    import shutil as shutil_module

    tmp = Path(tempfile.mkdtemp(prefix="ddo_copy_fail_"))
    source = tmp / "new"
    target = tmp / "app"
    source.mkdir()
    target.mkdir()
    (source / "ok.dll").write_text("1", encoding="utf-8")
    (source / "locked.dll").write_text("2", encoding="utf-8")
    original = shutil_module.copy2

    def fake_copy2(src, dst, *args, **kwargs):
        if Path(src).name == "locked.dll":
            raise PermissionError("被占用")
        return original(src, dst, *args, **kwargs)

    shutil_module.copy2 = fake_copy2
    try:
        failures = update.copy_tree(source, target)
    finally:
        shutil_module.copy2 = original
    assert len(failures) == 1 and "locked.dll" in failures[0]
    assert (target / "ok.dll").exists()               # 其它文件照样复制


def test_parse_apply_args_and_wait_for_exit():
    options = update.parse_apply_args(
        ["prog.exe", update.APPLY_FLAG, "E:\\app", update.TMP_FLAG, "C:\\tmp",
         update.PID_FLAG, "1234"])
    assert options == {"target": "E:\\app", "tmp_dir": "C:\\tmp", "wait_pid": "1234"}
    assert update.parse_apply_args(["prog.exe"])["target"] == ""
    # wait_for_exit：PID 为 0 时应该立刻返回，不阻塞、不报错
    update.wait_for_exit(0)


def test_can_self_update_only_for_frozen():
    """源码运行不做自动升级（改用发行页 / git pull）。"""
    import sys

    assert update.can_self_update() == bool(getattr(sys, "frozen", False))


# ---------------------------------------------------------------------------
# 发布包签名：只有"你签过的包"才允许自动安装
# ---------------------------------------------------------------------------

def _signed(notes_extra="", version="9.9.9", filename="DDO.zip",
            content=b"fake package bytes", seed=b"\x11" * 32, tamper=None,
            trust_seed=b"\x11" * 32):
    """造一个"已签名"的假安装包 + 签名块，返回 (zip 路径, UpdateInfo, 公钥hex)。"""
    tmp = Path(tempfile.mkdtemp(prefix="ddo_sign_test_"))
    package = tmp / filename
    package.write_bytes(content)
    digest = update.sha256_file(package)
    if tamper == "digest":
        digest = "0" * 64
    elif tamper == "version":
        version = "1.0.0"
    elif tamper == "file":
        filename = "Other.zip"
    elif tamper == "signature":
        seed = b"\x22" * 32        # 用另一把私钥签 → 验不过
    message = update.signature_message(version, filename, digest)
    block = update.make_signature_block(version, filename, digest,
                                        ed25519.sign(message, seed))
    notes = ("这是更新说明。" + notes_extra + "\n\n" + block)
    info = update.UpdateInfo(version="9.9.9", tag="v9.9.9", notes=notes,
                             asset_url="https://example.invalid/a.zip",
                             asset_name="DDO.zip", current="3.0.19")
    return package, info, ed25519.publickey(trust_seed).hex()


def test_signature_block_round_trip():
    _package, info, pubkey = _signed()
    parsed = update.parse_signature(info.notes)
    assert parsed is not None
    assert parsed["version"] == "9.9.9" and parsed["file"] == "DDO.zip"
    assert len(bytes.fromhex(parsed["sha256"])) == 32
    assert ed25519.verify(base64.b64decode(parsed["sig"]),
                          update.signature_message(parsed["version"],
                                                   parsed["file"], parsed["sha256"]),
                          bytes.fromhex(pubkey))


def test_verify_package_accepts_only_your_signature():
    package, info, pubkey = _signed()
    ok, reason = update.verify_package(package, info, pubkey=pubkey)
    assert ok, reason

    # 换一把私钥签的 → 拒绝（这就是"别人改了我的源码/账号"的情形）
    package, info, pubkey = _signed(tamper="signature")
    ok, reason = update.verify_package(package, info, pubkey=pubkey)
    assert not ok and "签名" in reason


def test_verify_package_rejects_tampered_content():
    package, info, pubkey = _signed()
    package.write_bytes(b"tampered after signing")     # 下载后被改过
    ok, reason = update.verify_package(package, info, pubkey=pubkey)
    assert not ok and "sha256" in reason


def test_verify_package_rejects_version_or_file_mismatch():
    for tamper in ("version", "file"):
        package, info, pubkey = _signed(tamper=tamper)
        ok, reason = update.verify_package(package, info, pubkey=pubkey)
        assert not ok, tamper


def test_verify_package_without_signature_or_pubkey():
    package, info, pubkey = _signed()
    info.notes = "没有签名块的发行说明"
    ok, reason = update.verify_package(package, info, pubkey=pubkey)
    assert not ok and "没有签名" in reason
    # 公钥还没配置（app/update.py 里 RELEASE_PUBKEY 为空）→ 一律不信任
    package, info, _pubkey = _signed()
    ok, reason = update.verify_package(package, info, pubkey="")
    assert not ok and "公钥" in reason


def test_verify_package_accepts_backup_pubkey():
    """RELEASE_PUBKEY 可以写多把（在用 + 备用）：任何一把验过都算你的包。

    为什么要留这条路：私钥万一丢了/要轮换，已经发出去的老版本 exe 还认得备用钥匙，
    不至于从此没法再给老用户发自动更新。
    """
    key_a = ed25519.publickey(b"\x11" * 32).hex()
    package_b, info_b, key_b = _signed(seed=b"\x33" * 32, trust_seed=b"\x33" * 32)
    both = "%s, %s" % (key_a, key_b)

    package_a, info_a, _ = _signed()
    ok, reason = update.verify_package(package_a, info_a, pubkey=both)
    assert ok and "主密钥" in reason
    ok, reason = update.verify_package(package_b, info_b, pubkey=both)
    assert ok and "备用密钥" in reason                 # 备用钥匙签的也认

    ok, _reason = update.verify_package(package_b, info_b, pubkey=key_a)
    assert not ok                                     # 只留了在用的那把 → 拒绝


def test_pubkeys_split_and_fingerprint():
    assert update.pubkeys("aa bb,cc;dd dd") == ["aa", "bb", "cc", "dd", "dd"]
    assert update.pubkeys("") == []
    configured = update.pubkeys()                     # 仓库里已经配好公钥
    assert configured and all(len(key) == 64 for key in configured)
    fingerprint = update.pubkey_fingerprint(configured[0])
    assert fingerprint.count(" ") == 15 and fingerprint == fingerprint.upper()
    assert update.pubkey_fingerprint("xyz") == ""     # 长度不对就不给指纹


def test_page_url_ok_only_trusted_hosts():
    assert update.page_url_ok("https://gitee.com/git55236/ddo-chat-translator")
    assert update.page_url_ok("https://github.com/a/b/releases/tag/v1")
    assert not update.page_url_ok("http://evil.example/releases/tag/v1")
    assert not update.page_url_ok("https://gitee.com.evil.example/x")
    assert not update.page_url_ok("javascript:alert(1)")
    assert not update.page_url_ok("")


def test_check_replaces_untrusted_page_url():
    """账号被拿走后在发行版里塞钓鱼链接 → 更新窗口不会把那个地址递给浏览器。"""
    data = json.dumps({"tag_name": "v9.9.9", "body": "x",
                       "html_url": "https://phish.example/releases/tag/v9.9.9",
                       "assets": []}).encode("utf-8")
    info = update.check("3.0.19", fetcher=lambda url: data)
    assert info is not None
    assert info.page_url.startswith("https://gitee.com/")
