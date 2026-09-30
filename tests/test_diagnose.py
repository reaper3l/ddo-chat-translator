"""Bug 反馈包：内容要全、**密钥绝不能漏出去**。"""
import json
import tempfile
import zipfile
from pathlib import Path

from app import diagnose

SECRET = "sk-abcdefghijklmnopqrstuvwxyz123456"


def _config():
    return {
        "engine": "deepseek",
        "deepseek_key": SECRET,
        "deepseek_model": "deepseek-chat",
        "interval_ms": 1200,
        "nested": {"access_token": "tok-1234567890abcdef", "keep": 1},
    }


def test_redact_config_hides_secrets_only():
    clean = diagnose.redact_config(_config())
    assert clean["deepseek_key"] == diagnose.SECRET_MARK
    assert clean["nested"]["access_token"] == diagnose.SECRET_MARK
    assert clean["engine"] == "deepseek"           # 普通项不动
    assert clean["nested"]["keep"] == 1
    assert SECRET not in json.dumps(clean, ensure_ascii=False)


def test_secret_values_ignores_short_values():
    values = diagnose.secret_values({"deepseek_key": SECRET, "other_key": "", "x_key": "1"})
    assert values == [SECRET]        # 空值和太短的（"1"）不参与替换，免得误伤正文


def test_scrub_removes_secret_and_user_name():
    text = "key=%s  路径 C:\\Users\\someone\\data" % SECRET
    cleaned = diagnose.scrub(text, [SECRET], home=r"C:\Users\someone")
    assert SECRET not in cleaned
    assert "someone" not in cleaned
    assert "%USERPROFILE%" in cleaned


def test_report_has_everything_but_no_secret():
    report = diagnose.build_report(
        problem="点了升级没反应",
        config=_config(),
        records=[{"kind": "chat", "source": "need heals", "translated": "需要治疗"},
                 {"kind": "system", "source": "Medics has logged on", "translated": "同上"}],
        last_lines=["(小队):[小队] Ize: need heals", "(小队):Dorgeth已断线"],
        memory=None)
    assert "need heals" in report and "需要治疗" in report      # 翻译记录在
    assert "(小队):[小队] Ize: need heals" in report            # OCR 原始行在
    assert "点了升级没反应" in report                            # 用户描述在
    assert "程序版本" in report and "Python" in report
    assert diagnose.SECRET_MARK in report                       # 配置里那项被替换
    assert SECRET not in report                                 # 密钥绝不出现


def test_bundle_zip_contains_report_and_extras_without_secret():
    report = diagnose.build_report(problem="x", config=_config())
    tmp = Path(tempfile.mkdtemp(prefix="ddo_report_"))
    bundle = diagnose.write_bundle(
        tmp, report,
        extras={"memory.json": '{"terms": {}}',
                "最近一次画面.png": b"\x89PNG\r\n\x1a\n fake"},
        stamp="20260930_120000")
    assert bundle.exists() and bundle.suffix == ".zip"
    assert bundle.name.endswith("20260930_120000.zip")
    with zipfile.ZipFile(bundle) as archive:
        names = set(archive.namelist())
        assert {"报告.txt", "memory.json", "最近一次画面.png"} <= names
        text = archive.read("报告.txt").decode("utf-8")
        assert SECRET not in text
        for name in names:                       # 整个包里都不许出现密钥
            assert SECRET.encode("utf-8") not in archive.read(name)


def test_issue_url_points_at_the_repo():
    url = diagnose.issue_url()
    assert url.startswith("https://gitee.com/git55236/ddo-chat-translator/issues/new")
