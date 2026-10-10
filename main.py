"""DDO 聊天翻译助手 —— 程序入口。

运行：  python main.py

也支持几个"不开界面"的命令行用法（给脚本 / 插件 / 排查问题用）：

    python main.py --serve                 起本地翻译平台（只监听 127.0.0.1）
    python main.py --serve --port 8765     指定端口
    python main.py --translate "omw"       翻一条，直接打印译文
    python main.py --translate "omw" --json        机器可读的完整结果
    python main.py --translate "马上到" --direction zh2en
"""
from __future__ import annotations

import logging
import os
import sys
import json


def _read_early_config() -> dict:
    """在导入 tkinter / onnxruntime 之前先把配置读出来（DPI 和线程数都必须早设置）。"""
    try:
        from app import paths

        data = json.loads(paths.CONFIG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _read_dpi_mode(config: dict) -> str:
    mode = str(config.get("dpi_mode", "auto")).lower()
    return mode if mode in ("auto", "legacy") else "auto"


def _setup_thread_env(config: dict) -> None:
    """限制数学库/推理库的线程与自旋。

    onnxruntime 默认会用满所有核心、并且线程会忙等（spin），监听时就会和游戏抢 CPU，
    表现就是游戏变卡。这里把线程数压到配置值、并把等待策略改成被动睡眠。
    必须在导入 onnxruntime 之前设置。
    """
    try:
        threads = str(max(1, min(8, int(config.get("ocr_threads", 2) or 2))))
    except Exception:
        threads = "2"
    for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(key, threads)
    os.environ.setdefault("OMP_WAIT_POLICY", "PASSIVE")


def _lower_process_priority(config: dict) -> str:
    """把程序的**后台线程**降到"低于正常"优先级，但**界面线程提回正常**。

    监听是后台任务，慢一点没关系，但游戏被抢 CPU 就会卡；降优先级后游戏永远先拿到 CPU。
    可**界面线程不能跟着一起降** —— 游戏、串流主机、编码器把机器占满时，界面线程抢不到
    时间片，用户看到的正是"鼠标拖窗口不跟手"（甚至点按钮都要等）。
    Win32 的线程优先级是相对进程类的：进程类设成 below-normal 后，把主线程设成
    THREAD_PRIORITY_HIGHEST（+2）正好等于"正常"这一档的绝对优先级 ——
    于是：后台的抓屏/OCR 继续让着游戏，界面该跟手还是跟手。
    """
    if sys.platform != "win32":
        return "non-windows"
    if not config.get("low_priority", True):
        return "disabled"
    try:
        import ctypes

        below_normal = 0x00004000
        thread_highest = 2
        kernel32 = ctypes.windll.kernel32
        # 必须声明返回类型：GetCurrentProcess() 返回的是伪句柄（64 位全 1），
        # 不声明会被截成 32 位，导致后面 SetPriorityClass 拿到错误句柄而失败。
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        kernel32.SetPriorityClass.restype = ctypes.c_int
        kernel32.GetCurrentThread.restype = ctypes.c_void_p
        kernel32.SetThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
        kernel32.SetThreadPriority.restype = ctypes.c_int
        handle = kernel32.GetCurrentProcess()
        if not kernel32.SetPriorityClass(handle, below_normal):
            return "failed"
        ui_ok = bool(kernel32.SetThreadPriority(
            kernel32.GetCurrentThread(), thread_highest))
        return "below-normal（界面线程保持正常）" if ui_ok else "below-normal"
    except Exception as exc:
        return "failed: %s" % exc


def _setup_dpi(config: dict) -> str:
    """让进程感知 DPI，使"框选坐标"和"截图坐标"落在同一个物理像素空间里。

    以前用 DPI Unaware：Windows 会把 Tk 报告的坐标按显示缩放虚拟化
    （125% 缩放下 1000 物理像素只报 800），而截图拿到的是物理像素，
    于是框选的区域和实际采样的区域就会错位。

    现在默认按显示器感知 DPI（per-monitor v2）。如果某些机器上仍有问题，
    把 data/config.json 里的 "dpi_mode" 改成 "legacy" 即可退回旧行为。

    实现放在 app/dpi.py，诊断工具（tools\\trace.py 等）也会调用同一份代码。
    """
    from app import dpi

    return dpi.enable(_read_dpi_mode(config))


def _setup_environment() -> str:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    config = _read_early_config()
    _setup_thread_env(config)
    # 让线程之间更快地让出 GIL：默认 5ms 一次，后台抓屏/OCR 一旦在跑，
    # 界面线程最坏要等满这 5ms 才能拿到锁 —— 拖动时就是一顿一顿的。
    # 调到 1ms 后界面跟手得多，代价只是线程切换略多一点点（实测监听时 CPU 无变化）。
    try:
        sys.setswitchinterval(0.001)
    except Exception:
        pass
    priority = _lower_process_priority(config)
    # DPI 必须在导入/创建 tkinter 窗口之前设好，所以放在最前面
    return "%s / 优先级 %s" % (_setup_dpi(config), priority)


def _setup_logging() -> None:
    from app import paths

    paths.ensure_dirs()
    logging.basicConfig(
        filename=str(paths.LOG_PATH),
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        encoding="utf-8",
    )


def _offer_crash_report() -> None:
    """程序异常退出时，顺手把反馈包生成好，并告诉用户文件在哪。

    崩溃时用户最需要的就是"把现场交出去" —— 这里自动打包（配置文件里的 API Key 会被
    自动隐藏），然后弹一个框告诉路径，用户照着发反馈就行。
    """
    try:
        from app import diagnose, paths
        from app.config import load_config

        report = diagnose.build_report(
            problem="程序异常退出（这份报告是崩溃时自动生成的）",
            config=load_config(), records=[], last_lines=[])
        bundle = diagnose.write_bundle(paths.DATA_DIR / "反馈", report,
                                       stamp=__import__("datetime").datetime.now()
                                       .strftime("崩溃_%Y%m%d_%H%M%S"))
        message = ("程序遇到了一个错误，已经自动把现场打包好：\n%s\n\n"
                   "可以在「设置 → 关于 → 反馈问题」里把压缩包发到项目反馈页；"
                   "报告里的 API Key 已经自动隐藏。" % bundle)
    except Exception as exc:                     # 打包失败也不能挡住原来的报错
        message = "程序遇到了一个错误（自动打包也失败了：%s）" % exc
    try:
        import tkinter.messagebox as messagebox

        messagebox.showwarning("程序遇到错误", message)
    except Exception:
        print(message)


def _arg_value(argv, name: str, default=None):
    """取 `--name value` 或 `--name=value`。给命令行用法共用。"""
    for index, item in enumerate(argv):
        if item == name and index + 1 < len(argv):
            return argv[index + 1]
        if isinstance(item, str) and item.startswith(name + "="):
            return item.split("=", 1)[1]
    return default


def _run_serve(argv) -> int:
    """不开界面，只跑本地翻译平台（给脚本 / 常驻服务用）。"""
    import time

    from app import paths
    from app.config import load_config
    from app.platform import Platform

    paths.ensure_dirs()
    _setup_logging()
    config = load_config()
    port = _arg_value(argv, "--port")
    if port:
        try:
            config["platform_port"] = int(port)
        except ValueError:
            print("端口要是数字：%s" % port)
            return 2
    platform = Platform(config)
    result = platform.start(config.get("platform_port"))
    if not result.get("ok"):
        print("本地平台启动失败：%s" % result.get("error"))
        return 2
    if result.get("note"):
        print(result["note"])
    print("本地翻译平台已启动：%s" % platform.url())
    print("　令牌：%s" % platform.tokens.path)
    print("　插件目录：%s" % platform.plugins.root)
    print("　接口说明：docs/平台接口.md")
    print("按 Ctrl+C 停止。")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n正在停止…")
    finally:
        platform.stop()
    return 0


def _run_translate(argv) -> int:
    """命令行翻一条（和界面共用同一份术语表 / 记忆库 / 缓存）。"""
    text = _arg_value(argv, "--translate")
    if not text:
        print('用法：python main.py --translate "文本" [--direction en2zh|zh2en|auto] [--json]')
        return 2
    direction = str(_arg_value(argv, "--direction", "auto") or "auto")
    as_json = "--json" in argv

    from app import paths
    from app.config import load_config
    from app.service import TranslatorService

    paths.ensure_dirs()
    service = TranslatorService(load_config())
    result = service.translate(text, direction)
    service.flush_cache()
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result.get("zh") or "")
        if not result.get("ok"):
            print("（失败：%s）" % result.get("error"), file=sys.stderr)
    return 0 if result.get("ok") else 1


