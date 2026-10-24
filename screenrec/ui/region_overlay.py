"""全屏半透明框选录制区域（每个屏幕一个覆盖层，坐标为物理像素）。"""

from PySide6.QtCore import QObject, QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

HINT = "拖拽框选录制区域 · 双击选择整个屏幕 · Esc 取消"
BORDER = QColor("#E5484D")


def match_screens(monitors):
    """把 EnumDisplayMonitors 的物理矩形与 QScreen 一一配对。"""
    screens = QGuiApplication.screens()
    pairs, used = [], set()
    for m in monitors:
        best, best_area = None, -1
        for j, s in enumerate(screens):
            if j in used:
                continue
            dpr = s.devicePixelRatio()
            g = s.geometry()
            pr = (round(g.left() * dpr), round(g.top() * dpr),
                  round((g.left() + g.width()) * dpr), round((g.top() + g.height()) * dpr))
            w = min(m.right, pr[2]) - max(m.left, pr[0])
            h = min(m.bottom, pr[3]) - max(m.top, pr[1])
            if w * h > best_area:
                best, best_area = j, w * h
        if best is None:
            best = min(len(pairs), len(screens) - 1)
        used.add(best)
        pairs.append((screens[best], m))
    return pairs


class _OverlayWidget(QWidget):
    def __init__(self, screen, monitor, controller):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setScreen(screen)
        self._screen, self._monitor, self._controller = screen, monitor, controller
        self._start = None   # 屏幕内物理坐标
        self._cur = None
        self.setCursor(Qt.CrossCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setGeometry(screen.geometry())

    def _to_phys(self, pos: QPoint):
        dpr = self._screen.devicePixelRatio()
        return (self._monitor.left + round(pos.x() * dpr),
                self._monitor.top + round(pos.y() * dpr))

    def _rect_phys(self):
        if self._start is None or self._cur is None:
            return None
        return (min(self._start[0], self._cur[0]), min(self._start[1], self._cur[1]),
                max(self._start[0], self._cur[0]), max(self._start[1], self._cur[1]))

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self._start = self._to_phys(ev.position().toPoint())
            self._cur = self._start
            self.update()
        elif ev.button() == Qt.RightButton:
            self._controller.cancel()

    def mouseMoveEvent(self, ev):
        if self._start is not None:
            self._cur = self._to_phys(ev.position().toPoint())
            self.update()

    def mouseReleaseEvent(self, ev):
        if ev.button() != Qt.LeftButton or self._start is None:
            return
        rect = self._rect_phys()
        self._start = self._cur = None
        if rect and rect[2] - rect[0] >= 8 and rect[3] - rect[1] >= 8:
            self._controller.finish(rect, self._monitor.index)
        else:
            self.update()

    def mouseDoubleClickEvent(self, ev):
        m = self._monitor
        self._controller.finish((m.left, m.top, m.right, m.bottom), m.index)

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            self._controller.cancel()

    def paintEvent(self, _):
        p = QPainter(self)
        rect = self._rect_phys()
        dpr = self._screen.devicePixelRatio()
        ml, mt = self._monitor.left, self._monitor.top

        # OddEven 填充：整屏压暗，选中区域镂空
        path = QPainterPath()
        path.addRect(self.rect())
        sel = None
        if rect:
            sel = QRectF((rect[0] - ml) / dpr, (rect[1] - mt) / dpr,
                         (rect[2] - rect[0]) / dpr, (rect[3] - rect[1]) / dpr)
            path.addRect(sel)
        p.fillPath(path, QColor(0, 0, 0, 110))

        f = QFont(self.font())
        f.setPointSize(max(10, self.font().pointSize() + 1))
        p.setFont(f)
        p.setPen(QColor(255, 255, 255, 210))
        p.drawText(QRect(0, 12, self.width(), 30), Qt.AlignHCenter, HINT)

        if sel is not None:
            p.setPen(QPen(BORDER, 2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(sel)
            p.setPen(QColor("white"))
            fb = QFont(self.font())
            fb.setBold(True)
            p.setFont(fb)
            label = f"{rect[2] - rect[0]} × {rect[3] - rect[1]}"
            label_rect = QRectF(sel.left(), max(0.0, sel.top() - 26), max(140, sel.width()), 24)
            p.drawText(label_rect, Qt.AlignRight | Qt.AlignVCenter, label)
        p.end()


class RegionSelector(QObject):
    finished = Signal(int, tuple)   # monitor_index, 物理全局 rect(l,t,r,b)
    cancelled = Signal()

    def __init__(self, monitors, parent=None):
        super().__init__(parent)
        self._widgets = []
        for screen, m in match_screens(monitors):
            self._widgets.append(_OverlayWidget(screen, m, self))

    def run(self):
        for w in self._widgets:
            w.show()
            w.raise_()
            w.activateWindow()
            w.setFocus()

    def close(self):
        for w in self._widgets:
            w.close()
        self._widgets = []

    def finish(self, rect, monitor_index):
        self.close()
        self.finished.emit(monitor_index, rect)

    def cancel(self):
        self.close()
        self.cancelled.emit()
