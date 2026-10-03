"""提交前自检：仓库里（包括全部 git 历史）有没有混进 API Key / Token。

用法：
    python tools\check_secrets.py

退出码 0 = 干净，1 = 发现可疑内容。建议每次提交前跑一次。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SELF = Path(__file__).resolve()

# 发布签名私钥（和 tools/sign_release.py 用的是同一个位置）。
# 它比 API Key 还敏感：拿到它就能签出"验得过"的假安装包，等于冒你的名发版。
SIGNING_KEY_FILE = Path.home() / ".ddo-release" / "release.key"

# 通用特征，不写死任何真实密钥（否则这个文件本身就成了泄漏点）
PATTERNS = [
    (r"sk-[A-Za-z0-9_\-]{16,}", "疑似大模型 API Key（sk- 开头）"),
    (r"(?i)(api[_-]?key|access[_-]?token|secret[_-]?key|password)"
     r"[\"'\s:=]{1,6}[\"'][A-Za-z0-9_\-+/=]{20,}[\"']", "疑似硬编码的密钥/令牌"),
]
SKIP_SUFFIX = {".png", ".jpg", ".ico", ".zip", ".exe", ".dll", ".pyd", ".onnx", ".pyc"}

# 这次实际扫了多少（打印出来，"干净"才有依据）
STATS = {"files": 0, "history_scans": 0}

# 已知的"测试用假密钥"（见 tests/test_diagnose.py 的脱敏测试）：历史里翻出来属于
# 预期，不该当成泄漏 —— 但必须写明白、且只认这几个精确字符串，不能默默忽略。
KNOWN_PLACEHOLDERS = ("sk-abcdefghijklmnopqrstuvwxyz123456",
                      "tok-1234567890abcdef")
COMMIT_MARK = "@@COMMIT@@"


def _looks_like_placeholder(text: str) -> bool:
    return any(item in (text or "") for item in KNOWN_PLACEHOLDERS)


def _run_checked(args):
    """跑一条 git 命令，返回 (是否成功, 输出或错误信息)。

    为什么不能"失败了就当没扫到"：这个脚本是防密钥泄漏的闸门。仓库属主不对、
    git 没装、命令报错时，如果按"没扫到 = 干净"处理就会**静默放行** ——
    实测踩过：以别的身份跑（git 报 dubious ownership）时它一直说"干净"，
    换成仓库属主再跑才发现测试里有两条形如密钥的假字符串。
    """
    try:
        result = subprocess.run(args, cwd=str(ROOT), capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
    except Exception as exc:                       # noqa: BLE001
        return False, str(exc)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        return False, (detail[0] if detail else "git 退出码 %d" % result.returncode)
    return True, result.stdout or ""


def scan_tracked_files():
    problems = []
    ok, output = _run_checked(["git", "ls-files"])
    if not ok:
        return ["读不到受版本控制的文件清单（git 出错：%s）—— 这次**没扫成**，"
                "不要当成干净" % output]
    scanned = 0
    for name in output.splitlines():
        name = name.strip()
        if not name or name == "tools/check_secrets.py":
            continue
        path = ROOT / name
        if path.suffix.lower() in SKIP_SUFFIX or not path.exists():
            continue
        try:
            if path.stat().st_size > 2_000_000:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        scanned += 1
        for line_number, line in enumerate(text.splitlines(), 1):
            for pattern, label in PATTERNS:
                if re.search(pattern, line) and not _looks_like_placeholder(line):
                    problems.append("%s:%d  %s" % (name, line_number, label))
    STATS["files"] = scanned
    return problems


def scan_history():
    """翻 git 历史：找出"哪次提交新增/删过形如密钥的内容"。

    做法：把全部历史的补丁一次拿出来，在本进程里用**同一套** Python 正则扫。
    以前是把正则丢给 `git log -G`，但 git 用的是 POSIX 正则（`(?i)`、`\-`、`\s`
    都不认）—— 实测直接报 "Invalid range end"，而错误又被吞掉，整段历史扫描
    等于空转（这次复验才发现）。用 Python 扫还顺带能把"测试里故意写的假密钥"
    和真泄漏分开：只报提交号的话，假密钥会一直报警，报到最后没人看，闸门就废了。
    """
    ok, output = _run_checked(["git", "log", "--all", "-p",
                              "--pretty=format:" + COMMIT_MARK + "%h"])
    if not ok:
        return ["翻不了 git 历史（git 出错：%s）—— 这次**没扫成**，不要当成干净"
                % output]
    STATS["history_scans"] = 1
    problems = []
    commit = ""
    for line in output.splitlines():
        if line.startswith(COMMIT_MARK):
            commit = line[len(COMMIT_MARK):].strip()
            continue
        if line[:1] not in "+-":
            continue
        body = line[1:]
        if _looks_like_placeholder(body):
            continue
        for pattern, label in PATTERNS:
            if re.search(pattern, body):
                problems.append("历史提交 %s  %s | %s"
                                % (commit or "?", label, body.strip()[:60]))
    return problems


def _key_forms():
    """本机发布签名私钥的几种写法（hex / base64），用来在仓库里反查有没有泄漏。"""
    import base64

    if not SIGNING_KEY_FILE.exists():
        return []
    try:
        raw = SIGNING_KEY_FILE.read_text(encoding="utf-8").strip()
        try:
            data = bytes.fromhex(raw)
        except ValueError:
            data = base64.b64decode(raw)
    except Exception:
        return []
    if len(data) != 32:
        return []
    return [data.hex(), base64.b64encode(data).decode("ascii")]


def scan_signing_key():
    """发布签名私钥有没有被误提交（没生成过密钥就跳过）。"""
    forms = _key_forms()
    if not forms:
        return []
    problems = []
    ok, output = _run_checked(["git", "ls-files"])
    if not ok:
        return ["读不到受版本控制的文件清单（git 出错：%s）—— 私钥那一项**没扫成**"
                % output]
    for name in output.splitlines():
        name = name.strip()
        path = ROOT / name
        if not name or not path.exists():
            continue
        try:
            if path.stat().st_size > 2_000_000:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if any(form in text for form in forms):
            problems.append("%s  发布签名私钥被提交进仓库了！" % name)
    for form in forms:
        ok, output = _run_checked(["git", "log", "--all", "-S", form, "--oneline"])
        if not ok:
            problems.append("翻不了 git 历史查私钥（git 出错：%s）—— 这一项**没扫成**"
                            % output)
            continue
        for line in output.splitlines():
            if line.strip():
                problems.append("历史提交 %s  发布签名私钥出现在 git 历史里！"
                                % line.strip()[:40])
    return problems


def main() -> int:
    print("=" * 62)
    print("密钥自检：扫描受版本控制的文件、全部 git 历史、以及发布签名私钥")
    print("=" * 62)
    problems = scan_tracked_files() + scan_history() + scan_signing_key()
    if problems:
        print("发现可疑内容（请立刻处理，不要把密钥提交上去）：")
        for item in sorted(set(problems)):
            print("  [!] %s" % item)
        print("\n如果确实误提交了：删掉文件并 commit，然后**重置对应的 Key**（历史里的内容"
              "即使删掉也还能被翻出来）。")
        return 1
    if SIGNING_KEY_FILE.exists():
        print("干净：扫了 %d 个受控文件、%d 轮 git 历史检索，"
              "没有 API Key / Token，发布签名私钥也没有混进去。"
              % (STATS["files"], STATS["history_scans"]))
    else:
        print("干净：扫了 %d 个受控文件、%d 轮 git 历史检索，没有 API Key / Token。"
              % (STATS["files"], STATS["history_scans"]))
        print("　（本机还没有发布签名私钥，跳过那一项；生成私钥后再跑一次会一并检查）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
