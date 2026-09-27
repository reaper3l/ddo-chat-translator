"""统一的路径与 JSON 读写（全部基于程序所在目录的绝对路径）。

旧版程序大量使用相对路径（open("config.json")），换个工作目录启动就会读写
失败或写错位置。这里集中管理，并且所有写入都是"先写临时文件再替换"的原子写，
避免程序中途被杀导致配置/学习库损坏。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


def _application_dir() -> Path:
    # PyInstaller onedir: 可执行文件旁边；源码运行: 项目根目录
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _bundle_dir() -> Path:
    """打包后"随程序一起发出去的数据"（术语表、图标）所在目录。

    PyInstaller onedir 会把 datas 放进 exe 旁边的 `_internal`，而
    `_MEIPASS` 正好指向那里。**必须和"用户数据目录"分开处理**：数据文件在
    `_internal`，config/学习库却在 exe 旁边。不分开的话打包版读不到术语表 ——
    界面看起来一切正常，只有术语保护整块失效（v3.0.0~v3.0.5 的 exe 就是这样，
    术语 0 条）。
    """
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        return Path(bundle)
    return _application_dir()


APP_DIR = _application_dir()
BUNDLE_DIR = _bundle_dir()


def _assets_dir() -> Path:
    """术语表目录：优先 exe/项目旁边的 assets（用户可自行替换词表），
    没有就用打包内置的那份，最后兜底返回本地路径（让报错指向该放文件的地方）。"""
    local = APP_DIR / "assets"
    if local.is_dir():
        return local
    bundled = BUNDLE_DIR / "assets"
    if bundled.is_dir():
        return bundled
    return local


ASSETS_DIR = _assets_dir()
DATA_DIR = APP_DIR / "data"
LOG_DIR = DATA_DIR / "logs"

CONFIG_PATH = DATA_DIR / "config.json"
MEMORY_PATH = DATA_DIR / "memory.json"
CACHE_PATH = DATA_DIR / "cache.json"
GLOSSARY_PATH = ASSETS_DIR / "glossary.json"
GLOSSARY_EXTRA_PATH = ASSETS_DIR / "glossary_extra.json"
LOG_PATH = LOG_DIR / "app.log"


def icon_path() -> Path:
    """窗口图标的实际位置（源码运行 / 打包后都能找到）。"""
    candidates = [APP_DIR / "app_icon.ico", BUNDLE_DIR / "app_icon.ico",
                  ASSETS_DIR / "app_icon.ico"]
    for candidate in candidates:
        try:
            if candidate.exists():
                return candidate
        except Exception:
            continue
    return candidates[0]


def ensure_dirs() -> None:
    for path in (ASSETS_DIR, DATA_DIR, LOG_DIR):
        path.mkdir(parents=True, exist_ok=True)


def ensure_path(path) -> Path:
    """把字符串/Path 统一成 Path 对象。"""
    return path if isinstance(path, Path) else Path(path)


def read_json(path: Path, default: Any = None) -> Any:
    """读取 JSON；文件不存在或损坏时返回 default，不抛异常。"""
    if default is None:
        default = {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default
    except Exception:
        return default


def write_json(path: Path, data: Any) -> bool:
    """原子写入 JSON。失败返回 False（调用方决定是否提示）。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return True
    except Exception:
        return False
