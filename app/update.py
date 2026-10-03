"""自动检查更新 + 在线升级。

* **查**：读 Gitee 的 `releases/latest` 接口（公开接口，不需要 Token），拿到最新
  版本号和发行说明，和当前 `__version__` 比一比；
* **升**：下载发行版压缩包 → 解压到临时目录 → 启动"解压出来的新 exe"（带上目标目录）
  → 旧程序退出 → 新 exe 把文件覆盖到程序目录（**保留 data\\ 用户数据**）→
  启动程序目录里的新版。整个过程写日志到程序目录的 `ddo_update.log`，
  失败会弹窗说明并把旧版本重新打开（不再用批处理脚本，见下面 APPLY_FLAG 那段注释）。

只有**打包版 exe**能自动升级；源码运行（`python main.py`）只提示去发行版下载或
`git pull` —— 直接改源码树太危险，而且用户多半是用 git 管理的。

这里所有网络/文件操作都是普通函数，方便单测（`check()` 允许注入 fetcher）。
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
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from . import AUTHOR, HOMEPAGE, __version__
from . import ed25519

API_LATEST = "https://gitee.com/api/v5/repos/git55236/ddo-chat-translator/releases/latest"
# 备用更新源（GitHub 镜像仓库）：Gitee 打不开、被限流或临时抽风时从这里查。
# 两边是**同一批发行版、同一个安装包、同一个签名**，所以走哪个源都安全。
API_LATEST_MIRROR = "https://api.github.com/repos/reaper3l/ddo-chat-translator/releases/latest"
MIRROR_HOMEPAGE = "https://github.com/reaper3l/ddo-chat-translator"
USER_AGENT = "DDOTranslator/%s (+%s)" % (__version__, HOMEPAGE)

# --------------------------------------------------------------------------
# 发布包签名（防"被人改了源码 → 自动更新装成篡改版"）
#
# 规则很简单：**只有用下面这把公钥对应的私钥签过的安装包，程序才会自动安装**。
# 私钥只在你自己的机器上（tools/sign_release.py 生成，默认 %USERPROFILE%\.ddo-release），
# 永远不进仓库、不进 exe。这样一来：
#   * 别人就算拿到 Gitee 账号、改了源码、传了个假包 —— 程序验签不通过，直接拒绝安装；
#   * 反过来你也能自证：发布页上的签名谁能用这把公钥验过，才可能是你发的。
# 公钥为空 = 还没配置：程序**不会**自动装任何东西（只会提示你去发行页手动下载）。
# --------------------------------------------------------------------------
#
# 现在配了**两把**（空格分隔，顺序有意义）：
#   第 1 把 = 主密钥：日常发版就用它签（私钥在 %USERPROFILE%\.ddo-release\release.key）；
#   第 2 把 = 备用密钥：离线另存，平时不用。主密钥丢了或者要轮换时，用它签的包
#             —— 装了本版（含这把公钥）的用户照样认，不至于从此没法给老用户发更新。
# 任何一把验过都算官方包；验签说明里会写明是"主密钥"还是"备用密钥 #n"签的。
# 换钥匙的完整做法见《安全与验证说明.md》第 4 节。
RELEASE_PUBKEY = ("e19e5e79cabce8b62435ed81ebae771a58cdf449c45550cb6e44272462ea37d7 "
                  "2551b83b049916cd59819b6d40cda216eb706dda90dd27c286a9fbe11abd1bc2")

# "打开发行页"只允许这些域名 —— 万一账号被别人拿走，他能在发行版里塞钓鱼链接，
# 更新窗口会把地址交给浏览器，所以这里卡一道。
TRUSTED_HOSTS = ("gitee.com", "github.com", "githubusercontent.com")

# 发行说明里的签名块（发布时由签名工具打印，粘到 Gitee 发行说明末尾即可）
SIGNATURE_MARK = "---- DDO-RELEASE-SIGNATURE ----"


@dataclass
class UpdateInfo:
    version: str                 # 最新版本号，如 "3.0.20"
    tag: str = ""                # 原始 tag，如 "v3.0.20"
    notes: str = ""              # 发行说明（Markdown 文本）
    page_url: str = ""           # 发行页地址（打不开自动升级时的兜底）
    asset_url: str = ""          # 压缩包下载地址
    asset_name: str = ""         # 压缩包文件名
    asset_size: int = 0          # 字节
    current: str = __version__
    extra: dict = field(default_factory=dict)

    @property
    def size_mb(self) -> float:
        return self.asset_size / (1024.0 * 1024.0) if self.asset_size else 0.0


def parse_version(text: str) -> tuple:
    """把 "v3.0.20" / "3.0.20" / "3.0.20.1" 解析成可比较的元组。"""
    numbers = re.findall(r"\d+", str(text or ""))
    if not numbers:
        return ()
    return tuple(int(part) for part in numbers[:4])


def is_newer(candidate: str, current: str = __version__) -> bool:
    """candidate 是不是比 current 新。"""
    left, right = parse_version(candidate), parse_version(current)
    if not left:
        return False
    # 位数不同时补齐再比（3.1 看成 3.1.0）
    size = max(len(left), len(right))
    left = left + (0,) * (size - len(left))
    right = right + (0,) * (size - len(right))
    return left > right


def _http_get(url: str, timeout: float = 8.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                   "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _parse_release(data, current: str, homepage: str) -> Optional["UpdateInfo"]:
    """把一份发行版 JSON（Gitee / GitHub 的字段差不多）解析成 UpdateInfo。"""
    if not isinstance(data, dict):
        return None
    tag = str(data.get("tag_name") or "").strip()
    if not tag or not is_newer(tag, current):
        return None
    info = UpdateInfo(version=tag.lstrip("vV"), tag=tag,
                      notes=str(data.get("body") or ""),
                      page_url=str(data.get("html_url") or ""),
                      current=current)
    if not info.page_url or not page_url_ok(info.page_url):
        # Gitee 的这个接口不总是给 html_url，按 tag 自己拼一个发行页地址；
        # 给了但不是 Gitee/GitHub 的（账号被拿走后的钓鱼链接）也一律不用。
        info.page_url = "%s/releases/tag/%s" % (homepage.rstrip("/"), tag)
    # Gitee 的 assets 里有下载地址和文件名（id 要另外查，这里用不到）
    for asset in (data.get("assets") or []):
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "")
        url = str(asset.get("browser_download_url") or "")
        if not url or name.endswith((".tar.gz", ".source")):
            continue
        if name.lower().endswith(".zip") and "archive/refs/tags" not in url:
            info.asset_url, info.asset_name = url, name
            break
    if not info.asset_url:
        # 没有可下载的包（例如附件被清理了）→ 让界面引导去发行页
        info.asset_url = ""
    return info


def check(current: str = __version__, fetcher: Optional[Callable[[str], bytes]] = None,
          timeout: float = 8.0) -> Optional[UpdateInfo]:
    """查最新发行版：先问 Gitee，再问 GitHub 镜像，取两边里**最新**的那个。

    两个源都可以单独失败：Gitee 打不开时自动用 GitHub，反过来也一样。
    """
    fetch = fetcher or (lambda url: _http_get(url, timeout))
    best: Optional[UpdateInfo] = None
    for api, homepage in ((API_LATEST, HOMEPAGE),
                          (API_LATEST_MIRROR, MIRROR_HOMEPAGE)):
        try:
            data = json.loads(fetch(api).decode("utf-8"))
        except Exception:
            continue
        info = _parse_release(data, current, homepage)
        if info is None:
            continue
        if best is None or is_newer(info.version, best.version):
            best = info
    return best


def download(url: str, target: Path,
             progress: Optional[Callable[[int, int], None]] = None,
             timeout: float = 60.0) -> Path:
    """下载到 target（边下边写，可回报进度）。"""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        with open(target, "wb") as handle:
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                done += len(chunk)
                if progress:
                    try:
                        progress(done, total)
                    except Exception:
                        pass
    return target


def can_self_update() -> bool:
    """能不能自动升级（只有打包版可以）。"""
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    if can_self_update():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _find_new_exe(root: Path, version: str) -> Optional[Path]:
    """在解压出来的目录里找新的 exe。"""
    wanted = "*_v%s.exe" % version
    for path in root.rglob(wanted):
        return path
    for path in root.rglob("*.exe"):        # 兜底：名字没对上就用唯一的 exe
        return path
    return None


# ---------------------------------------------------------------------- 自更新
# v3.0.23 起，覆盖文件这件事不再交给批处理脚本，而是**由解压出来的新版本自己干**：
#
#   旧版：下载 → 解压 → 写一个 update.cmd → 退出 → 由 cmd / robocopy 覆盖文件 → 启动新版
#   现在：下载 → 解压 → 启动"临时目录里的新 exe"，带上 `--apply-update <旧目录>`
#         → 旧版退出 → 新 exe 把文件复制过去 → 启动"程序目录里的新版"
#
# 为什么换掉批处理：那段是**哑的** —— 失败时用户看不到任何提示，也没日志可查。
# 真实案例：升级脚本没生效，用户只看到"程序关了、什么都没发生"，临时目录还留着，
# 却无从知道卡在哪一步。现在这几步都在 Python 里做，可以逐文件重试、每步写日志
# （程序目录下的 ddo_update.log）、失败弹窗并**把旧版本重新启动**。
APPLY_FLAG = "--apply-update"              # 新 exe 用这个参数进入"执行更新"模式
TMP_FLAG = "--update-tmp"
PID_FLAG = "--wait-pid"
UPDATE_LOG_NAME = "ddo_update.log"
COPY_ATTEMPTS = 4                          # 复制重试次数（旧版刚退出时文件可能还被占用）
COPY_RETRY_DELAY = 2.0                     # 每次重试之间等几秒
SKIP_DIR_NAMES = ("data",)                 # 用户数据：配置、学习库、缓存、日志 —— 一律不动


@dataclass
class PreparedUpdate:
    """新版本已经解压好，就等程序退出后由它自己把文件覆盖过去。"""

    source: Path          # 新版本所在目录（临时目录里解压出来的那个）
    target: Path          # 程序目录（要被覆盖的那个）
    exe_name: str         # 新版本的 exe 文件名
    tmp_dir: Path         # 临时目录（下次启动时清掉）
    version: str = ""

    def launch(self) -> None:
        launch_updater(self)


def prepare_update(package_zip: Path, version: str,
                   target_dir: Optional[Path] = None) -> PreparedUpdate:
    """解压新版本，返回 PreparedUpdate（调用方随后退出程序，由新版接手覆盖文件）。

    目录安排（都在系统临时目录里）：
        %TEMP%\\ddo_update_xxx\\app\\<新版目录>\\   解压出来的新版本
    """
    package_zip = Path(package_zip)
    target = Path(target_dir or app_dir())
    tmp = Path(tempfile.mkdtemp(prefix="ddo_update_"))
    extract_dir = tmp / "app"
    extract_dir.mkdir()
    with zipfile.ZipFile(package_zip) as archive:
        archive.extractall(extract_dir)
    new_exe = _find_new_exe(extract_dir, version)
    if new_exe is None:
        raise RuntimeError("安装包里没找到 exe")
    return PreparedUpdate(source=new_exe.parent, target=target,
                          exe_name=new_exe.name, tmp_dir=tmp, version=version)


def update_command(prepared: "PreparedUpdate") -> List[str]:
    """启动"新版本 exe"时的完整命令行（单独拿出来方便单测）。

    参数含义：把 `prepared.target`（程序目录）里的旧文件换成自己这一份；
    同时告诉它旧程序的 PID，等旧程序真的退出再动手。
    """
    return [str(prepared.source / prepared.exe_name),
            APPLY_FLAG, str(prepared.target),
            TMP_FLAG, str(prepared.tmp_dir),
            PID_FLAG, str(os.getpid())]


def launch_updater(prepared: "PreparedUpdate") -> None:
    """脱离当前进程启动"临时目录里的新 exe"，由它在我们退出后覆盖文件。"""
    flags = 0
    if os.name == "nt":
        flags = 0x00000008 | 0x00000200      # DETACHED_PROCESS | NEW_PROCESS_GROUP
    subprocess.Popen(update_command(prepared), cwd=str(prepared.source),
                     close_fds=True, creationflags=flags)


# ------------------------------------------------------------------ 执行覆盖
def should_skip(relative: Path) -> bool:
    """复制时跳过哪些：用户数据（data\\）、日志、我们自己的更新日志。"""
    parts = [part.lower() for part in relative.parts]
    if parts and parts[0] in SKIP_DIR_NAMES:
        return True
    if relative.name.lower() == UPDATE_LOG_NAME.lower():
        return True
    return relative.suffix.lower() == ".log"


def copy_tree(source: Path, target: Path) -> List[str]:
    """把新版本的文件覆盖到程序目录；返回没成功的相对路径（空列表 = 全部成功）。"""
    failures: List[str] = []
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if should_skip(relative):
            continue
        destination = target / relative
        try:
            if path.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
        except Exception as exc:
            failures.append("%s（%s）" % (relative, exc))
    return failures


def _log_writer(log_path: Path) -> Callable[[str], None]:
    """写一行日志（同时打印到标准输出，方便手动跑的时候看）。"""
    def log(message: str) -> None:
        line = "%s  %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), message)
        try:
            with open(log_path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except Exception:
            pass
        try:
            print(line)
        except Exception:
            pass
    return log


def wait_for_exit(pid: int, timeout: float = 60.0) -> None:
    """等旧程序退出再动手（Windows 内核等待；不轮询、不依赖第三方库）。"""
    if not pid or os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x00100000, False, int(pid))   # SYNCHRONIZE
        if not handle:
            return                       # 已经退出了（或没权限，那就直接往下走）
        try:
            kernel32.WaitForSingleObject(handle, int(max(1.0, timeout) * 1000))
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return


def _other_exe(target: Path, keep: str) -> Optional[Path]:
    """程序目录里除自己以外的 exe（就是上一版）。"""
    for path in sorted(target.glob("*.exe")):
        if path.name.lower() != keep.lower():
            return path
    return None


def _launch(exe: Path, cwd: Optional[Path] = None) -> None:
    """脱离当前进程启动一个 exe（自己随后退出时它不受影响）。"""
    flags = 0
    if os.name == "nt":
        flags = 0x00000008 | 0x00000200      # DETACHED_PROCESS | NEW_PROCESS_GROUP
    subprocess.Popen([str(exe)], cwd=str(cwd or exe.parent), close_fds=True,
                     creationflags=flags)


def _notify_failure(target: Path, source: Path, log_path: Path,
                    failures: List[str]) -> None:
    """更新失败时：把旧版本重新打开 + 弹窗告诉他怎么手动更新。"""
    old = _other_exe(target, Path(sys.executable).name)
    if old is not None:
        try:
            _launch(old, target)
        except Exception:
            pass
    lines = ["自动更新没有完成：有 %d 个文件没能替换（常见原因：被杀毒软件拦住，"
             "或程序还没完全退出）。" % len(failures),
             "",
             "原来的版本已经帮你重新打开了，可以继续用。",
             "",
             "想手动更新的话：把下面这个文件夹里的内容复制到程序目录",
             "　新版本：%s" % source,
             "　程序目录：%s" % target,
             "　（data 文件夹不要覆盖，那是配置和学习记录）",
             "",
             "详细日志：%s" % log_path]
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, "\n".join(lines),
                                         "DDO 翻译助手 · 更新没成功",
                                         0x00040000 | 0x00000030)
    except Exception:
        pass


def apply_update(target_dir, tmp_dir=None, wait_pid: int = 0) -> int:
    """新版本自己执行更新：等旧版退出 → 覆盖文件 → 启动新版。返回进程退出码。

    只有 `--apply-update` 启动的那份新 exe 会走到这里，不打开界面。
    """
    source = Path(sys.executable).resolve().parent
    target = Path(target_dir)
    log = _log_writer(target / UPDATE_LOG_NAME)
    log("=" * 60)
    log("开始更新：新版 v%s（%s）→ 程序目录 %s" % (__version__, source, target))
    if wait_pid:
        log("等待旧版本退出（PID %s）…" % wait_pid)
        wait_for_exit(int(wait_pid))
        log("旧版本已退出（或超时，后面会按文件重试）")
    failures: List[str] = []
    for attempt in range(1, COPY_ATTEMPTS + 1):
        failures = copy_tree(source, target)
        log("第 %d 次复制：%s" % (attempt, "全部成功" if not failures
                                 else "%d 个文件没成功" % len(failures)))
        if not failures:
            break
        if attempt < COPY_ATTEMPTS:
            time.sleep(COPY_RETRY_DELAY)
    if failures:
        for item in failures[:15]:
            log("  × %s" % item)
        log("更新失败：共 %d 个文件没能覆盖" % len(failures))
        _notify_failure(target, source, target / UPDATE_LOG_NAME, failures)
        return 1
    keep = Path(sys.executable).name
    old = _other_exe(target, keep)
    if old is not None:
        try:
            old.unlink()
            log("删掉旧版本：%s" % old.name)
        except Exception as exc:
            log("旧版本 %s 没能删除（%s）" % (old.name, exc))
    log("更新完成，启动新版：%s" % keep)
    try:
        _launch(target / keep, target)
    except Exception as exc:
        log("启动新版失败：%s" % exc)
        _notify_failure(target, source, target / UPDATE_LOG_NAME, ["启动新版失败"])
        return 1
    if tmp_dir:
        shutil.rmtree(str(tmp_dir), ignore_errors=True)    # 尽力清（自己所在目录删不干净）
    return 0


def parse_apply_args(argv: List[str]) -> dict:
    """从命令行里取 `--apply-update` / `--update-tmp` / `--wait-pid` 的值。"""
    def value(flag: str, default: str = "") -> str:
        if flag in argv:
            index = argv.index(flag)
            if index + 1 < len(argv):
                return str(argv[index + 1])
        return default
    return {"target": value(APPLY_FLAG), "tmp_dir": value(TMP_FLAG),
            "wait_pid": value(PID_FLAG, "0")}


def apply_update_from_argv(argv: List[str]) -> int:
    """`--apply-update` 的入口（main.py 一开始就拦下来）。"""
    options = parse_apply_args(argv)
    if not options["target"]:
        return 1
    try:
        wait_pid = int(options["wait_pid"] or 0)
    except (TypeError, ValueError):
        wait_pid = 0
    return apply_update(options["target"], tmp_dir=options["tmp_dir"] or None,
                        wait_pid=wait_pid)


def open_page(url: str) -> bool:
    """用系统默认浏览器打开发行页（只认可信域名，别把钓鱼链接递给浏览器）。"""
    if not url or not page_url_ok(url):
        return False
    try:
        if sys.platform == "win32":
            os.startfile(url)                                  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", url])
        else:
            subprocess.Popen(["xdg-open", url])
        return True
    except Exception:
        return False


def notes_brief(notes: str, limit: int = 600) -> str:
    """发行说明太长时截一下（对话框里显示用）。"""
    text = re.sub(r"\n{3,}", "\n\n", (notes or "").strip())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "……"


# ---------------------------------------------------------------------- 签名
def pubkeys(text: Optional[str] = None) -> List[str]:
    """把 RELEASE_PUBKEY 拆成公钥列表（允许逗号/空格/分号分隔的多把）。"""
    raw = RELEASE_PUBKEY if text is None else text
    return [part.strip() for part in re.split(r"[,\s;]+", raw or "") if part.strip()]


def pubkey_fingerprint(key_hex: str) -> str:
    """公钥指纹：每 4 个字符一组、全大写，方便人眼和发布页上的对一对。"""
    clean = "".join(ch for ch in (key_hex or "").lower()
                    if ch in "0123456789abcdef")
    if len(clean) != 64:
        return ""
    return " ".join(clean[i:i + 4] for i in range(0, 64, 4)).upper()


def page_url_ok(url: str) -> bool:
    """发行页地址是不是可信域名（只认 Gitee/GitHub，防钓鱼链接）。"""
    try:
        parts = urllib.parse.urlsplit(str(url or ""))
    except Exception:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    host = (parts.hostname or "").lower()
    return any(host == name or host.endswith("." + name) for name in TRUSTED_HOSTS)


def signature_message(version: str, filename: str, digest: str) -> bytes:
    """签名/验签用的"规范化消息"——三样东西必须一一对上，防止被移花接木。"""
    return ("ddo-update-v1\n%s\n%s\n%s\n" % (str(version).strip(),
                                             str(filename).strip(),
                                             str(digest).strip().lower())).encode("utf-8")


def sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    """算文件 sha256（大文件分块读，别一次读进内存）。"""
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def parse_signature(notes: str) -> Optional[dict]:
    """从发行说明里取出签名块（形如 key: value 的四行）。"""
    text = notes or ""
    if SIGNATURE_MARK not in text:
        return None
    block = text.split(SIGNATURE_MARK, 1)[1]
    data = {}
    for line in block.splitlines():
        line = line.strip().strip("`*-— ")
        if not line:
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        if key in ("version", "file", "sha256", "sig") and key not in data:
            data[key] = value.strip()
    if not all(k in data for k in ("version", "file", "sha256", "sig")):
        return None
    return data


def make_signature_block(version: str, filename: str, digest: str,
                         signature: bytes) -> str:
    """生成贴到发行说明里的签名块。"""
    import base64

    return "\n".join([
        SIGNATURE_MARK,
        "version: %s" % version,
        "file: %s" % filename,
        "sha256: %s" % digest,
        "sig: %s" % base64.b64encode(signature).decode("ascii"),
    ])


def verify_package(package: Path, info: "UpdateInfo",
                   pubkey: Optional[str] = None) -> Tuple[bool, str]:
    """校验下载来的安装包是不是"你签过的那个"。

    返回 (是否可信, 说明)。任何一步对不上都算不可信 —— 宁可让用户手动去发行页下载。
    """
    import base64

    key_list = pubkeys(pubkey)
    parsed = parse_signature(info.notes)
    if not key_list:
        return False, "还没有配置发布公钥（见 安全与验证说明.md）"
    if not parsed:
        return False, "这个发行版没有签名"
    if parsed["version"].lstrip("vV") != str(info.version).lstrip("vV"):
        return False, "签名里的版本号（%s）和发行版（%s）对不上" % (
            parsed["version"], info.version)
    if info.asset_name and parsed["file"] != info.asset_name:
        return False, "签名里的文件名（%s）和安装包（%s）对不上" % (
            parsed["file"], info.asset_name)
    try:
        digest = sha256_file(package)
    except Exception as exc:
        return False, "算文件校验和失败：%s" % exc
    if digest.lower() != parsed["sha256"].strip().lower():
        return False, "安装包的 sha256 和签名里写的不一致（下载可能被改过）"
    try:
        signature = base64.b64decode(parsed["sig"])
    except Exception:
        return False, "签名格式不对（base64 解不开）"
    message = signature_message(parsed["version"], parsed["file"], parsed["sha256"])
    for index, key_hex in enumerate(key_list, 1):
        try:
            key = bytes.fromhex(key_hex)
        except Exception:
            continue
        if len(key) != 32:                       # Ed25519 公钥固定 32 字节
            continue
        if ed25519.verify(signature, message, key):
            which = "主密钥" if index == 1 else "备用密钥 #%d" % index
            return True, "签名验证通过（%s，%s）" % (digest[:12], which)
    return False, "签名验证失败（这个包不是用你的私钥签的）"


def cleanup_leftovers() -> None:
    """清掉升级时留在临时目录里的东西（解压出来的新版本、旧版留下的脚本）。

    新版本自己删不掉自己所在的目录（文件被占用），所以要等**下一次启动**时顺手清：
    那时候新程序已经在程序目录里跑，临时目录没人用了。旧版本（≤ v3.0.22）留下的
    `ddo_update_*.cmd` 也在这里一起清掉。
    """
    try:
        temp = Path(tempfile.gettempdir())
    except Exception:
        return
    for pattern in ("ddo_update_*.cmd", "ddo_update_*"):
        for path in temp.glob(pattern):
            try:
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink()
            except Exception:
                continue
