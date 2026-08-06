#!/usr/bin/env bash
# Build the macOS .app bundle for the Codex local proxy.
#
# Mirrors scripts/build_local_proxy_exe.ps1 (the Windows build):
#   * validates the requested version
#   * generates the .icns app icon via the app's own --write-icon flag
#   * runs PyInstaller with packaging/CodexLocalProxy.macos.spec
#   * packages the .app into a .zip (the GitHub Release artifact) and
#     emits a .sha256 checksum
#
# Usage: build_local_proxy_macos.sh <version> [dist-directory]
#   version         major.minor.patch (must match the release tag)
#   dist-directory  output folder (default: dist)
set -euo pipefail

VERSION="${1:-}"
DIST_DIRECTORY="${2:-dist}"

if [[ ! $VERSION =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    printf 'Version must use major.minor.patch format: %s\n' "$VERSION" >&2
    exit 2
fi

PROJECT_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="$PROJECT_PATH/.venv/bin/python"
if [[ -x $VENV_PYTHON ]]; then
    PYTHON="$VENV_PYTHON"
else
    PYTHON="$(command -v python3 || command -v python)"
fi

# The release version is dictated by the caller (CI derives it from the git
# tag, the single source of truth). The APP_VERSION constant in source is only
# a development-time default and may lag behind published releases.

BUILD_PATH="$PROJECT_PATH/.build/local-proxy"
WORK_PATH="$BUILD_PATH/work"
ICON_PATH="$BUILD_PATH/codex-local-proxy.icns"
DIST_PATH="$(
    if [[ $DIST_DIRECTORY == /* ]]; then
        printf '%s' "$DIST_DIRECTORY"
    else
        printf '%s/%s' "$PROJECT_PATH" "$DIST_DIRECTORY"
    fi
)"

mkdir -p "$BUILD_PATH" "$WORK_PATH" "$DIST_PATH"

# Generate the app icon. The .icns suffix selects the macOS container.
"$PYTHON" "$PROJECT_PATH/local_proxy_app.py" --write-icon "$ICON_PATH"
if [[ ! -f $ICON_PATH ]]; then
    printf 'Unable to generate the application icon: %s\n' "$ICON_PATH" >&2
    exit 1
fi

export CODEX_LOCAL_PROXY_ICON="$ICON_PATH"
export CODEX_LOCAL_PROXY_VERSION="$VERSION"

# Build the .app bundle (onedir + BUNDLE).
"$PYTHON" -m PyInstaller \
    --noconfirm \
    --clean \
    --distpath "$DIST_PATH" \
    --workpath "$WORK_PATH" \
    "$PROJECT_PATH/packaging/CodexLocalProxy.macos.spec"

APP_PATH="$DIST_PATH/CodexLocalProxy-macos-arm64.app"
if [[ ! -d $APP_PATH ]]; then
    printf 'Build artifact was not found: %s\n' "$APP_PATH" >&2
    exit 1
fi

# Ship the .app as a .zip so the bundle structure survives the download.
# (dmg would require an extra toolchain; zip is the standard GitHub Release
# transport for macOS apps and preserves permissions/symlinks.)
ZIP_PATH="$DIST_PATH/CodexLocalProxy-macos-arm64.zip"
# ditto preserves macOS metadata better than zip; fall back to zip if absent.
if command -v ditto >/dev/null 2>&1; then
    ditto -c -k --keepParent "$APP_PATH" "$ZIP_PATH"
else
    (cd "$DIST_PATH" && zip -r -y "$(basename "$ZIP_PATH")" "$(basename "$APP_PATH")")
fi

CHECKSUM_PATH="$ZIP_PATH.sha256"
CHECKSUM="$(shasum -a 256 "$ZIP_PATH" | awk '{print $1}')"
printf '%s  %s\n' "$CHECKSUM" "$(basename "$ZIP_PATH")" > "$CHECKSUM_PATH"

ZIP_SIZE="$(wc -c < "$ZIP_PATH" | tr -d ' ')"
printf '{"version":"%s","app":"%s","zip":"%s","checksum":"%s","size_bytes":%s,"sha256":"%s"}\n' \
    "$VERSION" \
    "$APP_PATH" \
    "$ZIP_PATH" \
    "$CHECKSUM_PATH" \
    "$ZIP_SIZE" \
    "$CHECKSUM"
