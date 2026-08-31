from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping


CONTROL_ASSET_DIR = Path(__file__).resolve().parents[1] / "proxy_static"
CONTROL_UI_CLASSIC = "classic"
CONTROL_UI_MODERN = "modern"
CONTROL_UI_DEFAULT = CONTROL_UI_MODERN
CONTROL_UI_MODES = frozenset({CONTROL_UI_CLASSIC, CONTROL_UI_MODERN})


def normalize_control_ui(value: Any) -> str:
    return value if isinstance(value, str) and value in CONTROL_UI_MODES else CONTROL_UI_DEFAULT


def select_control_ui(
    settings: Mapping[str, Any] | None,
    override: str | None = None,
) -> str:
    if override in CONTROL_UI_MODES:
        return override
    return normalize_control_ui(settings.get("console_ui") if settings is not None else None)


def control_index_path(asset_dir: Path, console_ui: str) -> Path:
    mode = normalize_control_ui(console_ui)
    if mode == CONTROL_UI_CLASSIC:
        return asset_dir / "classic" / "index.html"
    return asset_dir / "dist" / "index.html"


def _resolve_file(root: Path, relative_path: str) -> Path | None:
    resolved_root = root.resolve()
    candidate = (resolved_root / relative_path).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def resolve_control_asset(asset_dir: Path, asset_name: str) -> Path | None:
    if asset_name in {"app.js", "styles.css"}:
        return _resolve_file(asset_dir / "classic", asset_name)
    if asset_name.startswith("assets/"):
        return _resolve_file(asset_dir / "dist" / "static", asset_name)
    return None
