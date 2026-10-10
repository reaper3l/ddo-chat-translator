"""插件：发现（`plugins/<id>/manifest.json`）+ 拉起（命令行 + stdin/stdout JSON）。

为什么要"进程 + JSON"而不是"加载 Python 模块进本进程"：

* 插件写崩了 / 死循环了，不会把翻译器一起带走；
* 插件用什么语言写都行（Python / exe / bat / node…），只要会读写 JSON；
* 超时可以直接把进程杀掉，界面永远不会被卡住。

协议（给写插件的人看的，完整版见 `docs/平台接口.md`）：

    宿主 → 插件（stdin 一次性写入，然后关掉 stdin）
        {"version": 1, "action": "<动作 id>", "payload": {...},
         "platform": {"base_url": "http://127.0.0.1:8765", "token": "<该插件的令牌>"}}

    插件 → 宿主（stdout 最后一段 JSON）
        {"ok": true, "message": "给用户看的一句话", "text": "可选的正文",
         "output": {...}, "files": ["可选：生成的文件的绝对路径"]}

    命令行：<command...> <action_id>，工作目录=插件目录，环境变量里额外带
        DDO_PLUGIN=1 / DDO_PLATFORM_URL / DDO_PLATFORM_TOKEN
    （三种方式任选，图省事就直接读 stdin）
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .. import paths

MANIFEST_NAME = "manifest.json"
DEFAULT_TIMEOUT = 180.0
MAX_TIMEOUT = 3600.0
MAX_OUTPUT_CHARS = 2_000_000
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class PluginError(Exception):
    """插件相关的可预期错误（界面直接把 message 显示给用户）。"""


def plugins_dir() -> Path:
    """插件目录：源码运行 = 项目里的 `plugins\\`；打包版 = exe 旁边（用户可写）。"""
    return paths.APP_DIR / "plugins"


def seeds_dir() -> Path:
    """随程序附带的示例插件（打包时放在 `_internal/examples/plugins`）。"""
    return paths.BUNDLE_DIR / "examples" / "plugins"


def ensure_plugins_dir(root=None) -> Path:
    """确保插件目录存在；**第一次跑（打包版）时把自带的示例插件放进去**。

    为什么要"复制一份"而不是直接在打包目录里读：程序目录（exe 旁边）才是用户
    能改、能加插件的地方；`_internal` 里的那份是只读的随包资源。
    """
    target = Path(root) if root is not None else plugins_dir()
    try:
        target.mkdir(parents=True, exist_ok=True)
        seeds = seeds_dir()
        if not seeds.is_dir():
            return target
        if any(target.iterdir()):
            return target
        for child in sorted(seeds.iterdir()):
            try:
                if child.is_dir():
                    shutil.copytree(child, target / child.name, dirs_exist_ok=True)
            except Exception:                     # noqa: BLE001
                continue
    except Exception:                             # noqa: BLE001
        pass
    return target


def python_command() -> List[str]:
    """跑 `*.py` 插件用的解释器。

    打包版自己没有 Python，只能找系统里的；找不到就明确告诉用户怎么回事，
    而不是抛一个看不懂的 WinError。
    """
    if not getattr(sys, "frozen", False):
        return [sys.executable]
    for name in ("python", "python3", "py"):
        found = shutil.which(name)
        if found:
            return [found]
    return []


@dataclass
class Plugin:
    id: str
    name: str
    version: str = ""
    description: str = ""
    author: str = ""
    directory: Path = field(default_factory=Path)
    runtime: str = "python"
    entry: str = ""
    command: List[str] = field(default_factory=list)
    actions: List[dict] = field(default_factory=list)
    scopes: List[str] = field(default_factory=list)
    readme: str = ""
    error: str = ""                      # manifest 有问题时这里放原因
    raw: dict = field(default_factory=dict)

    # ---------------------------------------------------------------
    @property
    def ok(self) -> bool:
        return not self.error

    def action(self, action_id: str) -> Optional[dict]:
        for item in self.actions:
            if str(item.get("id", "")) == str(action_id):
                return item
        return None

    def argv(self, action_id: str = "") -> List[str]:
        """拼出实际要执行的命令行。"""
        if self.command:
            base = [part.replace("{plugin_dir}", str(self.directory))
                    for part in self.command]
        else:
            entry = (self.directory / self.entry) if self.entry else None
            if entry is None:
                raise PluginError("manifest 里既没有 command 也没有 entry")
            if str(self.entry).lower().endswith(".py"):
                python = python_command()
                if not python:
                    raise PluginError(
                        "这是一个 Python 插件，但当前是打包版、系统里找不到 Python。"
                        "请安装 Python 3 后再试，或改用 exe 形式的插件。")
                base = python + [str(entry)]
            else:
                base = [str(entry)]
        if action_id:
            base = base + [str(action_id)]
        return base


class PluginRegistry:
    """发现 + 启用状态 + 调用插件。启用状态存在 `data/platform.json`（TokenStore）。"""

    def __init__(self, tokens=None, root: Optional[Path] = None) -> None:
        self.tokens = tokens
        self.root = Path(root) if root is not None else plugins_dir()
        self._cache: Dict[str, Plugin] = {}

    # ------------------------------------------------------------ 扫描
    def scan(self, use_cache: bool = False) -> List[Plugin]:
        if use_cache and self._cache:
            return list(self._cache.values())
        if self._uses_default_root():
            ensure_plugins_dir(self.root)          # 打包版第一次跑：把示例插件铺出来
        found: Dict[str, Plugin] = {}
        try:
            entries = sorted(self.root.iterdir()) if self.root.is_dir() else []
        except Exception:
            entries = []
        for child in entries:
            try:
                if not child.is_dir():
                    continue
            except Exception:
                continue
            manifest = child / MANIFEST_NAME
            if not manifest.is_file():
                continue
            plugin = self._load_manifest(child, manifest)
            if plugin is not None:
                found[plugin.id] = plugin
        self._cache = found
        return list(found.values())

    def _uses_default_root(self) -> bool:
        try:
            return Path(self.root) == plugins_dir()
        except Exception:                         # noqa: BLE001
            return False

    @staticmethod
    def _load_manifest(directory: Path, manifest: Path) -> Optional[Plugin]:
        try:
            raw = json.loads(manifest.read_text(encoding="utf-8"))
        except Exception as exc:                     # noqa: BLE001
            return Plugin(id=directory.name, name=directory.name,
                          directory=directory, error="manifest.json 读不了：%s" % exc)
        if not isinstance(raw, dict):
            return Plugin(id=directory.name, name=directory.name,
                          directory=directory, error="manifest.json 不是一个对象")
        pid = str(raw.get("id") or directory.name).strip()
        if not _ID_RE.match(pid):
            return Plugin(id=directory.name, name=directory.name, directory=directory,
                          error="插件 id 不合法（只允许小写字母/数字/._-）：%s" % pid)
        actions = []
        for item in (raw.get("actions") or []):
            if isinstance(item, dict) and str(item.get("id", "")).strip():
                actions.append({"id": str(item["id"]).strip(),
                                "name": str(item.get("name") or item["id"]).strip(),
                                "desc": str(item.get("desc") or "").strip()})
            elif isinstance(item, str) and item.strip():
                actions.append({"id": item.strip(), "name": item.strip(), "desc": ""})
        command = [str(part) for part in (raw.get("command") or [])]
        plugin = Plugin(
            id=pid,
            name=str(raw.get("name") or pid),
            version=str(raw.get("version") or ""),
            description=str(raw.get("description") or ""),
            author=str(raw.get("author") or ""),
            directory=directory,
            runtime=str(raw.get("runtime") or ("python" if str(raw.get("entry", "")).lower().endswith(".py") else "exe")),
            entry=str(raw.get("entry") or ""),
            command=command,
            actions=actions,
            scopes=[str(s) for s in (raw.get("scopes") or [])],
            readme=str(raw.get("readme") or ""),
            raw=raw,
        )
        if not plugin.command and not plugin.entry:
            plugin.error = "manifest 里既没有 command 也没有 entry，宿主不知道怎么启动它"
        elif not plugin.command and plugin.entry and not (directory / plugin.entry).is_file():
            plugin.error = "找不到 entry 指向的文件：%s" % plugin.entry
        return plugin

    def get(self, plugin_id: str, use_cache: bool = True) -> Optional[Plugin]:
        pid = str(plugin_id or "").strip()
        if use_cache and pid in self._cache:
            return self._cache[pid]
        for plugin in self.scan(use_cache=False):
            if plugin.id == pid:
                return plugin
        return None

    def refresh(self) -> List[Plugin]:
        return self.scan(use_cache=False)

    # ------------------------------------------------------------ 启用 / 权限
    def is_enabled(self, plugin_id: str) -> bool:
        return bool(self.tokens and self.tokens.is_enabled(plugin_id))

    def set_enabled(self, plugin_id: str, enabled: bool) -> dict:
        if self.tokens is None:
            return {"ok": False, "error": "令牌存储不可用"}
        plugin = self.get(plugin_id)
        if plugin is None:
            return {"ok": False, "error": "找不到插件 %s" % plugin_id}
        if enabled and plugin.error:
            return {"ok": False, "error": plugin.error}
        if enabled:                              # 第一次启用就发一把令牌
            self.tokens._entry(plugin_id)
        self.tokens.set_enabled(plugin_id, bool(enabled))
        return {"ok": True, "plugin": plugin_id, "enabled": bool(enabled)}

    # ------------------------------------------------------------ 运行
    def run(self, plugin_id: str, action_id: str = "", payload=None,
            timeout: float = DEFAULT_TIMEOUT, base_url: str = "") -> dict:
        plugin = self.get(plugin_id)
        if plugin is None:
            return {"ok": False, "error": "找不到插件：%s" % plugin_id}
        if plugin.error:
            return {"ok": False, "error": plugin.error}
        if self.tokens is not None and not self.tokens.is_enabled(plugin_id):
            return {"ok": False, "error": "插件还没启用（设置 → 平台 / 插件）"}
        try:
            argv = plugin.argv(action_id)
        except PluginError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:                 # noqa: BLE001
            return {"ok": False, "error": "拼不出命令行：%s" % exc}
        if action_id and plugin.action(action_id) is None:
            return {"ok": False, "error": "插件没有这个动作：%s" % action_id}

        token = self.tokens.token_for(plugin_id) if self.tokens else ""
        request = {"version": 1, "action": action_id or "",
                   "payload": payload if payload is not None else {},
                   "platform": {"base_url": base_url or "", "token": token},
                   "plugin": {"id": plugin.id, "name": plugin.name,
                              "dir": str(plugin.directory)}}
        env = dict(os.environ)
        env["DDO_PLUGIN"] = "1"
        env["DDO_PLATFORM_URL"] = base_url or ""
        env["DDO_PLATFORM_TOKEN"] = token
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        try:
            timeout = max(1.0, min(MAX_TIMEOUT, float(timeout or DEFAULT_TIMEOUT)))
        except Exception:
            timeout = DEFAULT_TIMEOUT
        return self._exec(plugin, argv, request, env, timeout)

    @staticmethod
    def _creation_kwargs() -> dict:
        if sys.platform == "win32":
            # CREATE_NO_WINDOW：别给用户弹一个黑框出来
            return {"creationflags": 0x08000000}
        return {"start_new_session": True}

    def _exec(self, plugin: Plugin, argv: Sequence[str], request: dict,
              env: dict, timeout: float) -> dict:
        started = time.time()
        try:
            proc = subprocess.Popen(
                list(argv), cwd=str(plugin.directory), env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
                **self._creation_kwargs())
        except FileNotFoundError:
            return {"ok": False, "plugin": plugin.id,
                    "error": "启动失败：找不到 %s（插件依赖的程序没装？）" % argv[0]}
        except Exception as exc:                 # noqa: BLE001
            return {"ok": False, "plugin": plugin.id, "error": "启动失败：%s" % exc}

        payload_text = json.dumps(request, ensure_ascii=False)
        timed_out = False
        try:
            out, err = proc.communicate(input=payload_text, timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_tree(proc)
            try:
                out, err = proc.communicate(timeout=10)
            except Exception:                    # noqa: BLE001
                out, err = "", ""
        except Exception as exc:                 # noqa: BLE001
            _kill_tree(proc)
            return {"ok": False, "plugin": plugin.id, "error": "运行出错：%s" % exc}

        elapsed = int((time.time() - started) * 1000)
        out = (out or "")[:MAX_OUTPUT_CHARS]
        err = (err or "")[-4000:]
        if timed_out:
            return {"ok": False, "plugin": plugin.id, "elapsed_ms": elapsed,
                    "error": "插件超时（超过 %.0f 秒，已强制结束）" % timeout,
                    "stderr": err}
        parsed, raw_output = _parse_output(out)
        result = {
            "ok": bool(parsed.get("ok", proc.returncode == 0)) if parsed else False,
            "plugin": plugin.id,
            "action": request.get("action", ""),
            "elapsed_ms": elapsed,
            "returncode": proc.returncode,
            "stderr": err,
        }
        if parsed:
            result["message"] = str(parsed.get("message") or "")
            result["text"] = str(parsed.get("text") or "")
            result["output"] = parsed.get("output")
            files = parsed.get("files")
            result["files"] = [str(item) for item in files] if isinstance(files, list) else []
            if not result["ok"] and not parsed.get("message"):
                result["error"] = str(parsed.get("error") or "插件报告失败")
        else:
            result["ok"] = False
            result["text"] = raw_output
            result["error"] = ("插件没有输出可解析的 JSON（退出码 %s）。"
                               "它打印了什么，看下面。" % proc.returncode)
        if not result["ok"] and err and not result.get("error"):
            result["error"] = err.strip().splitlines()[-1] if err.strip() else "插件失败"
        return result

    # ------------------------------------------------------------ 安装 / 卸载
    def install_zip(self, zip_path) -> dict:
        """从 zip 装一个插件（手动分发那条路：不给订阅源，用户自己选文件）。"""
        source = Path(zip_path)
        if not source.is_file():
            return {"ok": False, "error": "找不到文件：%s" % source}
        temp = Path(tempfile.mkdtemp(prefix="ddo_plugin_"))
        try:
            try:
                with zipfile.ZipFile(source) as archive:
                    _safe_extract(archive, temp)
            except zipfile.BadZipFile:
                return {"ok": False, "error": "这不是一个有效的 zip 文件"}
            except PluginError as exc:
                return {"ok": False, "error": str(exc)}
            manifest = _find_manifest(temp)
            if manifest is None:
                return {"ok": False, "error": "压缩包里没有找到 manifest.json"}
            plugin = self._load_manifest(manifest.parent, manifest)
            if plugin is None:
                return {"ok": False, "error": "manifest.json 解析失败"}
            if plugin.error:
                return {"ok": False, "error": plugin.error}
            target = self.root / plugin.id
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            self.root.mkdir(parents=True, exist_ok=True)
            shutil.copytree(manifest.parent, target)
            self.refresh()
            return {"ok": True, "plugin": plugin.id, "name": plugin.name,
                    "directory": str(target),
                    "message": "已安装到 %s（默认未启用）" % target}
        except Exception as exc:                 # noqa: BLE001
            return {"ok": False, "error": "安装失败：%s" % exc}
        finally:
            shutil.rmtree(temp, ignore_errors=True)

    def uninstall(self, plugin_id: str) -> dict:
        plugin = self.get(plugin_id)
        if plugin is None:
            return {"ok": False, "error": "找不到插件：%s" % plugin_id}
        try:
            shutil.rmtree(plugin.directory, ignore_errors=True)
        except Exception as exc:                 # noqa: BLE001
            return {"ok": False, "error": "删不掉：%s" % exc}
        if self.tokens is not None:
            self.tokens.forget(plugin_id)
        self.refresh()
        return {"ok": True, "plugin": plugin_id}


# ---------------------------------------------------------------- 辅助
def _kill_tree(proc) -> None:
    """把插件进程**连同它的子进程**一起结束（只 kill 父进程会留下孤儿）。"""
    if proc is None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, creationflags=0x08000000)
        else:
            import signal
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                proc.kill()
    except Exception:                            # noqa: BLE001
        try:
            proc.kill()
        except Exception:
            pass


def _parse_output(text: str):
    """插件输出可能是"纯 JSON"或"日志若干行 + 最后一段 JSON"，两种都认。"""
    text = (text or "").strip()
    if not text:
        return None, ""
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data, text
    except Exception:                            # noqa: BLE001
        pass
    for line in reversed(text.splitlines()):
        chunk = line.strip()
        if not chunk.startswith("{"):
            continue
        try:
            data = json.loads(chunk)
        except Exception:                        # noqa: BLE001
            continue
        if isinstance(data, dict):
            return data, text
    return None, text


def _safe_extract(archive: zipfile.ZipFile, target: Path) -> None:
    """解压前先确认包里没有绝对路径 / `..`（zip slip）。"""
    target = target.resolve()
    for name in archive.namelist():
        candidate = (target / name).resolve()
        try:
            candidate.relative_to(target)
        except ValueError:
            raise PluginError("压缩包里有不安全的路径：%s" % name)
    archive.extractall(str(target))


def _find_manifest(root: Path) -> Optional[Path]:
    direct = root / MANIFEST_NAME
    if direct.is_file():
        return direct
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / MANIFEST_NAME).is_file():
            return child / MANIFEST_NAME
    return None
