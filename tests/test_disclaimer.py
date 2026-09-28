"""使用须知 / 免责声明的纯逻辑测试（不开窗口、不写配置）。"""
from datetime import datetime

from app import disclaimer
from app.config import DEFAULT_CONFIG


def test_fresh_config_needs_agreement():
    assert disclaimer.needs_agreement({}) is True
    assert disclaimer.needs_agreement({"agreement_version": 0}) is True
    # 脏数据（手改坏了配置）不能把用户挡在门外，但也不能当成"已同意"放过去
    assert disclaimer.needs_agreement({"agreement_version": "abc"}) is True
    assert disclaimer.needs_agreement({"agreement_version": None}) is True
    assert disclaimer.needs_agreement({"agreement_version": ""}) is True


def test_accept_records_version_and_time():
    config = {}
    stamp = disclaimer.accept(config, now=datetime(2026, 9, 28, 20, 5, 0))
    assert config["agreement_version"] == disclaimer.DISCLAIMER_VERSION
    assert stamp == "2026-09-28 20:05:00"
    assert disclaimer.accepted_at(config) == "2026-09-28 20:05:00"
    assert disclaimer.needs_agreement(config) is False
    # 同意时间在没同意过时是空串（关于页拿它显示状态）
    assert disclaimer.accepted_at({}) == ""


def test_terms_revision_asks_again():
    """条款改版（版本号 +1）后，老用户要重新确认一次。"""
    config = {"agreement_version": disclaimer.DISCLAIMER_VERSION,
              "agreement_accepted_at": "2026-09-28 20:05:00"}
    assert disclaimer.needs_agreement(config) is False
    config["agreement_version"] = disclaimer.DISCLAIMER_VERSION - 1
    assert disclaimer.needs_agreement(config) is True
    # 比当前版本更新（比如用户降级了程序）不算需要重新同意
    config["agreement_version"] = disclaimer.DISCLAIMER_VERSION + 1
    assert disclaimer.needs_agreement(config) is False


def test_text_is_readable_and_covers_key_points():
    body = disclaimer.text()
    assert "条款版本 %d" % disclaimer.DISCLAIMER_VERSION in body
    assert disclaimer.DISCLAIMER_DATE in body
    # 这几件事必须写进正文里，缺了就等于没告知
    for keyword in ("第三方", "服务条款", "上传到任何地方", "API", "担保",
                    "著作权", "签名公钥", "不同意"):
        assert keyword in body, keyword
    # Tk 文本框不认 Markdown，正文里不该出现 ** 这种标记
    assert "**" not in body
    assert len(body) > 400          # 太短说明内容被删了


def test_default_config_has_agreement_keys():
    assert DEFAULT_CONFIG["agreement_version"] == 0
    assert DEFAULT_CONFIG["agreement_accepted_at"] == ""
    assert disclaimer.needs_agreement(DEFAULT_CONFIG) is True
