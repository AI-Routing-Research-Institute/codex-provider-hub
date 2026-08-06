from __future__ import annotations

from pathlib import Path


def display_path(path: Path) -> str:
    resolved = path.expanduser().resolve()
    try:
        relative = resolved.relative_to(Path.home().resolve())
    except ValueError:
        return str(resolved)
    return "~" if not relative.parts else f"~/{relative.as_posix()}"


def resolve_user_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()