def main() -> int:
    # 自动升级：由旧版拉起来的"新版本 exe"走这条路 —— 只做文件替换，不开界面、
    # 不设 DPI/优先级，所以必须放在最前面拦下来。
    if "--apply-update" in sys.argv:
        from app import update as update_module

        return update_module.apply_update_from_argv(sys.argv)
    # 命令行用法：不装界面、不改优先级，直接干活
    if "--serve" in sys.argv:
        return _run_serve(sys.argv)
    if "--translate" in sys.argv:
        return _run_translate(sys.argv)
    dpi_state = _setup_environment()
    _setup_logging()
    try:
        from app import __version__, source_stamp

        stamp = "v%s（%s）" % (__version__, source_stamp())
    except Exception:                              # noqa: BLE001
        stamp = "未知"
    # 反馈问题时先说这一行就够了：能立刻确认"跑的是哪一份代码"
    logging.info("程序启动，版本 %s，DPI 模式：%s", stamp, dpi_state)
    print("版本：%s\nDPI 模式：%s" % (stamp, dpi_state))
    # 自检开关：不打开主界面，把每一环走一遍并写报告（打包版出问题时用它定位）
    if "--self-check" in sys.argv or "--selfcheck" in sys.argv:
        from app.selfcheck import run as run_selfcheck

        return run_selfcheck()
    # 顺手清理上一次自动升级留在临时目录里的脚本/解压文件
    try:
        from app import update as update_module

        update_module.cleanup_leftovers()
    except Exception:
        pass
    try:
        from app.ui.main_window import MainWindow

        MainWindow().run()
        return 0
    except Exception:
        logging.exception("程序异常退出")
        _offer_crash_report()
        raise
    finally:
        logging.info("程序退出")


if __name__ == "__main__":
    raise SystemExit(main())
