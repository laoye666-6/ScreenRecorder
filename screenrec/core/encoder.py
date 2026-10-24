"""ffmpeg 编码：定位、能力探测、参数构建与分段合并。"""

import os
import shutil
import subprocess
import sys

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# 编码器 -> 家族（决定质量参数与滤镜链的写法）
ENCODER_KINDS = {
    "h264_nvenc": "nvenc", "hevc_nvenc": "nvenc",
    "h264_qsv": "qsv", "hevc_qsv": "qsv",
    "h264_amf": "amf", "hevc_amf": "amf",
    "libx264": "x264", "libx265": "x265",
}

QUALITY_LEVELS = ["超高", "高", "均衡", "无损"]
CODECS = [("h264", "H.264（兼容性最好）"), ("h265", "H.265（体积更小）")]


def find_ffmpeg():
    if getattr(sys, "frozen", False):
        # 1) 打包时内置的 ffmpeg；2) exe 同目录（用户可自行替换）
        bundled = os.path.join(getattr(sys, "_MEIPASS", ""), "ffmpeg.exe")
        if os.path.exists(bundled):
            return bundled
        beside = os.path.join(os.path.dirname(sys.executable), "ffmpeg.exe")
        if os.path.exists(beside):
            return beside
    p = shutil.which("ffmpeg")
    if p:
        return p
    local = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "bin", "ffmpeg.exe")
    if os.path.exists(local):
        return local
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def probe_encoders(ffmpeg, names):
    """用与录制完全一致的 BGRA 管道输入逐个试编码，返回可用列表。"""
    ok = []
    data = bytes(320 * 240 * 4)
    for name in names:
        try:
            r = subprocess.run(
                [ffmpeg, "-hide_banner", "-v", "error", "-nostdin",
                 "-f", "rawvideo", "-pix_fmt", "bgra", "-s", "320x240", "-r", "30",
                 "-i", "pipe:0", "-frames:v", "3", "-c:v", name, "-f", "null", "-"],
                input=data, capture_output=True, timeout=60, creationflags=CREATE_NO_WINDOW)
            if r.returncode == 0:
                ok.append(name)
        except Exception:
            pass
    return ok


def candidates_for(codec: str):
    """按优先级返回候选编码器（先硬件后软件）。"""
    if codec == "h265":
        return ["hevc_nvenc", "hevc_qsv", "hevc_amf", "libx265"]
    return ["h264_nvenc", "h264_qsv", "h264_amf", "libx264"]


def _quality_args(kind: str, level: int, codec: str):
    """level: 0=超高 1=高 2=均衡 3=无损。返回 (编码参数, 强制输出 pix_fmt)。"""
    if kind in ("x264", "x265"):
        if level == 3:
            return ["-qp", "0"], "yuv444p"
        crf = (12, 18, 24)[level] if codec == "h264" else (16, 23, 28)[level]
        preset = ("slow", "medium", "veryfast")[level]
        return ["-crf", str(crf), "-preset", preset], None
    if kind == "qsv":
        q = (15, 20, 26)[level]
        return ["-global_quality", str(q), "-preset", "medium"], None
    if kind == "nvenc":
        q = (15, 19, 25)[level]
        return ["-preset", "p5", "-rc", "vbr", "-cq", str(q), "-b:v", "0"], None
    if kind == "amf":
        q = (15, 20, 25)[level]
        return ["-quality", "quality", "-rc", "cqp",
                "-qp_i", str(q), "-qp_p", str(q + 2), "-qp_b", str(q + 4)], None
    raise ValueError(kind)


def build_ffmpeg_args(*, ffmpeg, in_w, in_h, fps, out_w, out_h, encoder,
                      quality_level, out_path, faststart=True, log_path=None):
    kind = ENCODER_KINDS[encoder]
    codec = "hevc" if encoder.startswith("hevc") else "h264"
    lossless = quality_level == 3 and kind in ("x264", "x265")

    args = [ffmpeg, "-hide_banner", "-v", "warning", "-nostdin", "-y",
            "-f", "rawvideo", "-pix_fmt", "bgra",
            "-s", f"{in_w}x{in_h}", "-r", str(fps), "-i", "pipe:0"]

    vf = []
    if (out_w, out_h) != (in_w, in_h):
        vf.append(f"scale={out_w}:{out_h}:flags=lanczos")
    # x264/x265/qsv 走 CPU 侧色域转换（RGB 全范围 -> BT.709 有限范围）；
    # nvenc/amf 直接吃 BGRA 由 GPU 内部转换，除非需要缩放
    if kind in ("x264", "x265", "qsv"):
        if vf:
            vf = [f"scale={out_w}:{out_h}:flags=lanczos:out_color_matrix=bt709:"
                  f"out_range={'full' if lossless else 'tv'}"]
        else:
            vf = [f"scale=out_color_matrix=bt709:out_range={'full' if lossless else 'tv'}"]
        vf.append("format=yuv444p" if lossless else "format=yuv420p")
    if vf:
        args += ["-vf", ",".join(vf)]

    qargs, _ = _quality_args(kind, quality_level, codec)
    args += ["-c:v", encoder] + qargs
    if lossless:
        args += ["-color_range", "2"]
    else:
        args += ["-colorspace", "bt709", "-color_primaries", "bt709",
                 "-color_trc", "bt709", "-color_range", "tv"]
    args += ["-g", str(max(2, int(fps) * 2))]
    if faststart:
        args += ["-movflags", "+faststart"]
    args.append(out_path)
    return args


def concat_segments(ffmpeg, seg_paths, out_path):
    """concat demuxer 无损拼接（同时完成 faststart 重排）。"""
    if len(seg_paths) == 1:
        _remux_one(ffmpeg, seg_paths[0], out_path)
    else:
        lst = out_path + ".list.txt"
        with open(lst, "w", encoding="utf-8") as f:
            for s in seg_paths:
                f.write("file '%s'\n" % s.replace("'", "'\\''"))
        r = subprocess.run(
            [ffmpeg, "-hide_banner", "-v", "warning", "-nostdin", "-y",
             "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy",
             "-movflags", "+faststart", out_path],
            capture_output=True, timeout=600, creationflags=CREATE_NO_WINDOW)
        os.remove(lst)
        if r.returncode != 0:
            raise RuntimeError("拼接失败: " + r.stderr.decode("utf-8", "replace")[-800:])
    for s in seg_paths:
        _cleanup(s)
    return out_path


def _remux_one(ffmpeg, src, dst):
    r = subprocess.run(
        [ffmpeg, "-hide_banner", "-v", "warning", "-nostdin", "-y",
         "-i", src, "-c", "copy", "-movflags", "+faststart", dst],
        capture_output=True, timeout=600, creationflags=CREATE_NO_WINDOW)
    if r.returncode != 0:
        # 退回直接改名（moov 在尾部，文件仍有效）
        shutil.move(src, dst)
        return
    _cleanup(src)


def _cleanup(path):
    for p in (path, path + ".log"):
        try:
            os.remove(p)
        except OSError:
            pass
