"""DDO 聊天翻译助手 —— 重构版。"""

import time
from pathlib import Path

__version__ = "3.0.35"
AUTHOR = "一键三连"
HOMEPAGE = "https://gitee.com/git55236/ddo-chat-translator"

_STAMP = {"value": None}


def source_stamp() -> str:
    """这份代码的"指纹"：git 短哈希 + 关键文件的修改时间。

    为什么要它：这个项目在磁盘上有很多副本（备份夹 + 打包版 + 源码），历史上真出现过
    "改了但用户跑的是另一份"的情况；用户自己也说过"不知道是哪个版本"。把它放在
    设置 → 关于 和启动日志里，一眼就能对上"到底跑的是哪一份"。
    """
    if _STAMP["value"] is not None:
        return _STAMP["value"]
    root = Path(__file__).resolve().parent
    parts = []
    try:
        head = (root.parent / ".git" / "HEAD")
        text = head.read_text(encoding="utf-8").strip()
        if text.startswith("ref:"):
            ref = root.parent / ".git" / text.split(" ", 1)[1].strip()
            text = ref.read_text(encoding="utf-8").strip() if ref.exists() else text
        parts.append("git %s" % text[:7])
    except Exception:                              # noqa: BLE001
        parts.append("打包版")                      # 没有 .git 就是打包出来的
    for name in ("ui/tour.py", "ui/frameless.py"):
        try:
            mtime = (root / name).stat().st_mtime
            parts.append("%s %s" % (Path(name).name,
                                    time.strftime("%m-%d %H:%M", time.localtime(mtime))))
        except Exception:                          # noqa: BLE001
            continue
    _STAMP["value"] = " / ".join(parts)
    return _STAMP["value"]
