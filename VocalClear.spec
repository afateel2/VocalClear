# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for VocalClear
#
# Build:  pyinstaller VocalClear.spec
# Output: dist\VocalClear\VocalClear.exe  (one-folder) — or use --onefile for a single exe
#
# Requirements:
#   pip install pyinstaller
#   All packages from requirements.txt must be installed in the active venv.

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

ROOT = Path(SPECPATH)

# ── Data files ──────────────────────────────────────────────────────────────
datas = [
    # App icon
    (str(ROOT / "vocalclear.ico"), "."),
]

# DeepFilterNet ships model weights as package data — include them if installed
try:
    datas += collect_data_files("df", includes=["*.onnx", "*.bin", "*.json"])
except Exception:
    pass

# imageio_ffmpeg bundles ffmpeg.exe — include it
try:
    datas += collect_data_files("imageio_ffmpeg", includes=["**/*"])
except Exception:
    pass

# ── Hidden imports ───────────────────────────────────────────────────────────
hidden_imports = [
    "sounddevice",
    "soundfile",
    "scipy.signal",
    "scipy._lib.messagestream",
    "numpy",
    "pystray._win32",
    "PIL._tkinter_finder",
    "PIL.Image",
    "PIL.ImageDraw",
    "PIL.PngImagePlugin",
    "PIL.BmpImagePlugin",
    "PIL.IcoImagePlugin",
    "PIL._imaging",
    "tkinter",
    "tkinter.ttk",
    "tkinter.filedialog",
    "tkinter.messagebox",
    "winreg",
    "ctypes.wintypes",
    "imageio_ffmpeg",
    "zipfile",
    "webbrowser",
]

# Optional AI backends
for mod in ("df", "df.enhance", "pyrnnoise.rnnoise"):
    hidden_imports.append(mod)

# ── Binary dependencies ──────────────────────────────────────────────────────
binaries = []
try:
    binaries += collect_dynamic_libs("sounddevice")
except Exception:
    pass

# rnnoise.dll — used by pyrnnoise.rnnoise via ctypes; must land next to the
# pyrnnoise package so os.path.dirname(__file__) resolves it correctly.
try:
    import os as _os, pyrnnoise as _pyrnn
    _rnn_dll = _os.path.join(_os.path.dirname(_pyrnn.__file__), "rnnoise.dll")
    if _os.path.exists(_rnn_dll):
        binaries.append((_rnn_dll, "pyrnnoise"))
except Exception:
    pass

# ── Analysis ─────────────────────────────────────────────────────────────────
a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "IPython", "jupyter", "notebook", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VocalClear",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                          # UPX can trigger AV false positives
    console=False,                      # windowed — no CMD window (equivalent to pythonw)
    disable_windowed_traceback=False,
    icon=str(ROOT / "vocalclear.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="VocalClear",
)
