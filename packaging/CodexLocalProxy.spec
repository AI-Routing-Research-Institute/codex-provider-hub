from __future__ import annotations

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


ROOT = Path(SPECPATH).resolve().parent
ICON_PATH = Path(os.environ["CODEX_LOCAL_PROXY_ICON"]).resolve()
VERSION_FILE = Path(os.environ["CODEX_LOCAL_PROXY_VERSION_FILE"]).resolve()

hidden_imports = [
    "pystray._win32",
    "paramiko",
    "paramiko.transport",
    "cryptography.hazmat.primitives.asymmetric.ed25519",
    *collect_submodules("tiktoken_ext"),
]
data_files = [
    (str(ROOT / "proxy_static" / "classic"), "proxy_static/classic"),
    (str(ROOT / "proxy_static" / "dist"), "proxy_static/dist"),
    (str(ROOT / "scripts" / "status_provider_import.py"), "scripts"),
    (str(ROOT / "provider_status" / "config.py"), "status_bootstrap"),
    (str(ROOT / "provider_status" / "claude_probe.py"), "status_bootstrap"),
    *collect_data_files("tiktoken"),
]

a = Analysis(
    [str(ROOT / "local_proxy_app.py")],
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
