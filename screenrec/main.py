"""入口：DPI 感知、GUI 启动、CLI 测试模式。"""

import ctypes
import os
import sys
import threading
import time

from .core.encoder import candidates_for, find_ffmpeg, probe_encoders
from .core.monitors import ensure_dpi_awareness, list_monitors


def _run_test(argv):
    """python -m screenrec --test [秒数] [帧率]：无 GUI 跑通采集→编码→产物校验。"""
    from PySide6.QtCore import Qt

    seconds = float(argv[2]) if len(argv) > 2 else 5.0
    fps = int(argv[3]) if len(argv) > 3 else 30

    from .core.capture import RecorderBridge, RecorderConfig, RecorderWorker

    # 无事件循环，直接连接让回调在 worker 线程同步执行
    direct = Qt.DirectConnection if "--direct" in argv else Qt.AutoConnection
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        print("未找到 ffmpeg")
        return 1
    print("ffmpeg:", ffmpeg)
    encs = probe_encoders(ffmpeg, candidates_for("h264"))
    print("可用编码器:", encs)
    if not encs:
        return 1

    monitors = list_monitors()
    m = monitors[0]
    region = (m.left, m.top, m.left + m.width // 2 * 2, m.top + m.height // 2 * 2)
    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "测试输出")
    os.makedirs(out_dir, exist_ok=True)
    cfg = RecorderConfig(
        monitor_index=m.index, monitor_rect=m.rect, region=region, fps=fps,
        out_dir=out_dir, base_name="test_capture", ffmpeg=ffmpeg, encoder=encs[0],
        quality_level=1, scale_wh=None, cursor=True)

    class Bridge(RecorderBridge):
        pass

    signals = {}
    b = Bridge()
    b.started.connect(lambda n: print("后端:", n), direct)
    b.stats.connect(lambda st: print(f"  {st['elapsed']:.1f}s frames={st['frames']} "
                                     f"fps={st['fps']:.1f} {st['bytes'] / 1048576:.1f}MB"), direct)
    result = {}

    def on_err(e):
        print("错误:", e)
        result["err"] = e

    def on_fin(p):
        result["path"] = p

    b.error.connect(on_err, direct)
    b.finished.connect(on_fin, direct)

    worker = RecorderWorker(cfg, b)
    t0 = time.perf_counter()
    worker.start()
    threading.Timer(seconds, worker.stop).start()
    worker.join(timeout=seconds + 120)
    print(f"耗时 {time.perf_counter() - t0:.1f}s")
    path = result.get("path", "")
    if result.get("err"):
        return 1
    if not path:
        print("未生成文件")
        return 1
    print("产物:", path)
    r = os.system(f'ffprobe -v error -show_entries stream=width,height,r_frame_rate,codec_name '
                  f'-show_entries format=duration,size -of default=nw=1 "{path}"')
    return 0


def main():
    ensure_dpi_awareness()
    if "--test" in sys.argv:
        sys.exit(_run_test(sys.argv))

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("ScreenRecorder")
    app.setApplicationDisplayName("屏幕录制工具")

    from .core.hotkeys import HotkeyManager
    from .ui.main_window import MainWindow

    hotkeys = HotkeyManager()
    app.installNativeEventFilter(hotkeys)
    win = MainWindow(hotkeys)
    win.show()
    sys.exit(app.exec())
