import ctypes
import traceback

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

import bettercam

print("== device_info ==")
print(bettercam.device_info())
print("== output_info ==")
print(bettercam.output_info())

# probe every device/output combo
for d in range(4):
    for o in range(3):
        try:
            cam = bettercam.create(device_idx=d, output_idx=o, output_color="BGRA", max_buffer_len=2)
            print(f"OK   device={d} output={o}")
            import time
            ok = 0; nones = 0
            t0 = time.perf_counter()
            for i in range(60):
                f = cam.grab()
                if f is None: nones += 1
                else:
                    ok += 1
                    if ok == 1:
                        print("   first frame:", f.shape, f.dtype, "corner px:", f[0, 0])
            el = time.perf_counter() - t0
            print(f"   60 grabs in {el:.3f}s ok={ok} none={nones}")
            del cam
        except Exception as e:
            print(f"FAIL device={d} output={o}: {type(e).__name__}: {e}")
