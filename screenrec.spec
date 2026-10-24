# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：python -m PyInstaller --clean -y screenrec.spec"""
import os

a = Analysis(
    ["run.py"],
    pathex=[os.path.abspath(".")],
    binaries=[],
    datas=[
        # 打包前先运行 packaging/prepare.py 下载静态 ffmpeg
        ("packaging/ffmpeg.exe", "."),
    ],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "tkinter", "unittest", "pydoc_data",
        "PySide6.QtNetwork", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
        "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D",
        "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtSvg",
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
        "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtTest",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ScreenRecorder",
    icon="packaging/icon.ico",
    console=False,
    disable_windowed_traceback=False,
    upx=False,
    clean=True,
)
