"""主窗口：参数配置、托盘、快捷键与录制状态机。"""

import os
import sys
import threading
import time

from PySide6.QtCore import QRect, QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QKeySequence, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout,
                               QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QMainWindow, QMenu, QMessageBox, QPushButton, QKeySequenceEdit,
                               QSpinBox, QSystemTrayIcon, QVBoxLayout, QWidget)

from ..core.capture import RecorderBridge, RecorderConfig, RecorderWorker
from ..core.encoder import (CODECS, QUALITY_LEVELS, candidates_for, find_ffmpeg,
                            probe_encoders)
from ..core.hotkeys import HotkeyManager
from ..core.monitors import list_monitors
from .region_overlay import RegionSelector, match_screens

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if getattr(sys, "frozen", False):  # PyInstaller 单文件：settings.ini 放在 exe 旁边
    APP_DIR = os.path.dirname(sys.executable)


def _make_icon(color):
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor("#2B2B33"))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(4, 4, 56, 56, 14, 14)
    p.setBrush(QColor(color))
    p.drawEllipse(20, 20, 24, 24)
    p.end()
    return QIcon(pm)


class _Countdown(QLabel):
    def __init__(self, screen, seconds, on_done):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.Tool | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TransparentForMouseInputs)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setScreen(screen)
        f = QFont(self.font())
        f.setPointSize(72)
        f.setBold(True)
        self.setFont(f)
        self.setStyleSheet("color:#fff;background:rgba(0,0,0,150);border-radius:24px;")
        self.setAlignment(Qt.AlignCenter)
        self.resize(200, 200)
        g = screen.geometry()
        self.move(g.center() - QRect(0, 0, 200, 200).center())
        self._left = seconds
        self._on_done = on_done
        self.setText(str(self._left))
        self.show()
        self.raise_()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

    def _tick(self):
        self._left -= 1
        if self._left < 0:
            self._timer.stop()
            self.close()
            self._on_done()
        else:
            self.setText(str(self._left))
            self.raise_()

    def abort(self):
        self._timer.stop()
        self.close()


