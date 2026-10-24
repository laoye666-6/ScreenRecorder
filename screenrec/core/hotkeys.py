"""全局快捷键：RegisterHotKey + Qt 原生事件过滤器（窗口隐藏时依然有效）。

RegisterHotKey(None) 产生的线程消息（hwnd=NULL）不经过 Qt 窗口过程，
因此绑定到一个隐藏的原生窗口上，由 attach_window(hwnd) 注入。
过滤器在主线程事件循环中被调用，回调无需额外的线程封送。
"""

import ctypes
import time
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, Qt
from PySide6.QtGui import QKeySequence

user32 = ctypes.WinDLL("user32", use_last_error=True)

WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000

_QT_MODS = [(Qt.ShiftModifier, MOD_SHIFT), (Qt.ControlModifier, MOD_CONTROL),
            (Qt.AltModifier, MOD_ALT), (Qt.MetaModifier, MOD_WIN)]


def qtkey_to_vk(key: int):
    """Qt.Key -> Windows VK 码（F 键与 ASCII 可打印键数值正好一致）。"""
    if Qt.Key_F1 <= key <= Qt.Key_F24:
        return 0x70 + (key - Qt.Key_F1)
    if 0x20 <= key <= 0x5A or 0xBA <= key <= 0xC0 or key in (0xDB, 0xDC):
        return key
    return None


class HotkeyManager(QAbstractNativeEventFilter):
    def __init__(self):
        QAbstractNativeEventFilter.__init__(self)
        self._next_id = 1
        self._registered = {}   # id -> (vk, mods)
        self._bindings = {}     # id -> callable
        self._hwnd = None
        self._last_fire = (0, 0.0)  # (id, 时钟) —— 某些合成按键按下/抬起会各发一次，去重防抖

    def attach_window(self, hwnd: int):
        """必须在注册前调用；注销也使用同一窗口。"""
        self._hwnd = hwnd

    def bind(self, hotkey_id: int, fn):
        self._bindings[hotkey_id] = fn

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY:
                hotkey_id = int(msg.wParam)
                now = time.perf_counter()
                last_id, last_t = self._last_fire
                if hotkey_id == last_id and now - last_t < 0.08:
                    return False, 0
                self._last_fire = (hotkey_id, now)
                fn = self._bindings.get(hotkey_id)
                if fn is not None:
                    try:
                        fn()
                    except Exception:
                        pass
        return False, 0

    def register(self, seq: QKeySequence) -> int:
        if seq.isEmpty():
            raise ValueError("快捷键为空")
        combo = seq[0]
        if isinstance(combo, int):
            key = combo & ~Qt.KeyboardModifierMask
        else:
            key = int(combo.key())
        vk = qtkey_to_vk(key)
        if vk is None:
            raise ValueError("仅支持 字母/数字/F1~F24 及其组合")
        m = MOD_NOREPEAT
        if isinstance(combo, int):
            mods = Qt.KeyboardModifiers(combo & Qt.KeyboardModifierMask)
        else:
            mods = combo.keyboardModifiers()
        for qt_mod, win_mod in _QT_MODS:
            if mods & qt_mod:
                m |= win_mod
        hotkey_id = self._next_id
        self._next_id += 1
        if not user32.RegisterHotKey(self._hwnd, hotkey_id, m, vk):
            err = ctypes.get_last_error()
            if err == 1409:  # ERROR_HOTKEY_ALREADY_REGISTERED
                raise OSError("该快捷键已被其他程序占用")
            raise OSError(f"快捷键注册失败（Win32 错误码 {err}）")
        self._registered[hotkey_id] = (vk, m)
        return hotkey_id

    def unregister(self, hotkey_id: int):
        self._bindings.pop(hotkey_id, None)
        if hotkey_id in self._registered:
            user32.UnregisterHotKey(self._hwnd, hotkey_id)
            del self._registered[hotkey_id]

    def unregister_all(self):
        for hotkey_id in list(self._registered):
            self.unregister(hotkey_id)
