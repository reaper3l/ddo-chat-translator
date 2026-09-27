"""DDO 聊天翻译助手 —— 程序入口。

运行：  python main.py
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


def _setup_dpi(config: dict) -> str:
    """让进程感知 DPI，使"框选坐标"和"截图坐标"落在同一个物理像素空间里。

    以前用 DPI Unaware：Windows 会把 Tk 报告的坐标按显示缩放虚拟化
    （125% 缩放下 1000 物理像素只报 800），而截图拿到的是物理像素，
    于是框选的区域和实际采样的区域就会错位。

    现在默认按显示器感知 DPI（per-monitor v2）。如果某些机器上仍有问题，
    把 data/config.json 里的 "dpi_mode" 改成 "legacy" 即可退回旧行为。
    """
    if sys.platform != "win32":
        return "non-windows"

    import ctypes

    if _read_dpi_mode(config) == "legacy":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(0)
            return "legacy(unaware)"
        except Exception:
            return "legacy(failed)"

    # 1) Win10 1703+：per-monitor v2（DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4）
    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return "per-monitor-v2"
    except Exception:
        pass
    # 2) Win8.1+：per-monitor
    try:
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            return "per-monitor"
    except Exception:
        pass
    # 3) Vista+：system aware
    try:
        if ctypes.windll.user32.SetProcessDPIAware():
            return "system"
    except Exception:
        pass
    return "unknown"


def _setup_environment() -> str:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    config = _read_early_config()
    _setup_thread_env(config)
    # DPI 必须在导入/创建 tkinter 窗口之前设好，所以放在最前面
    return _setup_dpi(config)


def _setup_logging() -> None:
    from app import paths

    paths.ensure_dirs()
    logging.basicConfig(
        filename=str(paths.LOG_PATH),
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        encoding="utf-8",
    )


def main() -> int:
    dpi_state = _setup_environment()
    _setup_logging()
    logging.info("程序启动，DPI 模式：%s", dpi_state)
    print("DPI 模式：%s" % dpi_state)
    try:
        from app.ui.main_window import MainWindow

        MainWindow().run()
        return 0
    except Exception:
        logging.exception("程序异常退出")
        raise
    finally:
        logging.info("程序退出")


if __name__ == "__main__":
    raise SystemExit(main())