class MainWindow(QMainWindow):
    def __init__(self, hotkeys: HotkeyManager):
        super().__init__()
        self.hotkeys = hotkeys
        # 隐藏原生窗口承载全局快捷键（WM_HOTKEY 需要有窗口接收）
        self._hk_host = QWidget()
        self.hotkeys.attach_window(int(self._hk_host.winId()))
        self.monitors = list_monitors()
        self.worker = None
        self.state = "idle"          # idle / countdown / recording / paused / saving
        self.selector = None
        self.countdown = None
        self._pending_cfg = None
        self._backend_name = ""
        self._error_msg = ""
        self._encoder_pool = {}
        self._probe_done = False
        self._hk_start_id = None
        self._hk_pause_id = None
        self.settings = QSettings(os.path.join(APP_DIR, "settings.ini"), QSettings.IniFormat)

        self.setWindowTitle("屏幕录制工具")
        self.setWindowIcon(_make_icon("#E5484D"))
        self._build_ui()
        self._build_tray()
        self._load_settings()
        self._apply_hotkeys()
        self._start_encoder_probe()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(10)

        gb_area = QGroupBox("录制区域（物理像素）")
        form = QFormLayout(gb_area)
        row = QHBoxLayout()
        self.cmb_monitor = QComboBox()
        for m in self.monitors:
            self.cmb_monitor.addItem(m.label())
        self.cmb_monitor.currentIndexChanged.connect(self._on_monitor_changed)
        btn_pick = QPushButton("框选区域…")
        btn_pick.clicked.connect(self._pick_region)
        btn_full = QPushButton("整个屏幕")
        btn_full.clicked.connect(self._use_full_monitor)
        row.addWidget(self.cmb_monitor, 1)
        row.addWidget(btn_pick)
        row.addWidget(btn_full)
        form.addRow(row)
        grid = QGridLayout()
        self.sp_x = QSpinBox(); self.sp_y = QSpinBox()
        self.sp_w = QSpinBox(); self.sp_h = QSpinBox()
        self.sp_x.setRange(-16384, 16384)
        self.sp_y.setRange(-16384, 16384)
        self.sp_w.setRange(8, 16384)
        self.sp_h.setRange(8, 16384)
        for col, (sp, name) in enumerate(((self.sp_x, "X"), (self.sp_y, "Y"),
                                          (self.sp_w, "宽"), (self.sp_h, "高"))):
            sp.setSuffix(" px")
            grid.addWidget(QLabel(name), 0, col)
            grid.addWidget(sp, 1, col)
        form.addRow(grid)
        root.addWidget(gb_area)

        gb_param = QGroupBox("视频参数")
        form = QFormLayout(gb_param)
        self.cmb_fps = QComboBox()
        self.cmb_fps.setEditable(True)
        self.cmb_fps.addItems(["60", "30", "15", "24", "25", "48", "120", "144"])
        self.cmb_fps.setCurrentText("60")
        form.addRow("帧率", self.cmb_fps)

        res_row = QHBoxLayout()
        self.chk_native = QCheckBox("原生尺寸")
        self.chk_native.setChecked(True)
        self.chk_native.toggled.connect(self._on_native_toggled)
        self.sp_ow = QSpinBox(); self.sp_oh = QSpinBox()
        self.sp_ow.setRange(64, 7680); self.sp_oh.setRange(64, 4320)
        self.sp_ow.setSuffix(" px"); self.sp_oh.setSuffix(" px")
        res_row.addWidget(self.chk_native)
        res_row.addWidget(QLabel("宽"))
        res_row.addWidget(self.sp_ow)
        res_row.addWidget(QLabel("高"))
        res_row.addWidget(self.sp_oh)
        res_row.addStretch(1)
        form.addRow("输出分辨率", res_row)

        self.cmb_codec = QComboBox()
        for _, label in CODECS:
            self.cmb_codec.addItem(label)
        self.cmb_codec.currentIndexChanged.connect(self._on_codec_changed)
        self.cmb_encoder = QComboBox()
        self.cmb_quality = QComboBox()
        self.cmb_quality.addItems(QUALITY_LEVELS)
        self.cmb_quality.setCurrentIndex(1)
        form.addRow("编码格式", self.cmb_codec)
        form.addRow("编码器", self.cmb_encoder)
        form.addRow("画质", self.cmb_quality)
        root.addWidget(gb_param)

        gb_out = QGroupBox("输出与行为")
        form = QFormLayout(gb_out)
        dir_row = QHBoxLayout()
        self.ed_outdir = QLineEdit()
        btn_dir = QPushButton("浏览…")
        btn_dir.clicked.connect(self._browse_dir)
        dir_row.addWidget(self.ed_outdir, 1)
        dir_row.addWidget(btn_dir)
        form.addRow("输出目录", dir_row)
        self.ed_prefix = QLineEdit("录屏")
        form.addRow("文件名前缀", self.ed_prefix)

        opt_row = QHBoxLayout()
        self.chk_hide = QCheckBox("录制时隐藏主窗口")
        self.chk_hide.setChecked(True)
        self.chk_cursor = QCheckBox("捕获鼠标指针")
        self.chk_cursor.setChecked(True)
        self.chk_countdown = QCheckBox("开始前倒计时 3 秒")
        self.chk_countdown.setChecked(True)
        for w in (self.chk_hide, self.chk_cursor, self.chk_countdown):
            opt_row.addWidget(w)
        form.addRow(opt_row)
        root.addWidget(gb_out)

        gb_hk = QGroupBox("全局快捷键")
        form = QFormLayout(gb_hk)
        self.hk_start = QKeySequenceEdit(QKeySequence("F9"))
        self.hk_pause = QKeySequenceEdit(QKeySequence("F10"))
        self.hk_start.keySequenceChanged.connect(self._on_hotkey_edited)
        self.hk_pause.keySequenceChanged.connect(self._on_hotkey_edited)
        form.addRow("开始 / 停止", self.hk_start)
        form.addRow("暂停 / 继续", self.hk_pause)
        root.addWidget(gb_hk)

        self.lbl_status = QLabel("就绪")
        self.lbl_status.setStyleSheet("color:#666;")
        self.lbl_status.setWordWrap(True)
        root.addWidget(self.lbl_status)

        btn_row = QHBoxLayout()
        self.btn_main = QPushButton("● 开始录制")
        self.btn_main.setFixedHeight(42)
        self.btn_main.setStyleSheet(
            "QPushButton{background:#E5484D;color:#fff;font-size:16px;font-weight:bold;"
            "border:none;border-radius:8px;padding:0 28px;}"
            "QPushButton:hover{background:#F2555A;}"
            "QPushButton:disabled{background:#888;}")
        self.btn_main.clicked.connect(self._on_main_button)
        self.btn_pause = QPushButton("暂停")
        self.btn_pause.setFixedHeight(42)
        self.btn_pause.setEnabled(False)
        self.btn_pause.clicked.connect(self._on_pause_button)
        btn_row.addWidget(self.btn_main)
        btn_row.addWidget(self.btn_pause)
        root.addLayout(btn_row)

        self._config_widgets = [
            self.cmb_monitor, self.sp_x, self.sp_y, self.sp_w, self.sp_h,
            self.cmb_fps, self.chk_native, self.sp_ow, self.sp_oh,
            self.cmb_codec, self.cmb_encoder, self.cmb_quality,
            self.ed_outdir, self.ed_prefix, self.chk_hide, self.chk_cursor,
            self.chk_countdown]
        self.setMinimumWidth(540)

        if find_ffmpeg() is None:
            self.lbl_status.setText("未找到 ffmpeg：请安装 ffmpeg 或将其放到 bin/ffmpeg.exe")
            self.btn_main.setEnabled(False)

    def _build_tray(self):
        self.tray = QSystemTrayIcon(_make_icon("#666"), self)
        menu = QMenu()
        self.tray.setContextMenu(menu)
        act_show = QAction("显示主窗口", self)
        act_show.triggered.connect(self._show_window)
        self.act_toggle = QAction("开始录制 (F9)", self)
        self.act_toggle.triggered.connect(self._on_main_button)
        self.act_pause = QAction("暂停 (F10)", self)
        self.act_pause.setEnabled(False)
        self.act_pause.triggered.connect(self._on_pause_button)
        act_quit = QAction("退出", self)
        act_quit.triggered.connect(self._quit)
        for a in (act_show, self.act_toggle, self.act_pause):
            menu.addAction(a)
        menu.addSeparator()
        menu.addAction(act_quit)
        self.tray.setContextMenu(menu)
        self.tray.setToolTip("屏幕录制工具")
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    # ------------------------------------------------------------------ 编码器探测
    def _start_encoder_probe(self):
        def work():
            ffmpeg = find_ffmpeg()
            pool = {}
            if ffmpeg:
                for codec, _ in CODECS:
                    pool[codec] = probe_encoders(ffmpeg, candidates_for(codec))
            self._encoder_pool = pool
            self._probe_done = True
        threading.Thread(target=work, daemon=True).start()
        QTimer.singleShot(300, self._poll_probe)

    def _poll_probe(self):
        if self._probe_done:
            self._fill_encoders()
        else:
            QTimer.singleShot(300, self._poll_probe)

    def _codec_key(self):
        return "h265" if self.cmb_codec.currentIndex() == 1 else "h264"

    def _fill_encoders(self):
        names = self._encoder_pool.get(self._codec_key(), [])
        cur = self.cmb_encoder.currentData()
        self.cmb_encoder.blockSignals(True)
        self.cmb_encoder.clear()
        self.cmb_encoder.addItem("自动（优先硬件）", "auto")
        for n in names:
            self.cmb_encoder.addItem(n, n)
        if cur:
            i = self.cmb_encoder.findData(cur)
            if i >= 0:
                self.cmb_encoder.setCurrentIndex(i)
        self.cmb_encoder.blockSignals(False)

    def _on_codec_changed(self):
        self._fill_encoders()

    # ------------------------------------------------------------------ 区域
    def _monitor(self):
        i = self.cmb_monitor.currentIndex()
        return self.monitors[max(0, min(i, len(self.monitors) - 1))]

    def _on_monitor_changed(self):
        m = self._monitor()
        x, y = self.sp_x.value(), self.sp_y.value()
        if not (m.left <= x < m.right and m.top <= y < m.bottom):
            self._set_region(m.left, m.top, m.right, m.bottom)

    def _set_region(self, l, t, r, b):
        self.sp_x.setValue(l)
        self.sp_y.setValue(t)
        self.sp_w.setValue(r - l)
        self.sp_h.setValue(b - t)

    def _use_full_monitor(self):
        m = self._monitor()
        self._set_region(m.left, m.top, m.right, m.bottom)

    def _pick_region(self):
        if self.state != "idle" or self.selector:
            return
        self.selector = RegionSelector(self.monitors, self)
        self.selector.finished.connect(self._on_region_picked)
        self.selector.cancelled.connect(lambda: setattr(self, "selector", None))
        self.selector.run()

    def _on_region_picked(self, mon_idx, rect):
        self.selector = None
        self.cmb_monitor.blockSignals(True)
        self.cmb_monitor.setCurrentIndex(mon_idx)
        self.cmb_monitor.blockSignals(False)
        self._set_region(*rect)

    # ------------------------------------------------------------------ 参数
    def _on_native_toggled(self, on):
        self.sp_ow.setEnabled(not on)
        self.sp_oh.setEnabled(not on)

    def _browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择输出目录", self.ed_outdir.text() or "")
        if d:
            self.ed_outdir.setText(d)

    def _collect_config(self):
        m = self._monitor()
        l = max(m.left, min(self.sp_x.value(), m.right - 8))
        t = max(m.top, min(self.sp_y.value(), m.bottom - 8))
        r = min(m.right, l + self.sp_w.value())
        b = min(m.bottom, t + self.sp_h.value())
        if r - l < 8 or b - t < 8:
            l, t, r, b = m.left, m.top, m.right, m.bottom
        w, h = (r - l) // 2 * 2, (b - t) // 2 * 2
        r, b = l + w, t + h

        try:
            fps = int(self.cmb_fps.currentText().strip())
        except ValueError:
            QMessageBox.warning(self, "参数错误", "帧率必须是整数")
            return None
        if not 1 <= fps <= 240:
            QMessageBox.warning(self, "参数错误", "帧率需在 1~240 之间")
            return None

        scale_wh = None
        if not self.chk_native.isChecked():
            ow, oh = self.sp_ow.value() // 2 * 2, self.sp_oh.value() // 2 * 2
            if ow < 64 or oh < 64:
                QMessageBox.warning(self, "参数错误", "输出分辨率过小")
                return None
            scale_wh = (ow, oh)

        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            QMessageBox.critical(self, "缺少依赖", "未找到 ffmpeg，请安装后重试。")
            return None

        codec = self._codec_key()
        quality = self.cmb_quality.currentIndex()
        enc_name = self.cmb_encoder.currentData() or "auto"
        available = self._encoder_pool.get(codec)
        if available is None:
            available = probe_encoders(ffmpeg, candidates_for(codec))
            self._encoder_pool[codec] = available
            self._fill_encoders()
        if quality == 3:
            lossless_name = "libx265" if codec == "h265" else "libx264"
            if lossless_name not in available:
                QMessageBox.warning(self, "编码器不可用",
                                    "无损模式需要 libx264/libx265，本机 ffmpeg 不支持。")
                return None
            enc_name = lossless_name
        elif enc_name == "auto":
            if not available:
                QMessageBox.critical(self, "编码器不可用",
                                     f"ffmpeg 探测不到 {codec} 的可用编码器。")
                return None
            enc_name = available[0]

        out_dir = os.path.expanduser(self.ed_outdir.text().strip() or
                                     os.path.join("~", "Videos", "录屏"))
        base = f"{self.ed_prefix.text().strip() or '录屏'}_{time.strftime('%Y%m%d_%H%M%S')}"
        return RecorderConfig(
            monitor_index=m.index, monitor_rect=m.rect, region=(l, t, r, b), fps=fps,
            out_dir=out_dir, base_name=base, ffmpeg=ffmpeg, encoder=enc_name,
            quality_level=quality, scale_wh=scale_wh, cursor=self.chk_cursor.isChecked())

    # ------------------------------------------------------------------ 状态机
    def _on_main_button(self):
        if self.selector:
            return
        if self.state == "idle":
            self._start_flow()
        elif self.state == "countdown":
            self._cancel_countdown()
        elif self.state in ("recording", "paused"):
            self._stop_flow()

    def _start_flow(self):
        cfg = self._collect_config()
        if cfg is None:
            return
        self._pending_cfg = cfg
        self._save_settings()
        self.state = "countdown"
        self.btn_main.setText("取消")
        if self.chk_hide.isChecked():
            self.hide()
        if self.chk_countdown.isChecked():
            screens = dict((m.index, s) for s, m in match_screens(self.monitors))
            self.countdown = _Countdown(screens[cfg.monitor_index], 3, self._begin_worker)
        else:
            QTimer.singleShot(0, self._begin_worker)

    def _cancel_countdown(self):
        if self.countdown:
            self.countdown.abort()
            self.countdown = None
        self.state = "idle"
        self._pending_cfg = None
        self.btn_main.setText("● 开始录制")
        if not self.isVisible():
            self._show_window()

    def _begin_worker(self):
        if self.state != "countdown" or self._pending_cfg is None:
            return
        self.countdown = None
        cfg = self._pending_cfg
        self.state = "recording"
        self.btn_main.setText("■ 停止录制")
        self.btn_pause.setText("暂停")
        self.btn_pause.setEnabled(True)
        self.act_toggle.setText("停止录制 (F9)")
        self.act_pause.setEnabled(True)
        for w in self._config_widgets:
            w.setEnabled(False)
        self.lbl_status.setStyleSheet("color:#E5484D;font-weight:bold;")
        self.lbl_status.setText("正在启动采集…")
        self.tray.setIcon(_make_icon("#E5484D"))
        bridge = RecorderBridge()
        bridge.started.connect(self._on_backend_started)
        bridge.stats.connect(self._on_stats)
        bridge.paused.connect(self._on_paused)
        bridge.resumed.connect(self._on_resumed)
        bridge.error.connect(self._on_error)
        bridge.finished.connect(self._on_finished)
        self.worker = RecorderWorker(cfg, bridge)
        self.worker.start()

    def _stop_flow(self):
        if not self.worker:
            return
        self.state = "saving"
        self.btn_main.setEnabled(False)
        self.btn_pause.setEnabled(False)
        self.lbl_status.setText("正在保存…")
        self.worker.stop()

    def _on_pause_button(self):
        if self.state == "recording" and self.worker:
            self.worker.pause()
        elif self.state == "paused" and self.worker:
            self.worker.resume()

    # ------------------------------------------------------------------ worker 回调
    def _on_backend_started(self, name):
        self._backend_name = name
        extra = "（Win10 下屏幕边缘会出现系统黄色边框，不会进入视频）" if name == "WGC" else ""
        self.lbl_status.setText(f"● 录制中 00:00 · {name} 后端{extra}")

    def _on_stats(self, st):
        if self.state not in ("recording", "paused"):
            return
        e = int(st["elapsed"])
        ts = (f"{e // 3600:02d}:{e % 3600 // 60:02d}:{e % 60:02d}" if e >= 3600
              else f"{e // 60:02d}:{e % 60:02d}")
        mb = st["bytes"] / 1048576
        prefix = "⏸ 已暂停 " if self.state == "paused" else "● 录制中 "
        self.lbl_status.setText(
            f"{prefix}{ts} · 实际 {st['fps']:.1f} fps · {mb:.1f} MB · "
            f"{self._backend_name} / {self._pending_cfg.encoder if self._pending_cfg else '?'}")
        self.tray.setToolTip(f"屏幕录制工具 - 录制中 {ts}")

    def _on_paused(self):
        self.state = "paused"
        self.btn_pause.setText("继续")
        self.act_pause.setText("继续 (F10)")
        self.tray.setIcon(_make_icon("#F5A623"))

    def _on_resumed(self):
        self.state = "recording"
        self.btn_pause.setText("暂停")
        self.act_pause.setText("暂停 (F10)")
        self.tray.setIcon(_make_icon("#E5484D"))

    def _on_error(self, msg):
        self._error_msg = msg

    def _on_finished(self, path):
        self.worker = None
        self.state = "idle"
        self._pending_cfg = None
        self.btn_main.setEnabled(True)
        self.btn_main.setText("● 开始录制")
        self.btn_pause.setEnabled(False)
        self.btn_pause.setText("暂停")
        self.act_toggle.setText("开始录制 (F9)")
        self.act_pause.setEnabled(False)
        self.act_pause.setText("暂停 (F10)")
        for w in self._config_widgets:
            w.setEnabled(True)
        self.tray.setIcon(_make_icon("#666"))
        self.lbl_status.setStyleSheet("")
        if path:
            self.lbl_status.setText(f"已保存：{path}")
            self.tray.showMessage("录制完成", f"已保存到\n{path}",
                                  QSystemTrayIcon.Information, 5000)
        err = self._error_msg
        self._error_msg = ""
        if err:
            self.lbl_status.setText("录制出错")
            self._show_window()
            QMessageBox.critical(self, "录制出错", err)
        elif not path:
            self.lbl_status.setText("录制未保存")
        self._show_window()

    # ------------------------------------------------------------------ 托盘/窗口
    def _show_window(self):
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowMinimized)
        self.raise_()
        self.activateWindow()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self._show_window()

    def _quit(self):
        if self.worker is not None:
            self.worker.stop()
            self.worker.join(timeout=20)
        self.hotkeys.unregister_all()
        self.tray.hide()
        QApplication.instance().quit()

    def closeEvent(self, ev):
        if self.worker is not None and self.state in ("recording", "paused"):
            ev.ignore()
            self.hide()
            self.tray.showMessage("仍在录制", "录制仍在后台进行：按 F9 停止，或使用托盘菜单。",
                                  QSystemTrayIcon.Information, 4000)
            return
        if self.worker is not None:  # saving 状态：等待收尾
            self.worker.stop()
            self.worker.join(timeout=20)
        self._save_settings()
        self.hotkeys.unregister_all()
        ev.accept()

    # ------------------------------------------------------------------ 快捷键
    def _apply_hotkeys(self):
        if self._hk_start_id is not None:
            self.hotkeys.unregister(self._hk_start_id)
            self._hk_start_id = None
        if self._hk_pause_id is not None:
            self.hotkeys.unregister(self._hk_pause_id)
            self._hk_pause_id = None
        actions = ((self.hk_start, "_hk_start_id", "开始/停止", self._on_main_button),
                   (self.hk_pause, "_hk_pause_id", "暂停/继续", self._on_pause_button))
        for editor, attr, label, fn in actions:
            seq = editor.keySequence()
            if seq.isEmpty():
                continue
            try:
                hid = self.hotkeys.register(seq)
                self.hotkeys.bind(hid, fn)
                setattr(self, attr, hid)
            except (OSError, ValueError) as e:
                editor.blockSignals(True)
                editor.setKeySequence(QKeySequence())
                editor.blockSignals(False)
                self.lbl_status.setText(f"{label} 快捷键注册失败：{e}")

    def _on_hotkey_edited(self):
        self._apply_hotkeys()

    # ------------------------------------------------------------------ 设置
    def _load_settings(self):
        s = self.settings
        self.cmb_monitor.setCurrentIndex(
            min(max(0, s.value("mon_idx", 0, int)), len(self.monitors) - 1))
        m = self._monitor()
        self._set_region(s.value("x", m.left, int), s.value("y", m.top, int),
                         s.value("w", m.width, int), s.value("h", m.height, int))
        self.cmb_fps.setCurrentText(s.value("fps", "60"))
        self.chk_native.setChecked(s.value("native", True, bool))
        self.sp_ow.setValue(s.value("ow", 1920, int))
        self.sp_oh.setValue(s.value("oh", 1080, int))
        self.cmb_quality.setCurrentIndex(s.value("quality", 1, int))
        self.ed_outdir.setText(s.value("out_dir", os.path.join("~", "Videos", "录屏")))
        self.ed_prefix.setText(s.value("prefix", "录屏"))
        self.chk_hide.setChecked(s.value("hide", True, bool))
        self.chk_cursor.setChecked(s.value("cursor", True, bool))
        self.chk_countdown.setChecked(s.value("countdown", True, bool))
        self.hk_start.blockSignals(True)
        self.hk_start.setKeySequence(QKeySequence(s.value("hk_start", "F9")))
        self.hk_pause.blockSignals(True)
        self.hk_pause.setKeySequence(QKeySequence(s.value("hk_pause", "F10")))
        self.hk_start.blockSignals(False)
        self.hk_pause.blockSignals(False)

    def _save_settings(self):
        s = self.settings
        s.setValue("mon_idx", self.cmb_monitor.currentIndex())
        s.setValue("x", self.sp_x.value())
        s.setValue("y", self.sp_y.value())
        s.setValue("w", self.sp_w.value())
        s.setValue("h", self.sp_h.value())
        s.setValue("fps", self.cmb_fps.currentText())
        s.setValue("native", self.chk_native.isChecked())
        s.setValue("ow", self.sp_ow.value())
        s.setValue("oh", self.sp_oh.value())
        s.setValue("quality", self.cmb_quality.currentIndex())
        s.setValue("out_dir", self.ed_outdir.text())
        s.setValue("prefix", self.ed_prefix.text())
        s.setValue("hide", self.chk_hide.isChecked())
        s.setValue("cursor", self.chk_cursor.isChecked())
        s.setValue("countdown", self.chk_countdown.isChecked())
        s.setValue("hk_start", self.hk_start.keySequence().toString())
        s.setValue("hk_pause", self.hk_pause.keySequence().toString())
        s.sync()
