import time

print("=== DDA (DxgiDuplicationSession) ===")
try:
    from windows_capture import DxgiDuplicationSession
    s = DxgiDuplicationSession(monitor_index=1)
    ok, lat = 0, []
    last = None
    for i in range(40):
        t0 = time.perf_counter()
        f = s.acquire_frame(timeout_ms=100)
        if f is not None:
            arr = f.to_numpy()
            last = arr
            ok += 1
            lat.append((time.perf_counter() - t0) * 1000)
        time.sleep(0.033)
    print(f"DDA ok={ok}/40, latency avg={sum(lat)/max(1,len(lat)):.1f}ms")
    if last is not None:
        print("frame:", last.shape, last.dtype, "corner:", last[0, 0])
except Exception as e:
    print("DDA failed:", type(e).__name__, e)

print("=== WGC (WindowsCapture) ===")
try:
    from windows_capture import WindowsCapture
    cap = WindowsCapture(monitor_index=1, cursor_capture=True, draw_border=None)
    frames = []
    done = False

    @cap.event
    def on_frame_arrived(frame, capture_control):
        global done
        if len(frames) < 3:
            fb = frame.frame_buffer
            frames.append((frame.width, frame.height, str(frame.frame_format), fb.shape, fb.dtype, fb[0, 0]))
        if len(frames) >= 3 and not done:
            done = True
            capture_control.stop()

    @cap.event
    def on_closed():
        pass

    ctrl = cap.start_free_threaded()
    time.sleep(4)
    try:
        ctrl.stop()
    except Exception:
        pass
    for f in frames:
        print(f)
except Exception as e:
    print("WGC failed:", type(e).__name__, e)
