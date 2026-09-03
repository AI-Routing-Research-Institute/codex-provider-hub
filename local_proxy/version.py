from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path


VERSION_TAG_RE = re.compile(r"^v(\d+\.\d+\.\d+)$")
CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def resolve_app_version(
    fallback: str,
    *,
    frozen: bool | None = None,
    repository: Path | None = None,
    runner: CommandRunner = subprocess.run,
) -> str:
    """Resolve a source checkout version without weakening packaged builds."""
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if is_frozen:
        return fallback

    repository = repository or Path(__file__).resolve().parents[1]
    command: Sequence[str] = (
        "git",
        "describe",
        "--tags",
        "--match",
        "v[0-9]*",
        "--abbrev=0",
        "HEAD",
    )
    options: dict[str, object] = {
        "cwd": repository,
        "check": True,
        "capture_output": True,
        "text": True,
        "timeout": 2,
    }
    if sys.platform == "win32":
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = runner(command, **options)
    except (OSError, subprocess.SubprocessError):
        return fallback

    match = VERSION_TAG_RE.fullmatch(result.stdout.strip())
    return match.group(1) if match is not None else fallback
