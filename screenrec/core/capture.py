"""采集后端（DDA→WGC 自动降级）与录制线程：定帧率节拍 + 流式喂给 ffmpeg。"""

import ctypes
import os
import subprocess
import threading
import time

import numpy as np
from PySide6.QtCore import QObject, Signal
from windows_capture import DxgiDuplicationSession, WindowsCapture

from .cursor import CursorCompositor
from .encoder import build_ffmpeg_args, concat_segments

CREATE_NO_WINDOW = 0x08000000
winmm = ctypes.windll.winmm


class CaptureError(Exception):
    pass


class _DDABackend:
    """桌面复制 API：无边框、低开销；拉取式（屏幕无更新返回 None）。"""
    name = "DDA"
    cursor_native = False

    def __init__(self, monitor_index, fps):
        self.session = DxgiDuplicationSession(monitor_index=monitor_index + 1)
        self.timeout_ms = max(2, min(int(1000 / fps), 16))

    def validate(self, expect_wh):
        arr = None
        for _ in range(3):
            f = self.session.acquire_frame(500)
            if f is not None:
                arr = f.to_numpy()
                break
        if arr is None:
            raise CaptureError("DDA 500ms 内未返回任何帧")
        if (arr.shape[1], arr.shape[0]) != expect_wh:
            raise CaptureError(f"DDA 帧尺寸 {arr.shape[1]}x{arr.shape[0]} 与显示器 {expect_wh[0]}x{expect_wh[1]} 不符")
        return arr

    def read(self):
        f = self.session.acquire_frame(self.timeout_ms)
        return None if f is None else f.to_numpy()

    def close(self):
        self.session = None


class _WGCBackend:
    """Windows.Graphics.Capture：兼容性最好；Win10 录制时屏幕有黄色边框（不进入画面）。"""
    name = "WGC"
    cursor_native = True

    def __init__(self, monitor_index, fps, cursor):
        self._event = threading.Event()
        self._slot = None
        self._lock = threading.Lock()
        # minimum_update_interval 仅 Win11 支持，Win10 必须省略
        self.cap = WindowsCapture(monitor_index=monitor_index + 1,
                                  cursor_capture=cursor, draw_border=None)

        @self.cap.event
        def on_frame_arrived(frame, capture_control):
            with self._lock:
                self._slot = frame.frame_buffer
            self._event.set()

        @self.cap.event
        def on_closed():
            self._event.set()

        self.control = self.cap.start_free_threaded()

    def validate(self, expect_wh):
        arr = None
        for _ in range(60):
            arr = self.read()
            if arr is not None:
                break
            time.sleep(0.05)
        if arr is None:
            raise CaptureError("WGC 3s 内未返回任何帧")
        if (arr.shape[1], arr.shape[0]) != expect_wh:
            raise CaptureError(f"WGC 帧尺寸 {arr.shape[1]}x{arr.shape[0]} 与显示器 {expect_wh[0]}x{expect_wh[1]} 不符")
        return arr

    def read(self):
        """非阻塞返回当前最新帧；屏幕静止时返回上次的对象（调用方按对象身份去重复制）。"""
        with self._lock:
            return self._slot

    def close(self):
        try:
            self.control.stop()
        except Exception:
            pass


class RecorderBridge(QObject):
    started = Signal(str)          # 后端名
    stats = Signal(dict)           # elapsed/frames/fps/bytes
    paused = Signal()
    resumed = Signal()
    error = Signal(str)
    finished = Signal(str)         # 最终文件路径，失败为 ""


class RecorderConfig:
    def __init__(self, *, monitor_index, monitor_rect, region, fps, out_dir, base_name,
                 ffmpeg, encoder, quality_level, scale_wh, cursor, ffmpeg_log=True):
        self.monitor_index = monitor_index          # 0 基，EnumDisplayMonitors 顺序
        self.monitor_rect = monitor_rect            # 物理全局 (l,t,r,b)
        self.region = region                        # 物理全局 (l,t,r,b)，已偶数化且夹在显示器内
        self.fps = int(fps)
        self.out_dir = out_dir
        self.base_name = base_name
        self.ffmpeg = ffmpeg
        self.encoder = encoder
        self.quality_level = quality_level
        self.scale_wh = scale_wh                    # None=原生；否则 (w,h)
        self.cursor = cursor
        self.ffmpeg_log = ffmpeg_log

    @property
    def out_size(self):
        w = self.region[2] - self.region[0]
        h = self.region[3] - self.region[1]
        if self.scale_wh:
            return self.scale_wh
        return (w, h)


