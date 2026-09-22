# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.building.build_main import Analysis, PYZ, EXE

a = Analysis(
    ['gui_launcher.py'],
    pathex=[],
    binaries=[],
    datas=[('assets', 'assets'), ('config_gui.json', '.')],
    hiddenimports=['tkinter', 'tkinter.ttk'],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=True,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name='SAI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon='assets/icon.ico',
)
