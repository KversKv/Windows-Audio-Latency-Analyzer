# -*- mode: python ; coding: utf-8 -*-

# onedir: python -m PyInstaller spec/latency_analyzer.spec --clean --noconfirm
# onefile: $env:LATENCY_ANALYZER_ONEFILE="1"; python -m PyInstaller spec/latency_analyzer.spec --clean --noconfirm

import os
from pathlib import Path


project_root = Path(SPECPATH).parent
onefile = os.environ.get("LATENCY_ANALYZER_ONEFILE") == "1"

a = Analysis(
    [str(project_root / "main.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        (str(project_root / "ui" / "styles" / "dark_theme.qss"), "ui/styles"),
        (str(project_root / "ui" / "assets" / "app_icon.png"), "ui/assets"),
        (str(project_root / "ui" / "assets" / "spin_up.svg"), "ui/assets"),
        (str(project_root / "ui" / "assets" / "spin_down.svg"), "ui/assets"),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PySide6.QtNetwork",
        "PySide6.QtPdf",
        "PySide6.QtPdfWidgets",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtQuickWidgets",
        "PySide6.QtTest",
    ],
    noarchive=False,
    optimize=2,
)

# 界面使用 Widgets 光栅渲染，不收集未启用的软件 OpenGL 后端。
a.binaries = [
    item for item in a.binaries
    if Path(item[0]).name.lower() != "opengl32sw.dll"
]
pyz = PYZ(a.pure)

exe_dependencies = [a.binaries, a.datas] if onefile else []
exe = EXE(
    pyz,
    a.scripts,
    *exe_dependencies,
    [],
    exclude_binaries=not onefile,
    name="WindowsAudioLatencyAnalyzer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(project_root / "ui" / "assets" / "app_icon.ico"),
)

if not onefile:
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="WindowsAudioLatencyAnalyzer",
    )
