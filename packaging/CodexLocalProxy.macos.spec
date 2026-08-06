# PyInstaller spec for the macOS .app bundle.
#
# Mirrors packaging/CodexLocalProxy.spec (the Windows build) with the
# platform-specific differences:
#   * tray backend hidden import is pystray._appkit (AppKit) instead of win32
#   * no Windows VSVersionInfo; the bundle version comes from Info.plist
#   * produces a CodexLocalProxy-macos-arm64.app via BUNDLE
#
# Required environment variables (set by scripts/build_local_proxy_macos.sh):
#   CODEX_LOCAL_PROXY_ICON         path to the generated .icns file
#   CODEX_LOCAL_PROXY_VERSION      release version (major.minor.patch)

from __future__ import annotations

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


ROOT = Path(SPECPATH).resolve().parent
ICON_PATH = Path(os.environ["CODEX_LOCAL_PROXY_ICON"]).resolve()
APP_VERSION = os.environ["CODEX_LOCAL_PROXY_VERSION"]

hidden_imports = [
    "pystray._appkit",
    *collect_submodules("tiktoken_ext"),
]
data_files = [
    (str(ROOT / "proxy_static"), "proxy_static"),
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
    [],
    exclude_binaries=True,
    name="CodexLocalProxy-macos-arm64",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="arm64",
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON_PATH),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="CodexLocalProxy-macos-arm64",
)

app = BUNDLE(
    coll,
    name="CodexLocalProxy-macos-arm64.app",
    icon=str(ICON_PATH),
    bundle_identifier="com.loongkkk.codex-local-proxy",
    info_plist={
        "CFBundleName": "Codex Local Proxy",
        "CFBundleDisplayName": "Codex 本地中转",
        "CFBundleShortVersionString": APP_VERSION,
        "CFBundleVersion": APP_VERSION,
        "CFBundlePackageType": "APPL",
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
        "LSUIElement": True,
    },
)
