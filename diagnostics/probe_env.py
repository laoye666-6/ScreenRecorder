import time
import ctypes

# per-monitor DPI aware, so coordinates are physical pixels
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

user32 = ctypes.windll.user32

class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT), ("rcWork", RECT),
                ("dwFlags", ctypes.c_ulong), ("szDevice", ctypes.c_wchar * 32)]

PROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.POINTER(RECT), ctypes.c_long)
mons = []
def cb(hmon, hdc, lprect, lparam):
    mi = MONITORINFOEXW(); mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
    user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
    r = mi.rcMonitor
    mons.append((mi.szDevice, (r.left, r.top, r.right, r.bottom),
                 bool(mi.dwFlags & 1), user32.GetDpiForMonitor(hmon, 0) if hasattr(user32, "GetDpiForMonitor") else 0))
    return True
user32.EnumDisplayMonitors(None, None, PROC(cb), 0)
print("== EnumDisplayMonitors ==")
for m in mons:
    print(m)
print("virtual:", user32.GetSystemMetrics(76), user32.GetSystemMetrics(77),
      user32.GetSystemMetrics(78), user32.GetSystemMetrics(79))

try:
    import bettercam
    lib = "bettercam"
except ImportError:
    import dxcam as lib_mod
    lib = "dxcam"
print("== capture lib:", lib, "==")
mod = bettercam if lib == "bettercam" else lib_mod
import inspect
print("create signature:", inspect.signature(mod.create))
info = mod.output_info()
print("output_info type:", type(info))
print(info)

cam = mod.create(output_idx=0, output_color="BGRA", max_buffer_len=2)
print("camera attrs:", [a for a in dir(cam) if not a.startswith("_")])
try:
    print("is_creating:", cam.is_creating)
except Exception:
    pass
# grab timing on possibly idle screen
t0 = time.perf_counter()
frames = 0
nones = 0
first = None
for i in range(120):
    f = cam.grab()
    if f is None:
        nones += 1
    else:
        frames += 1
        if first is None:
            first = f
el = time.perf_counter() - t0
print(f"grab loop: {el:.3f}s frames={frames} none={nones} shape={None if first is None else first.shape} dtype={None if first is None else first.dtype}")
# per-grab latency with sleep to let desktop update
lat = []
for i in range(30):
    t0 = time.perf_counter()
    f = cam.grab()
    if f is not None:
        lat.append((time.perf_counter() - t0) * 1000)
    time.sleep(0.033)
print("grab latency ms (non-null):", [f"{x:.1f}" for x in lat[:10]])
del cam
