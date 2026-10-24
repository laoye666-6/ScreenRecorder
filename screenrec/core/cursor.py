"""鼠标指针合成：DDA 原始帧不含光标，用 GetCursorInfo + 光标位图手动叠加。"""

import ctypes
from ctypes import wintypes

import numpy as np

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

CURSOR_SHOWING = 0x00000001


class _CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hCursor", wintypes.HANDLE), ("ptScreenPos", wintypes.POINT)]


class _ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD),
                ("yHotspot", wintypes.DWORD), ("hbmMask", wintypes.HANDLE),
                ("hbmColor", wintypes.HANDLE)]


class _BITMAP(ctypes.Structure):
    _fields_ = [("bmType", ctypes.c_long), ("bmWidth", ctypes.c_long),
                ("bmHeight", ctypes.c_long), ("bmWidthBytes", ctypes.c_long),
                ("bmPlanes", wintypes.WORD), ("bmBitsPixel", wintypes.WORD),
                ("bmBits", ctypes.c_void_p)]


class _BMIHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class _BMI(ctypes.Structure):
    _fields_ = [("bmiHeader", _BMIHEADER), ("colors", wintypes.DWORD * 3)]


class CursorCompositor:
    """把系统光标按当前位置与形状混入 BGRA 帧（frame 会被原地修改）。"""

    def __init__(self):
        self._cache = {}      # hCursor -> (bgra ndarray, hotspot_x, hotspot_y)
        self._order = []

    def blend(self, frame: np.ndarray, origin: tuple) -> np.ndarray:
        ci = _CURSORINFO()
        ci.cbSize = ctypes.sizeof(_CURSORINFO)
        if not user32.GetCursorInfo(ctypes.byref(ci)):
            return frame
        if not (ci.flags & CURSOR_SHOWING) or not ci.hCursor:
            return frame
        entry = self._cache.get(ci.hCursor)
        if entry is None:
            try:
                entry = self._load(ci.hCursor)
            except Exception:
                entry = None
            self._cache[ci.hCursor] = entry
            self._order.append(ci.hCursor)
            if len(self._order) > 16:
                old = self._order.pop(0)
                self._cache.pop(old, None)
        if entry is None:
            return frame
        bgra, hx, hy = entry
        h, w = bgra.shape[:2]
        x = ci.ptScreenPos.x - hx - origin[0]
        y = ci.ptScreenPos.y - hy - origin[1]
        fh, fw = frame.shape[:2]
        x0, y0 = max(x, 0), max(y, 0)
        x1, y1 = min(x + w, fw), min(y + h, fh)
        if x0 >= x1 or y0 >= y1:
            return frame
        sx0, sy0 = x0 - x, y0 - y
        reg = frame[y0:y1, x0:x1]
        cur = bgra[sy0:sy0 + (y1 - y0), sx0:sx0 + (x1 - x0)]
        a = cur[..., 3:4].astype(np.float32) / 255.0
        reg[..., :3] = (cur[..., :3] * a + reg[..., :3] * (1.0 - a)).astype(np.uint8)
        return frame

    def _load(self, hcursor):
        info = _ICONINFO()
        if not user32.GetIconInfo(hcursor, ctypes.byref(info)):
            return None
        try:
            color = self._read_32bpp(info.hbmColor) if info.hbmColor else None
            mask = self._read_1bpp(info.hbmMask) if info.hbmMask else None
            if color is not None and mask is not None:
                bgra = color
                alpha = bgra[..., 3]
                if alpha.max() == 0:  # 经典 AND/XOR 光标：颜色位无 alpha
                    andm = mask[:bgra.shape[0]]
                    bgra[..., 3] = np.where(andm > 0, 0, 255)
            elif mask is not None:
                hh = mask.shape[0] // 2
                andm, xorm = mask[:hh], mask[hh:]
                bgra = np.zeros((hh, mask.shape[1], 4), np.uint8)
                bgra[..., 0:3] = 255 * xorm[..., None]
                bgra[..., 3] = np.where(andm > 0, 0, 255)
            else:
                return None
            return (bgra, info.xHotspot, info.yHotspot)
        finally:
            if info.hbmColor:
                gdi32.DeleteObject(info.hbmColor)
            if info.hbmMask:
                gdi32.DeleteObject(info.hbmMask)

    def _read_32bpp(self, hbm):
        bm = _BITMAP()
        if not gdi32.GetObjectW(hbm, ctypes.sizeof(_BITMAP), ctypes.byref(bm)):
            return None
        w, h = bm.bmWidth, bm.bmHeight
        if w <= 0 or h <= 0:
            return None
        bmi = _BMI()
        bmi.bmiHeader.biSize = ctypes.sizeof(_BMIHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h  # 自顶向下
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0
        buf = np.zeros((h, w, 4), np.uint8)
        dc = user32.GetDC(None)
        try:
            if not gdi32.GetDIBits(dc, hbm, 0, h, buf.ctypes.data, ctypes.byref(bmi), 0):
                return None
        finally:
            user32.ReleaseDC(None, dc)
        return buf

    def _read_1bpp(self, hbm):
        bm = _BITMAP()
        if not gdi32.GetObjectW(hbm, ctypes.sizeof(_BITMAP), ctypes.byref(bm)):
            return None
        w, h = bm.bmWidth, bm.bmHeight
        stride = ((w + 31) // 32) * 4
        size = stride * h
        bmi = _BMI()
        bmi.bmiHeader.biSize = ctypes.sizeof(_BMIHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 1
        bmi.bmiHeader.biCompression = 0
        raw = np.zeros(size, np.uint8)
        dc = user32.GetDC(None)
        try:
            if not gdi32.GetDIBits(dc, hbm, 0, h, raw.ctypes.data, ctypes.byref(bmi), 0):
                return None
        finally:
            user32.ReleaseDC(None, dc)
        bits = np.unpackbits(raw.view(np.uint8), axis=0)[: h * stride * 8]
        return (bits.reshape(h, stride * 8)[:, :w] > 0).astype(np.uint8)
