"""自动检查更新 + 在线升级。

* **查**：读 Gitee 的 `releases/latest` 接口（公开接口，不需要 Token），拿到最新
  版本号和发行说明，和当前 `__version__` 比一比；
* **升**：下载发行版压缩包 → 解压到临时目录 → 写一个 `update.cmd` → 退出程序 →
  由那个批处理等几秒、把新文件覆盖到程序目录（**保留 data\\ 用户数据**）→
  重新启动程序 → 自己删除自己。

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
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from . import AUTHOR, HOMEPAGE, __version__
from . import ed25519

API_LATEST = "https://gitee.com/api/v5/repos/git55236/ddo-chat-translator/releases/latest"
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
RELEASE_PUBKEY = "e19e5e79cabce8b62435ed81ebae771a58cdf449c45550cb6e44272462ea37d7"

# 也可以写多把（用逗号/空格分隔）：第一把是"当前在用"的，后面当备用/轮换用 ——
# 任何一把验过都算你的包。好处是私钥万一丢了或要换，已经发出去的老版本 exe 还认得
# 备用钥匙，不至于从此没法再给老用户发更新。发布时用哪把就签哪把（见 tools/sign_release.py）。
# 例：RELEASE_PUBKEY = "<在用公钥> <备用公钥>"

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


def check(current: str = __version__, fetcher: Optional[Callable[[str], bytes]] = None,
          timeout: float = 8.0) -> Optional[UpdateInfo]:
    """查最新发行版；有新版返回 UpdateInfo，没有（或查不到）返回 None。"""
    fetch = fetcher or (lambda url: _http_get(url, timeout))
    try:
        data = json.loads(fetch(API_LATEST).decode("utf-8"))
    except Exception:
        return None
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
        info.page_url = "%s/releases/tag/%s" % (HOMEPAGE.rstrip("/"), tag)
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


# 这个批处理**全部用 ASCII**、而且**不把路径写在文件里** —— 路径是启动时当参数传进来的
# （%~1 源目录、%~2 程序目录、%~3 新 exe 名、%~4 临时目录）。
# 为什么要这样：程序目录/文件名带中文（DDO翻译助手_v3.0.20），而 cmd.exe 是用"控制台
# 代码页"读批处理文件的，把中文路径写进脚本里，换个代码页就全乱（实测 robocopy 直接
# 报"用法错误"退出，什么都没复制）。参数是进程间用 Unicode 传的，不受代码页影响。
# 另外也不改代码页（chcp）—— 那样只会让 echo 的中文更像乱码。
UPDATER_TEMPLATE = """@echo off
title DDO translator - updating
echo Updating DDO translator, please wait...
rem Wait for the app to exit completely: its exe and _internal DLLs are locked while running.
timeout /t 4 /nobreak >nul
robocopy "%~1" "%~2" /E /XD data /XF *.log /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
  echo.
  echo Update failed. Please extract the new package manually over this folder.
  pause
  exit /b 1
)
rem Remove exe files from older versions (each version has its version in the name).
for %%f in ("%~2\\*.exe") do if /I not "%%~nxf"=="%~3" del "%%f" >nul 2>nul
cd /d "%~2"
start "" "%~2\\%~3"
cd /d "%TEMP%"
rmdir /s /q "%~4" >nul 2>nul
del "%~f0" >nul 2>nul
"""


@dataclass
class PreparedUpdate:
    """解压 + 脚本都准备好了，就等程序退出后启动它。"""

    script: Path
    source: Path          # 新版本所在目录（robocopy 的源）
    target: Path          # 程序目录（要被覆盖的那个）
    exe_name: str         # 新版本的 exe 文件名
    tmp_dir: Path         # 临时目录（脚本最后会删掉）
    version: str = ""

    def launch(self) -> None:
        launch_updater(self)


def prepare_update(package_zip: Path, version: str,
                   target_dir: Optional[Path] = None) -> PreparedUpdate:
    """解压新版本并写好更新脚本，返回 PreparedUpdate（调用方随后退出程序并启动它）。

    目录安排（都在系统临时目录里）：
        %TEMP%\\ddo_update_xxx\\app\\    解压出来的新版本（robocopy 的源）
        %TEMP%\\ddo_update_xxx.cmd       更新脚本（故意放在解压目录外面，
                                          这样才能把解压目录整个删掉）
    路径不写进脚本，靠启动参数传（见 UPDATER_TEMPLATE 的说明）。
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
    script = tmp.with_suffix(".cmd")          # 放在解压目录**外面**
    script.write_text(UPDATER_TEMPLATE, encoding="ascii")
    return PreparedUpdate(script=script, source=new_exe.parent, target=target,
                          exe_name=new_exe.name, tmp_dir=tmp, version=version)


def launch_updater(prepared: "PreparedUpdate") -> None:
    """脱离当前进程启动更新脚本（关掉程序后由它接管）。

    路径**当参数传**（Unicode 进程参数），不写进 .cmd 文件里 —— 见 UPDATER_TEMPLATE。
    """
    flags = 0
    if os.name == "nt":
        flags = 0x00000008 | 0x00000200      # DETACHED_PROCESS | NEW_PROCESS_GROUP
    subprocess.Popen(["cmd", "/c", str(prepared.script), str(prepared.source),
                      str(prepared.target), prepared.exe_name,
                      str(prepared.tmp_dir)],
                     close_fds=True, creationflags=flags)


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
    """清掉升级时留在临时目录里的脚本和文件。

    更新脚本跑完会尽力删自己，但**正在运行的 .cmd 删不掉**（Windows 会拒绝），
    所以每次程序启动时顺手把 `%TEMP%\\ddo_update_*` 清一遍。
    脚本是先复制完文件、再启动新程序，所以新程序这时删它是安全的。
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
