# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all
from pathlib import Path
import psycopg
import PySide6

datas = [('app.ico', '.')]
binaries = []
hiddenimports = ['psycopg_binary.pq', 'psycopg_binary._psycopg']
hiddenimports += collect_submodules('psycopg')
tmp_ret = collect_all('psycopg_binary')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PyQt6', 'PyQt5', 'tkinter', 'matplotlib', 'numpy', 'pandas', 'scipy', 'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtQml', 'PySide6.QtQuick'],
    noarchive=False,
    optimize=0,
)
# Python 3.11.0 ships older VC DLLs. Use Qt's redistributables consistently
# for every destination; otherwise the bootloader preloads an incompatible DLL.
qt_dir = Path(PySide6.__file__).parent
runtime_names = {'vcruntime140.dll','vcruntime140_1.dll','msvcp140.dll','msvcp140_1.dll','msvcp140_2.dll'}
fixed = []
for destination, original, kind in a.binaries:
    name = Path(original).name
    # Windows 10/11 provides UCRT and API-set forwarding DLLs. Runtime PATH
    # may expose legacy copies from unrelated tools; never redistribute those.
    if name.lower() in {'ucrtbase.dll','icuuc.dll','icuin.dll'} or name.lower().startswith(('api-ms-win-', 'ext-ms-win-')):
        continue
    replacement = qt_dir / name
    if name.lower() in runtime_names and replacement.exists():
        original = str(replacement)
    fixed.append((destination, original, kind))
a.binaries = fixed
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='锦纷资产管理系统',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['app.ico'],
    version='version_info.txt',
)

