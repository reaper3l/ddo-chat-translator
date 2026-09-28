"""给打包出来的 exe 做 Windows 代码签名（Authenticode）。

它和「发布包签名」（tools/sign_release.py，Ed25519）是两件不同的事，配合着用：

* **Authenticode（这个脚本）**：让 Windows 认得"这个 exe 的发布者是谁" ——
  文件属性 → 数字签名 里能看到你的名字，SmartScreen 不再报"未知发布者"，
  也是别人/平台可以独立验证的身份证据。**需要一张买来的代码签名证书。**
* **Ed25519（sign_release.py）**：程序自己校验"这个安装包是我签的"，
  防止自动更新被塞假包。**用你自己生成的发布私钥，不要钱。**

正确顺序（很重要，反了就得重来）：

    exe  →  ① 本脚本签名  →  ② 压缩成 zip  →  ③ sign_release.py 给 zip 签名  →  发布

因为 ① 会改变 exe 的字节，zip 的 sha256 跟着变，所以必须先签 exe 再打包再签 zip。

用法
----
    python tools\\sign_exe.py check                          # 这台机器能不能签？有哪些证书？
    python tools\\sign_exe.py sign <exe>                     # 用证书存储里最合适的那张（/a）
    python tools\\sign_exe.py sign <exe> --thumbprint <指纹>  # 指定证书（U 盾/硬件令牌常这样）
    python tools\\sign_exe.py sign <exe> --pfx 证书.pfx       # 用 pfx 文件
    python tools\\sign_exe.py verify <exe>                    # 看签名状态和签名者

证书密码不写进命令行（会进历史/进程列表）：用环境变量 `DDO_PFX_PASSWORD` 传。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

DEFAULT_TIMESTAMP = "http://timestamp.digicert.com"
PFX_PASSWORD_ENV = "DDO_PFX_PASSWORD"


def _utf8_stdout() -> None:
    """中文 Windows 控制台是 GBK，写不出的字符会让脚本崩在打印上。"""
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass


def find_signtool() -> Optional[str]:
    """找 signtool.exe：先看 PATH，再翻 Windows SDK 的安装目录（取版本最高的）。"""
    found = shutil.which("signtool")
    if found:
        return found
    roots = [os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")]
    candidates: List[Path] = []
    for root in roots:
        if not root:
            continue
        kits = Path(root) / "Windows Kits"
        for name in ("10", "11"):
            bin_dir = kits / name / "bin"
            if not bin_dir.is_dir():
                continue
            for version in sorted(bin_dir.iterdir(), reverse=True):
                for arch in ("x64", "x86"):
                    exe = version / arch / "signtool.exe"
                    if exe.exists():
                        candidates.append(exe)
    return str(candidates[0]) if candidates else None


def _powershell(script: str) -> str:
    try:
        done = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                              capture_output=True, text=True, timeout=60,
                              encoding="utf-8", errors="replace")
        return (done.stdout or "") + (done.stderr or "")
    except Exception as exc:
        return "调用 PowerShell 失败：%s" % exc


def list_certs() -> str:
    """列出"本机用户证书存储"里能用来做代码签名的证书。"""
    # 优先用 certutil（Windows 自带，不依赖 PowerShell 的证书模块）；
    # 有些精简/受限的 PowerShell 里没有 Cert: 盘和 Security 模块。
    try:
        done = subprocess.run(["certutil", "-user", "-store", "My"],
                              capture_output=True, text=True, timeout=30,
                              encoding="utf-8", errors="replace")
        text = (done.stdout or "").strip()
        if text and "CertUtil" not in text:      # 报错时（受限终端）走下面那套
            return text
    except Exception:
        pass
    return _powershell(
        "$ErrorActionPreference='Stop'; try { Get-ChildItem Cert:\\CurrentUser\\My | "
        "Where-Object { "
        "$_.EnhancedKeyUsageList.ObjectId -contains '1.3.6.1.5.5.7.3.3' } | "
        "ForEach-Object { \"$($_.Thumbprint)  |  $($_.Subject)  |  到期 $($_.NotAfter)\" } } "
        "catch { '读取证书存储失败（可以改用 certutil -user -store My 看）：' + "
        "$_.Exception.Message }")


def describe(path: Path) -> str:
    """看一个文件的 Authenticode 签名状态（有 signtool 用它，没有就退回 PowerShell）。"""
    tool = find_signtool()
    if tool:
        try:
            done = subprocess.run([tool, "verify", "/pa", "/v", str(path)],
                                  capture_output=True, text=True, timeout=60,
                                  encoding="utf-8", errors="replace")
            text = ((done.stdout or "") + (done.stderr or "")).strip()
            if text:
                return text
        except Exception:
            pass
    return _powershell(
        "$ErrorActionPreference='Stop'; try { $s = Get-AuthenticodeSignature "
        "-LiteralPath '%s'; "
        "\"状态: $($s.Status)\"; "
        "\"签名者: $($s.SignerCertificate.Subject)\"; "
        "\"颁发者: $($s.SignerCertificate.Issuer)\"; "
        "\"有效期至: $($s.SignerCertificate.NotAfter)\"; "
        "\"时间戳: $($s.TimeStamperCertificate.Subject)\" } "
        "catch { '读取签名失败: ' + $_.Exception.Message }" % path)


def cmd_check(args) -> int:
    tool = find_signtool()
    print("=" * 66)
    print("代码签名环境检查")
    print("=" * 66)
    if tool:
        print("[ok]   signtool：%s" % tool)
    else:
        print("[!]    没找到 signtool.exe —— 需要装 Windows SDK 的「签名工具」组件：")
        print("       下载 https://developer.microsoft.com/windows/downloads/windows-sdk/")
        print("       安装时勾选 Windows SDK Signing Tools for Desktop Apps")
    print()
    print("可用于代码签名的证书（当前用户证书存储）：")
    certs = list_certs().strip()
    broken = ("CertUtil", "失败", "Exception", "Cannot find", "无法", "不存在")
    if not certs or any(mark in certs for mark in broken):
        print("   （没读出来：可能还没装证书，也可能这个终端读不到证书存储。")
        print("     证书装好后重跑本命令，或直接跑 sign 看结果）")
    else:
        print("   " + certs.replace("\n", "\n   "))
    print()
    print("没有证书也能发布：Ed25519 发布包签名（tools\\sign_release.py）已经能防"
          "「自动更新被塞假包」，")
    print("只是用户下载时 Windows 仍会显示「未知发布者」。")
    return 0


def cmd_sign(args) -> int:
    tool = find_signtool()
    if not tool:
        raise SystemExit("没找到 signtool.exe：先装 Windows SDK 的签名工具组件"
                         "（详见 python tools\\sign_exe.py check）")
    target = Path(args.file)
    if not target.exists():
        raise SystemExit("找不到文件：%s" % target)
    command = [tool, "sign", "/fd", args.digest, "/td", args.digest,
               "/tr", args.timestamp, "/v"]
    if args.pfx:
        command += ["/f", args.pfx]
        password = os.environ.get(PFX_PASSWORD_ENV)
        if password:
            command += ["/p", password]
        else:
            print("提示：没设环境变量 %s，如果证书有密码会弹窗让你输"
                  "（也可以先 set %s=密码 再跑）" % (PFX_PASSWORD_ENV, PFX_PASSWORD_ENV))
    elif args.thumbprint:
        command += ["/sha1", args.thumbprint]
    else:
        command += ["/a"]                      # 自动挑最合适的代码签名证书
    command += ["/d", args.description, "/du", args.url, str(target)]
    print("执行：" + " ".join(command[:2] + ["…"] + command[-4:]))
    done = subprocess.run(command)
    if done.returncode != 0:
        raise SystemExit("signtool 返回 %d（证书不对/没插 U 盾/时间戳服务器不通？）"
                         % done.returncode)
    print()
    print(describe(target))
    print("别忘了：签完 exe 之后再压缩成 zip，然后用 tools\\sign_release.py 给 zip 签名。")
    return 0


def cmd_verify(args) -> int:
    target = Path(args.file)
    if not target.exists():
        raise SystemExit("找不到文件：%s" % target)
    print(describe(target))
    return 0


def main() -> int:
    _utf8_stdout()
    parser = argparse.ArgumentParser(description="给 exe 做 Windows 代码签名（Authenticode）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="看这台机器能不能签、有哪些证书")
    p_check.set_defaults(func=cmd_check)

    p_sign = sub.add_parser("sign", help="给 exe 签名（默认自动挑证书）")
    p_sign.add_argument("file", help="要签名的 exe")
    p_sign.add_argument("--pfx", help="证书文件（.pfx/.p12）；密码用环境变量 %s"
                                     % PFX_PASSWORD_ENV)
    p_sign.add_argument("--thumbprint", help="证书指纹（证书装在证书存储里时用它）")
    p_sign.add_argument("--timestamp", default=DEFAULT_TIMESTAMP,
                        help="时间戳服务器（默认 %s）" % DEFAULT_TIMESTAMP)
    p_sign.add_argument("--digest", default="sha256", help="摘要算法（默认 sha256）")
    p_sign.add_argument("--description", default="DDO 翻译助手",
                        help="显示在签名里的产品名")
    p_sign.add_argument("--url", default="https://gitee.com/git55236/ddo-chat-translator",
                        help="显示在签名里的项目地址")
    p_sign.set_defaults(func=cmd_sign)

    p_verify = sub.add_parser("verify", help="看某个文件的签名状态")
    p_verify.add_argument("file")
    p_verify.set_defaults(func=cmd_verify)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
