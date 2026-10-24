"""打包准备：把静态 ffmpeg.exe 放到 packaging/（仅构建时需要）。

优先从 pip 包 imageio-ffmpeg 中提取（静态、自包含）；没有则从 gyan.dev 下载 essentials。
"""

import os
import shutil
import sys
import urllib.request
import zipfile

GYAN_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HERE, "ffmpeg.exe")


def from_imageio_ffmpeg():
    try:
        import imageio_ffmpeg
    except ImportError:
        return False
    src = imageio_ffmpeg.get_ffmpeg_exe()
    shutil.copyfile(src, TARGET)
    print("已从 imageio-ffmpeg 提取:", src)
    return True


def from_gyan():
    zip_path = os.path.join(HERE, "ffmpeg-essentials.zip")
    if not os.path.exists(zip_path):
        print("下载", GYAN_URL)
        urllib.request.urlretrieve(GYAN_URL, zip_path)
    with zipfile.ZipFile(zip_path) as z:
        name = next(n for n in z.namelist() if n.replace("\\", "/").endswith("/bin/ffmpeg.exe"))
        with open(TARGET, "wb") as f:
            f.write(z.read(name))
    print("已从 essentials 压缩包提取:", name)
    return True


def main():
    if os.path.exists(TARGET):
        print("ffmpeg.exe 已存在，跳过")
        return 0
    if from_imageio_ffmpeg():
        return 0
    from_gyan()
    return 0


if __name__ == "__main__":
    sys.exit(main())
