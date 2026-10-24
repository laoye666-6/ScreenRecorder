"""物理显示器枚举与 DPI 感知（坐标均为物理像素）。"""

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

user32 = ctypes.windll.user32


def ensure_dpi_awareness() -> None:
    """必须早于任何窗口创建调用，保证枚举/选区坐标是物理像素。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


@dataclass
class Monitor:
    index: int
    device: str
    left: int
    top: int
    right: int
    bottom: int
    primary: bool

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def rect(self) -> tuple:
        return (self.left, self.top, self.right, self.bottom)

    def label(self) -> str:
        tag = "，主显示器" if self.primary else ""
        return f"显示器 {self.index + 1}（{self.width}×{self.height}{tag}）"


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", _RECT), ("rcWork", _RECT),
                ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]


_ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                               ctypes.POINTER(_RECT), wintypes.LPARAM)


def list_monitors() -> list:
    out = []

    def cb(hmon, hdc, rect, lparam):
        mi = _MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(_MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r = mi.rcMonitor
            out.append(Monitor(len(out), mi.szDevice, r.left, r.top, r.right, r.bottom,
                               bool(mi.dwFlags & 1)))
        return True

    user32.EnumDisplayMonitors(None, None, _ENUMPROC(cb), 0)
    return out