class RecorderWorker(threading.Thread):
    def __init__(self, cfg: RecorderConfig, bridge: RecorderBridge):
        super().__init__(daemon=True, name="RecorderWorker")
        self.cfg = cfg
        self.bridge = bridge
        self._stop_flag = threading.Event()
        self._pause = threading.Event()
        self._resume = threading.Event()
        self._busy = threading.Lock()

    def stop(self):
        self._stop_flag.set()
        self._resume.set()

    def pause(self):
        with self._busy:
            if not self._pause.is_set():
                self._pause.set()
                self._resume.clear()

    def resume(self):
        with self._busy:
            if self._pause.is_set():
                self._pause.clear()
                self._resume.set()

    def run(self):
        winmm.timeBeginPeriod(1)
        try:
            self._run()
        finally:
            winmm.timeEndPeriod(1)

    # ------------------------------------------------------------------
    def _run(self):
        cfg = self.cfg
        backend, err1 = None, None
        try:
            backend = _DDABackend(cfg.monitor_index, cfg.fps)
            backend.validate((cfg.monitor_rect[2] - cfg.monitor_rect[0],
                              cfg.monitor_rect[3] - cfg.monitor_rect[1]))
        except Exception as e:
            err1 = f"{type(e).__name__}: {e}"
            backend = None
        if backend is None:
            try:
                backend = _WGCBackend(cfg.monitor_index, cfg.fps, cfg.cursor)
                backend.validate((cfg.monitor_rect[2] - cfg.monitor_rect[0],
                                  cfg.monitor_rect[3] - cfg.monitor_rect[1]))
            except Exception as e:
                self.bridge.error.emit(
                    f"屏幕采集初始化失败。\n桌面复制(DDA)：{err1}\n图形捕获(WGC)：{e}")
                return
        self.bridge.started.emit(backend.name)

        compositor = None
        if cfg.cursor and not backend.cursor_native:
            compositor = CursorCompositor()

        rl, rt, rr, rb = cfg.region
        ml, mt = cfg.monitor_rect[0], cfg.monitor_rect[1]
        slice_t, slice_b = rt - mt, rb - mt
        slice_l, slice_r = rl - ml, rr - ml
        origin = (rl, rt)  # 光标合成用：区域左上角的全局物理坐标

        segs = []          # [(proc, path, log)]
        proc = log = seg_path = None
        frames = 0
        start_t = None
        paused_at = None
        paused_total = 0.0
        bytes_written = 0
        last_stats = 0.0
        prev_stat_t = 0.0
        prev_frames = 0
        last_frame = None   # 后端返回的原始帧对象（用于身份比较，避免静止画面重复拷贝）
        last_buf = None     # 已就绪的连续 BGRA 缓冲
        error_msg = None
        profile = os.environ.get("SCREENREC_PROFILE") == "1"
        prof = {"read": 0.0, "copy": 0.0, "write": 0.0, "sleep": 0.0}

        try:
            interval = 1.0 / cfg.fps
            next_t = time.perf_counter()
            while not self._stop_flag.is_set():
                if self._pause.is_set() and proc is not None:
                    self._close_segment(proc, seg_path, log, segs)
                    proc = log = seg_path = None
                    paused_at = time.perf_counter()
                    self.bridge.paused.emit()
                    self._resume.wait()
                    if self._stop_flag.is_set():
                        break
                    paused_total += time.perf_counter() - paused_at
                    self.bridge.resumed.emit()

                if proc is None:
                    seg_path = self._seg_path(len(segs))
                    proc, log = self._open_segment(seg_path)
                    next_t = time.perf_counter()  # 每段从当下重新起拍

                t_p = time.perf_counter()
                frame = backend.read()
                if frame is None:
                    frame = last_frame
                else:
                    last_frame = frame
                if frame is not None:
                    if frame is last_frame and last_buf is not None and compositor is None:
                        buf = last_buf
                    else:
                        buf = np.ascontiguousarray(frame[slice_t:slice_b, slice_l:slice_r])
                        if compositor is not None:
                            buf = compositor.blend(buf, origin)
                        last_buf = buf
                    prof["read"] += time.perf_counter() - t_p
                    t_p = time.perf_counter()
                    try:
                        proc.stdin.write(buf.data)
                        bytes_written += buf.nbytes
                        frames += 1
                    except (BrokenPipeError, OSError) as e:
                        error_msg = "编码器进程中断：\n" + self._stderr_tail(seg_path)
                        break
                    prof["write"] += time.perf_counter() - t_p
                    if start_t is None:
                        # 节拍与计时对齐第一帧，消除采集/编码器启动爬坡造成的亏帧
                        start_t = time.perf_counter()
                        next_t = start_t
                        prev_stat_t, prev_frames = start_t, 0

                t_p = time.perf_counter()
                next_t += interval
                now = time.perf_counter()
                if now < next_t:
                    self._sleep_until(next_t)
                elif now - next_t > interval:
                    next_t = now  # 持续落后时重置节拍，避免突发补帧
                prof["sleep"] += time.perf_counter() - t_p

                now = time.perf_counter()
                if now - last_stats >= 0.5 and start_t is not None:
                    last_stats = now
                    elapsed = now - start_t - paused_total
                    inst = (frames - prev_frames) / max(1e-6, now - prev_stat_t)
                    prev_stat_t, prev_frames = now, frames
                    self.bridge.stats.emit({
                        "elapsed": max(0.0, elapsed), "frames": frames,
                        "fps": inst, "bytes": bytes_written})
        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
        finally:
            if profile:
                total = sum(prof.values()) or 1
                print("[profile] " + "  ".join(f"{k}={v:.1f}s ({v / total * 100:.0f}%)"
                                               for k, v in prof.items()))
            if proc is not None:
                self._close_segment(proc, seg_path, log, segs)
            try:
                backend.close()
            except Exception:
                pass

            final = ""
            try:
                paths = [p for _, p, _ in segs]
                for _, _, lg in segs:
                    try:
                        lg.close()
                    except Exception:
                        pass
                if paths and not error_msg:
                    final = concat_segments(cfg.ffmpeg, paths, self._final_path())
                elif paths:  # 出错也尽量保住已录内容
                    try:
                        final = concat_segments(cfg.ffmpeg, paths, self._final_path())
                    except Exception:
                        final = ""
            except Exception as e:
                error_msg = error_msg or str(e)
            if error_msg:
                self.bridge.error.emit(error_msg)
            self.bridge.finished.emit(final)

    # ------------------------------------------------------------------
    def _sleep_until(self, target):
        while not self._stop_flag.is_set() and not (self._pause.is_set()):
            remain = target - time.perf_counter()
            if remain <= 0:
                return
            time.sleep(min(remain, 0.004))

    def _seg_dir(self):
        d = self.cfg.out_dir
        os.makedirs(d, exist_ok=True)
        return d

    def _seg_path(self, idx):
        return os.path.join(self._seg_dir(), f"{self.cfg.base_name}.part{idx}.tmp.mp4")

    def _final_path(self):
        return os.path.join(self._seg_dir(), f"{self.cfg.base_name}.mp4")

    def _open_segment(self, path):
        args = build_ffmpeg_args(
            ffmpeg=self.cfg.ffmpeg,
            in_w=self.cfg.region[2] - self.cfg.region[0],
            in_h=self.cfg.region[3] - self.cfg.region[1],
            fps=self.cfg.fps,
            out_w=self.cfg.out_size[0], out_h=self.cfg.out_size[1],
            encoder=self.cfg.encoder, quality_level=self.cfg.quality_level,
            out_path=path, faststart=False)
        logf = open(path + ".log", "wb") if self.cfg.ffmpeg_log else subprocess.DEVNULL
        try:
            proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                    stderr=logf, creationflags=CREATE_NO_WINDOW)
        except Exception:
            if logf is not subprocess.DEVNULL:
                logf.close()
            raise
        return proc, logf

    @staticmethod
    def _close_segment(proc, path, logf, segs):
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
        segs.append((proc, path, logf))

    @staticmethod
    def _stderr_tail(seg_path):
        try:
            with open(seg_path + ".log", "rb") as f:
                return f.read()[-800:].decode("utf-8", "replace")
        except Exception:
            return ""
