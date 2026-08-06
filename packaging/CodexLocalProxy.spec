from __future__ import annotations

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


ROOT = Path(SPECPATH).resolve().parent
ICON_PATH = Path(os.environ["CODEX_LOCAL_PROXY_ICON"]).resolve()
VERSION_FILE = Path(os.environ["CODEX_LOCAL_PROXY_VERSION_FILE"]).resolve()

hidden_imports = [
    "pystray._win32",
    *collect_submodules("tiktoken_ext"),
]
data_files = [
    (str(ROOT / "proxy_static"), "proxy_static"),
    *collect_data_files("tiktoken"),
]

a = Analysis(
    [str(ROOT / "codex_local_proxy_app.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=data_files,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="CodexLocalProxy-win-x64",
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
    icon=str(ICON_PATH),
    version=str(VERSION_FILE),
)
