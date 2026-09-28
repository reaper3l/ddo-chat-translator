"""使用须知 / 免责声明：第一次打开程序时必须点"同意"才能用。

为什么要有它：这是个第三方游戏辅助工具，边界必须一开始就讲清楚 ——
它不是官方产品、可能和游戏服务条款有冲突、翻译结果可能出错、API Key 和费用是用户自己的、
数据留在本地。与其把这些埋在文档里，不如在**程序最开始**明确说一次，用户点了"同意"再继续。

约定：

* 条款有版本号 `DISCLAIMER_VERSION`。改了内容就 +1，程序会再让用户确认一次
  （老用户升级后也会看到，避免"条款变了但没人知道"）。
* 同意状态存在本地 `data\\config.json`：`agreement_version` + `agreement_accepted_at`。
  这既是功能需要，也是"用户确实被告知过"的凭据。
* 不同意 = 退出程序，程序不做任何事（不截图、不联网、不翻译）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional

# 改了下面的正文就要把这个数字 +1（用户会重新看到一次确认窗口）
DISCLAIMER_VERSION = 1
DISCLAIMER_DATE = "2026-09-28"
DISCLAIMER_TITLE = "使用须知与免责声明"

# 正文：纯文本（Tk 文本控件不认 Markdown，所以这里不用 ** 之类的标记）。
DISCLAIMER_TEXT = """DDO 聊天翻译助手 · 使用须知与免责声明（条款版本 {version}　更新日期 {date}）

一、这不是游戏官方产品
本工具是个人开发的第三方辅助工具，与《龙与地下城 Online》及其运营方没有任何隶属关系，
也未获得其授权、认可或背书。

二、它只做三件事
1）在你框选的屏幕区域内截图；2）用 OCR 识别聊天文字；3）把文字发给你自己配置的
翻译服务（如 DeepSeek）。它不读取也不修改游戏进程内存，不注入、不修改游戏文件，
不会替你操作游戏。

三、使用风险由你自行承担
部分游戏的服务条款可能不欢迎任何第三方工具。因使用本工具可能导致的账号风险、处罚
或其他损失，请你自行判断并承担。使用前请先确认你所在服务器的规则。

四、翻译结果仅供参考
翻译由第三方大模型生成，可能出错（术语、语气、昵称，尤其是游戏俚语与缩写）。
请不要把翻译结果用于交易、指挥等需要准确性的判断。

五、你的数据留在本地
配置、学习库、缓存、日志都保存在程序目录的 data 文件夹里，程序不会把它们上传到任何地方。
只有"需要翻译的那几句文字"会发送给你自己配置的翻译服务（用你自己的 API Key），
接口费用与使用合规由你负责。

六、没有担保
本程序按"现状"提供，作者不对其可用性、准确性或任何间接损失作出担保。在适用法律
允许的范围内，作者不承担因使用本程序而产生的责任。

七、版权与官方版本
本程序的著作权归作者所有（详见《版权与使用条款.md》）。只有发布在官方仓库、
并且能用作者的签名公钥验证通过的安装包才是官方版本；其它来源的副本与作者无关。
请不要删除或绕过程序内的签名校验机制。

点击「我已阅读并同意」，表示你已阅读、理解并接受以上各条内容；
点击「不同意，退出」，程序将直接关闭，不会做任何事。
"""


def text(version: int = DISCLAIMER_VERSION, date: str = DISCLAIMER_DATE) -> str:
    """返回要显示给用户的正文（版本号和日期自动填进去）。"""
    return DISCLAIMER_TEXT.format(version=version, date=date)


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def needs_agreement(config: Mapping[str, Any]) -> bool:
    """这份配置是不是还没同意（或者同意的是旧版本条款）。"""
    try:
        accepted = _as_int(config.get("agreement_version"))
    except AttributeError:
        return True
    return accepted < DISCLAIMER_VERSION


def accept(config: dict, now: Optional[datetime] = None) -> str:
    """记录"用户同意了这一版条款"，返回同意时间的字符串（本地时间）。"""
    stamp = (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    config["agreement_version"] = DISCLAIMER_VERSION
    config["agreement_accepted_at"] = stamp
    return stamp


def accepted_at(config: Mapping[str, Any]) -> str:
    """已同意的时间（没同意过返回空串）。"""
    return str(config.get("agreement_accepted_at") or "")


# ---------------------------------------------------------------------- 留痕
# 同意状态写在 config.json 里，但配置文件可能被用户删掉/覆盖；日志（data\logs\app.log）
# 是按时间追加、不会被程序重写的，所以同意的**时间点**再往日志里记一条 ——
# 将来真要举证"用户在什么时候被告知并同意了哪一版条款"，日志是更稳的那份证据。
def log_acceptance(stamp: str, version: int = DISCLAIMER_VERSION, logger=None) -> None:
    """记下"用户同意了第 N 版条款"，以及同意的时间。"""
    import logging

    from . import __version__

    (logger or logging).info(
        "使用须知：用户同意条款版本 %d（同意时间 %s，程序版本 v%s）", version, stamp,
        __version__)


def log_decline(logger=None) -> None:
    """记下"用户没同意、程序退出"（同样是留痕：说明程序没做任何事就关了）。"""
    import logging

    from . import __version__

    (logger or logging).warning(
        "使用须知：用户未同意条款版本 %d，程序退出（未做任何截图/翻译，程序版本 v%s）",
        DISCLAIMER_VERSION, __version__)
