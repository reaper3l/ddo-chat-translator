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
    for keyword in ("第三方", "服务条款", "API", "担保",
                    "著作权", "签名公钥", "不同意",
                    # 联网与"参与改进"必须写清楚（默认关、只发确认过的、能预览）
                    "公共词典", "参与改进", "默认关闭", "匿名", "预览",
                    # v3 起：开启后默认"关程序时自动发"，这条必须写明（否则等于没说）
                    "关闭程序时自动发送", "直接上传"):
        assert keyword in body, keyword
    # Tk 文本框不认 Markdown，正文里不该出现 ** 这种标记
    assert "**" not in body
    assert len(body) > 400          # 太短说明内容被删了


def test_default_config_has_agreement_keys():
    assert DEFAULT_CONFIG["agreement_version"] == 0
    assert DEFAULT_CONFIG["agreement_accepted_at"] == ""
    assert disclaimer.needs_agreement(DEFAULT_CONFIG) is True


class _FakeLogger:
    """假装成 logging，把写日志的调用记下来（不碰真的 app.log）。"""

    def __init__(self):
        self.lines = []

    def info(self, template, *args):
        self.lines.append(template % args if args else template)

    def warning(self, template, *args):
        self.lines.append(template % args if args else template)


def test_agreement_is_written_to_log():
    """同意的时间和条款版本要进日志：config.json 可能被删，日志是按时间追加的。"""
    logger = _FakeLogger()
    disclaimer.log_acceptance("2026-09-28 21:05:00", logger=logger)
    assert len(logger.lines) == 1, logger.lines
    line = logger.lines[0]
    assert "2026-09-28 21:05:00" in line
    assert "条款版本 %d" % disclaimer.DISCLAIMER_VERSION in line
    assert "同意" in line


def test_decline_is_written_to_log():
    logger = _FakeLogger()
    disclaimer.log_decline(logger=logger)
    assert logger.lines and "未同意" in logger.lines[0]
    assert "条款版本 %d" % disclaimer.DISCLAIMER_VERSION in logger.lines[0]
