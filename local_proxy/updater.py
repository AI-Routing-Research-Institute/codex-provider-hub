"""Online update: query GitHub Releases, compare versions, download and verify artifacts."""

from __future__ import annotations

import hashlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx


RELEASE_OWNER = "AI-Routing-Research-Institute"
RELEASE_REPO = "codex-provider-hub"
LATEST_RELEASE_API = (
    f"https://api.github.com/repos/{RELEASE_OWNER}/{RELEASE_REPO}/releases/latest"
)
RELEASES_PAGE = f"https://github.com/{RELEASE_OWNER}/{RELEASE_REPO}/releases/latest"
WINDOWS_ASSET = "CodexLocalProxy-win-x64.exe"
MACOS_ASSET = "CodexLocalProxy-macos-arm64.zip"
_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")
_USER_AGENT = "codex-provider-hub-updater"
_CHUNK_SIZE = 1 << 16


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class UpdateInfo:
    current_version: str
    latest_version: str
    has_update: bool
    release_url: str
    notes: str
    asset_name: str | None
    asset_url: str | None
    sha256_url: str | None
    published_at: str | None


def parse_version(text: str) -> tuple[int, int, int]:
    match = _VERSION_RE.search(text or "")
    if match is None:
        raise UpdateError(f"无法解析版本号：{text!r}")
    return tuple(int(part) for part in match.groups())


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


def platform_asset_name() -> str | None:
    if sys.platform == "win32":
        return WINDOWS_ASSET
    if sys.platform == "darwin":
        return MACOS_ASSET
    return None


def _select_asset(assets: list, name: str) -> str | None:
    for asset in assets:
        if isinstance(asset, dict) and asset.get("name") == name:
            url = asset.get("browser_download_url")
            return str(url) if url else None
    return None


def parse_release(
    payload: dict,
    current_version: str,
    *,
    asset_name: str | None = None,
) -> UpdateInfo:
    tag = str(payload.get("tag_name") or "").strip()
    if not tag:
        raise UpdateError("发布信息缺少 tag_name")
    latest_version = tag.lstrip("vV")
    assets = payload.get("assets") if isinstance(payload.get("assets"), list) else []
    wanted = asset_name or platform_asset_name()
    asset_url = _select_asset(assets, wanted) if wanted else None
    sha256_url = _select_asset(assets, f"{wanted}.sha256") if wanted else None
    try:
        newer = is_newer(latest_version, current_version)
    except UpdateError:
        newer = False
    return UpdateInfo(
        current_version=current_version,
        latest_version=latest_version,
        has_update=newer and asset_url is not None and sha256_url is not None,
        release_url=str(payload.get("html_url") or RELEASES_PAGE),
        notes=str(payload.get("body") or "").strip(),
        asset_name=wanted,
        asset_url=asset_url,
        sha256_url=sha256_url,
        published_at=payload.get("published_at"),
    )


def fetch_latest_release(*, timeout: float = 10.0, client=None) -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": _USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        if client is not None:
            response = client.get(LATEST_RELEASE_API, headers=headers, timeout=timeout)
        else:
            response = httpx.get(
                LATEST_RELEASE_API,
                headers=headers,
                timeout=timeout,
                follow_redirects=True,
            )
    except httpx.HTTPError as exc:
        raise UpdateError(f"检查更新失败：{exc}") from exc
    if response.status_code != 200:
        raise UpdateError(f"检查更新失败：HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise UpdateError("检查更新失败：响应不是有效 JSON") from exc
    if not isinstance(payload, dict):
        raise UpdateError("检查更新失败：响应格式异常")
    return payload


def check_for_update(
    current_version: str,
    *,
    timeout: float = 10.0,
    client=None,
    asset_name: str | None = None,
) -> UpdateInfo:
    payload = fetch_latest_release(timeout=timeout, client=client)
    return parse_release(payload, current_version, asset_name=asset_name)


def parse_sha256_document(text: str) -> str:
    parts = text.strip().split()
    token = parts[0].lower() if parts else ""
    if len(token) != 64 or any(ch not in "0123456789abcdef" for ch in token):
        raise UpdateError("SHA-256 校验文件格式不正确")
    return token


def verify_file_sha256(path: Path, expected_hex: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual.lower() != expected_hex.lower():
        raise UpdateError(f"SHA-256 校验失败：期望 {expected_hex}，实际 {actual}")
    return actual


def download_asset(
    info: UpdateInfo,
    destination_dir: Path,
    *,
    timeout: float = 120.0,
) -> Path:
    if info.asset_url is None or info.sha256_url is None or info.asset_name is None:
        raise UpdateError("当前平台没有可用的发布产物")
    destination_dir = Path(destination_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    target = destination_dir / info.asset_name
    temporary = target.with_name(target.name + ".part")
    temporary.unlink(missing_ok=True)
    try:
        with httpx.stream(
            "GET", info.asset_url, timeout=timeout, follow_redirects=True
        ) as response:
            if response.status_code != 200:
                raise UpdateError(f"下载失败：HTTP {response.status_code}")
            with open(temporary, "wb") as handle:
                for chunk in response.iter_bytes(_CHUNK_SIZE):
                    handle.write(chunk)
        sha_response = httpx.get(
            info.sha256_url, timeout=30.0, follow_redirects=True
        )
        if sha_response.status_code != 200:
            raise UpdateError(f"下载校验文件失败：HTTP {sha_response.status_code}")
        verify_file_sha256(temporary, parse_sha256_document(sha_response.text))
        target.unlink(missing_ok=True)
        temporary.replace(target)
    except httpx.HTTPError as exc:
        temporary.unlink(missing_ok=True)
        raise UpdateError(f"下载失败：{exc}") from exc
    except UpdateError:
        temporary.unlink(missing_ok=True)
        raise
    return target
