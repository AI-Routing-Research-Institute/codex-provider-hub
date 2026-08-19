#!/opt/codex-provider-probe/venv/bin/python
"""Restricted server-side importer used by the local SSH uploader."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

IMPORT_USER = "codex-status-import"
IMPORT_PATH = Path("/usr/local/sbin/codex-status-import-provider")
SUDOERS_PATH = Path("/etc/sudoers.d/codex-status-import")
CONFIG_PATH = Path("/etc/codex-provider-probe/providers.toml")
SECRET_ROOT = Path("/etc/codex-provider-probe/secrets")
FRAGMENT_ROOT = Path("/etc/codex-provider-probe/providers.d")
DROPIN_PATH = Path("/etc/systemd/system/codex-provider-worker.service.d/zzzzz-imported-providers.conf")
ORDER_PATH = FRAGMENT_ROOT / ".order.json"
PUBLIC_DATABASE_PATH = Path("/var/lib/codex-provider-probe/public/status.sqlite3")
LOCK_PATH = Path("/var/lib/codex-provider-probe/control/provider-import.lock")
APP_ROOT = Path("/opt/codex-provider-probe/app/provider_status")
VENV_PYTHON = Path("/opt/codex-provider-probe/venv/bin/python")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


class ImportErrorDetail(ValueError):
    pass


def _provider_block_id(block: str) -> str | None:
    match = re.search(r"(?m)^id\s*=\s*(['\"])([^'\"]+)\1\s*$", block)
    return match.group(2).strip() if match else None


def _provider_blocks(config_path: Path) -> list[tuple[str, str]]:
    if not config_path.is_file():
        return []
    text = config_path.read_text(encoding="utf-8")
    parts = re.split(r"(?m)(?=^\[\[providers\]\]\s*$)", text)
    return [(part, _provider_block_id(part) or "") for part in parts]


def _credential_from_block(block: str) -> str | None:
    match = re.search(r"(?m)^credential_name\s*=\s*(['\"])([^'\"]+)\1\s*$", block)
    return match.group(2).strip() if match else None


def _public_management_provider(item: dict[str, Any]) -> dict[str, Any]:
    models = item.get("models") if isinstance(item.get("models"), list) else []
    display_models = item.get("display_models", models)
    return {
        "id": str(item.get("id", item.get("provider_id", ""))),
        "name": str(item.get("name", "")),
        "base_url": str(item.get("base_url", "")),
        "models": list(models),
        "display_models": list(display_models) if isinstance(display_models, list) else list(models),
        "probe_mode": str(item.get("probe_mode", "automatic")),
    }


def _remove_main_provider(provider_id: str, config_path: Path) -> tuple[str | None, bool]:
    blocks = _provider_blocks(config_path)
    if not blocks:
        return None, False
    removed = False
    credential = None
    kept: list[str] = []
    for block, block_id in blocks:
        if block_id == provider_id:
            removed = True
            credential = _credential_from_block(block)
        else:
            kept.append(block)
    if removed:
        _atomic_write(config_path, "".join(kept), 0o644)
    return credential, removed


def delete_provider(
    provider_id: str,
    *,
    config_path: Path = CONFIG_PATH,
    fragment_root: Path = FRAGMENT_ROOT,
    secret_root: Path = SECRET_ROOT,
    public_database_path: Path | None = PUBLIC_DATABASE_PATH,
) -> dict[str, str]:
    if not ID_RE.fullmatch(provider_id):
        raise ImportErrorDetail("provider_id 格式无效")
    fragment_path = fragment_root / f"{provider_id}.toml"
    old_fragment = fragment_path.read_bytes() if fragment_path.exists() else None
    old_config = config_path.read_bytes() if config_path.exists() else None
    credential_name = None
    removed = False
    if fragment_path.exists():
        credential_name = _credential_from_block(fragment_path.read_text(encoding="utf-8"))
        fragment_path.unlink()
        removed = True
    else:
        credential_name, removed = _remove_main_provider(provider_id, config_path)
    if not removed:
        raise ImportErrorDetail("未找到指定供应商")
    if credential_name and Path(credential_name).name != credential_name:
        raise ImportErrorDetail("credential_name 格式无效")
    secret_path = secret_root / credential_name if credential_name else None
    old_secret = secret_path.read_bytes() if secret_path and secret_path.exists() else None
    if secret_path:
        secret_path.unlink(missing_ok=True)
    try:
        if public_database_path is not None and public_database_path.exists():
            import sqlite3
            with sqlite3.connect(public_database_path) as connection:
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("DELETE FROM providers WHERE id = ?", (provider_id,))
                connection.commit()
    except Exception:
        if old_config is None:
            config_path.unlink(missing_ok=True)
        else:
            config_path.write_bytes(old_config)
        if old_fragment is not None:
            fragment_path.write_bytes(old_fragment)
        if secret_path and old_secret is not None:
            secret_path.write_bytes(old_secret)
        raise
    return {"status": "deleted", "provider_id": provider_id}


def order_providers(provider_ids: list[str], *, fragment_root: Path = FRAGMENT_ROOT) -> dict[str, Any]:
    if len(provider_ids) != len(set(provider_ids)) or any(not isinstance(value, str) or not ID_RE.fullmatch(value) for value in provider_ids):
        raise ImportErrorDetail("provider_id 顺序列表无效")
    fragment_root.mkdir(parents=True, exist_ok=True)
    _atomic_write(fragment_root / ".order.json", json.dumps(provider_ids, ensure_ascii=False) + "\n", 0o644)
    return {"status": "ordered", "provider_ids": provider_ids}


def _delete_public_provider(provider_id: str, database_path: Path | None = None) -> None:
    database_path = database_path or PUBLIC_DATABASE_PATH
    if not database_path.exists():
        return
    import sqlite3

    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("DELETE FROM providers WHERE id = ?", (provider_id,))
        connection.commit()


def _restart_worker(*, check: bool = True) -> None:
    subprocess.run(["systemctl", "daemon-reload"], check=check)
    subprocess.run(["systemctl", "restart", "codex-provider-worker.service"], check=check)


def _restore_management_files(backups: dict[Path, bytes | None]) -> None:
    for path, content in backups.items():
        _restore_file(path, content)
    try:
        _restart_worker(check=False)
    except OSError:
        pass


def _delete_and_restart(provider_id: str) -> dict[str, str]:
    fragment_path = FRAGMENT_ROOT / f"{provider_id}.toml"
    source_path = fragment_path if fragment_path.is_file() else CONFIG_PATH
    source_text = source_path.read_text(encoding="utf-8") if source_path.is_file() else ""
    credential_name = _credential_from_block(
        next(
            (
                block
                for block, block_id in _provider_blocks(source_path)
                if block_id == provider_id
            ),
            source_text,
        )
    )
    if credential_name and Path(credential_name).name != credential_name:
        raise ImportErrorDetail("credential_name 格式无效")
    secret_path = SECRET_ROOT / credential_name if credential_name else None
    paths = [CONFIG_PATH, fragment_path, ORDER_PATH, DROPIN_PATH]
    if secret_path is not None:
        paths.append(secret_path)
    backups = {path: path.read_bytes() if path.exists() else None for path in paths}
    try:
        result = delete_provider(
            provider_id,
            config_path=CONFIG_PATH,
            fragment_root=FRAGMENT_ROOT,
            secret_root=SECRET_ROOT,
            public_database_path=None,
        )
        if ORDER_PATH.is_file():
            try:
                current_order = json.loads(ORDER_PATH.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                current_order = []
            if isinstance(current_order, list):
                order_providers(
                    [value for value in current_order if value != provider_id],
                    fragment_root=FRAGMENT_ROOT,
                )
        _write_import_dropin()
        _restart_worker()
        _delete_public_provider(provider_id)
        return result
    except Exception:
        _restore_management_files(backups)
        raise


def _order_and_restart(provider_ids: list[str]) -> dict[str, Any]:
    existing = _provider_ids(CONFIG_PATH, FRAGMENT_ROOT)
    if set(provider_ids) != existing:
        raise ImportErrorDetail("provider_id 顺序列表必须包含服务器全部供应商")
    old_order = ORDER_PATH.read_bytes() if ORDER_PATH.exists() else None
    try:
        result = order_providers(provider_ids, fragment_root=FRAGMENT_ROOT)
        _restart_worker()
        return result
    except Exception:
        _restore_management_files({ORDER_PATH: old_order})
        raise


def import_provider_fragment(
    payload: dict[str, Any],
    *,
    config_path: Path = CONFIG_PATH,
    secret_root: Path = SECRET_ROOT,
    fragment_root: Path | None = None,
) -> dict[str, str]:
    normalized = _validate_payload(payload)
    fragment_root = fragment_root or config_path.with_name("providers.d")
    try:
        existing = _provider_ids(config_path, fragment_root)
    except Exception as exc:
        raise ImportErrorDetail(f"无法读取现有状态配置：{exc}") from exc
    provider_id = normalized["provider_id"]
    if provider_id in existing:
        raise ImportErrorDetail("服务器已存在相同 provider_id，拒绝重复上传")

    credential_name = "imported_" + hashlib.sha256(provider_id.encode("utf-8")).hexdigest()[:24] + ".key"
    fragment_root.mkdir(parents=True, exist_ok=True)
    secret_root.mkdir(parents=True, exist_ok=True)
    fragment_path = fragment_root / f"{provider_id}.toml"
    secret_path = secret_root / credential_name
    if fragment_path.exists() or secret_path.exists():
        raise ImportErrorDetail("服务器已存在相同 provider_id，拒绝重复上传")
    fragment_text = _render_fragment(normalized, credential_name)
    _atomic_write(secret_path, normalized["credential"] + "\n", 0o600)
    try:
        _atomic_write(fragment_path, fragment_text, 0o644)
    except Exception:
        secret_path.unlink(missing_ok=True)
        raise
    return {"provider_id": provider_id, "fragment": str(fragment_path), "credential_name": credential_name}


def _provider_ids(config_path: Path, fragment_root: Path) -> set[str]:
    ids: set[str] = set()
    for path in (config_path, *sorted(fragment_root.glob("*.toml"))):
        if not path.is_file():
            continue
        with path.open("rb") as handle:
            raw = _load_toml(handle)
        service = raw.get("service")
        providers = raw.get("providers")
        if providers is None and isinstance(service, dict):
            providers = service.get("providers")
        if not isinstance(providers, list):
            continue
        for provider in providers:
            if isinstance(provider, dict):
                value = provider.get("id", provider.get("provider_id"))
                if isinstance(value, str) and value.strip():
                    ids.add(value.strip())
    return ids


def _validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ImportErrorDetail("上传内容必须是 JSON 对象")
    required = {"provider_id", "name", "base_url", "protocol", "models", "credential_kind", "credential"}
    if set(payload) - required - {"claude_base_url"}:
        raise ImportErrorDetail("上传内容包含未允许的字段")
    provider_id = payload.get("provider_id")
    if not isinstance(provider_id, str) or not ID_RE.fullmatch(provider_id):
        raise ImportErrorDetail("provider_id 格式无效")
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        raise ImportErrorDetail("供应商名称无效")
    protocol = payload.get("protocol")
    if protocol not in {"codex", "claude"}:
        raise ImportErrorDetail("协议必须是 codex 或 claude")
    credential_kind = payload.get("credential_kind")
    if credential_kind not in {"api_key", "auth_token"}:
        raise ImportErrorDetail("凭据类型无效")
    if protocol == "codex" and credential_kind != "api_key":
        raise ImportErrorDetail("Codex 只支持 API Key")
    credential = payload.get("credential")
    if not isinstance(credential, str) or not credential.strip() or len(credential) > 4096:
        raise ImportErrorDetail("凭据无效")
    base_url = _https_url(payload.get("base_url"), "base_url")
    models = payload.get("models")
    if not isinstance(models, list) or not models or any(
        not isinstance(model, str) or not model.strip() or len(model) > 240 for model in models
    ):
        raise ImportErrorDetail("models 必须是非空字符串数组")
    unique_models = list(dict.fromkeys(model.strip() for model in models))
    claude_base_url = None
    if protocol == "claude":
        claude_base_url = _https_url(payload.get("claude_base_url") or base_url, "claude_base_url")
    return {
        "provider_id": provider_id,
        "name": name.strip(),
        "base_url": base_url,
        "claude_base_url": claude_base_url,
        "protocol": protocol,
        "models": unique_models,
        "credential_kind": credential_kind,
        "credential": credential.strip(),
    }


def _load_toml(handle: Any) -> dict[str, Any]:
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - server runs Python 3.10
        import tomli as tomllib
    return tomllib.load(handle)


def _https_url(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.startswith("https://") or len(value) > 2048:
        raise ImportErrorDetail(f"{field} 必须是 HTTPS URL")
    return value.rstrip("/")


def _render_fragment(payload: dict[str, Any], credential_name: str) -> str:
    models = ", ".join(json.dumps(model, ensure_ascii=False) for model in payload["models"])
    lines = [
        "[[providers]]",
        f"id = {json.dumps(payload['provider_id'], ensure_ascii=False)}",
        f"name = {json.dumps(payload['name'], ensure_ascii=False)}",
        f"base_url = {json.dumps(payload['base_url'], ensure_ascii=False)}",
        f"credential_name = {json.dumps(credential_name)}",
        f"credential_kind = {json.dumps(payload['credential_kind'])}",
        "probe_mode = \"automatic\"",
        f"models = [{models}]",
        f"display_models = [{models}]",
        "healthy_interval_seconds = 600",
        "healthy_interval_max_seconds = 1200",
        "unhealthy_interval_seconds = 120",
        "unhealthy_interval_max_seconds = 300",
        "timeout_seconds = 90",
    ]
    if payload.get("protocol") == "claude":
        lines.insert(4, f"claude_base_url = {json.dumps(payload['claude_base_url'], ensure_ascii=False)}")
        mappings = ", ".join(f"{json.dumps(model)} = \"claude\"" for model in payload["models"])
        lines.append(f"model_clients = {{ {mappings} }}")
    return "\n".join(lines) + "\n"


def _atomic_write(path: Path, text: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            if hasattr(os, "fchmod"):
                os.fchmod(handle.fileno(), mode)
        if not hasattr(os, "fchmod"):
            os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _importer_script_text(source: Path) -> str:
    return source.read_bytes().decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")


def bootstrap(
    public_key: str,
    config_module: str | None = None,
    claude_probe_module: str | None = None,
) -> dict[str, str]:
    if os.geteuid() != 0:
        raise ImportErrorDetail("bootstrap 必须由 root 执行")
    if not public_key.startswith("ssh-ed25519 "):
        raise ImportErrorDetail("只支持 Ed25519 公钥")
    if not config_module or not claude_probe_module:
        raise ImportErrorDetail("bootstrap 缺少状态服务支持模块")
    support_files = (
        (Path(config_module), APP_ROOT / "config.py"),
        (Path(claude_probe_module), APP_ROOT / "claude_probe.py"),
    )
    backups = {
        target: target.read_bytes() if target.exists() else None
        for _, target in support_files
    }
    try:
        for source, target in support_files:
            if not source.is_file():
                raise ImportErrorDetail(f"bootstrap 支持文件不存在：{source.name}")
            _atomic_write(target, source.read_text(encoding="utf-8"), 0o644)
        subprocess.run(
            [str(VENV_PYTHON), "-m", "py_compile", *(str(target) for _, target in support_files)],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        subprocess.run(["systemctl", "restart", "codex-provider-worker.service"], check=True)
    except Exception:
        for _, target in support_files:
            _restore_file(target, backups[target])
        try:
            subprocess.run(["systemctl", "restart", "codex-provider-worker.service"], check=False)
        except OSError:
            pass
        raise
    _atomic_write(IMPORT_PATH, _importer_script_text(Path(__file__)), 0o755)
    import pwd

    home = Path("/var/lib") / IMPORT_USER
    try:
        pwd.getpwnam(IMPORT_USER)
    except KeyError:
        subprocess.run(
            ["useradd", "--system", "--create-home", "--home-dir", str(home), "--shell", "/bin/sh", IMPORT_USER],
            check=True,
        )
    else:
        subprocess.run(["usermod", "--home", str(home), "--shell", "/bin/sh", IMPORT_USER], check=True)
    ssh_dir = home / ".ssh"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    os.chown(home, pwd.getpwnam(IMPORT_USER).pw_uid, pwd.getpwnam(IMPORT_USER).pw_gid)
    os.chown(ssh_dir, pwd.getpwnam(IMPORT_USER).pw_uid, pwd.getpwnam(IMPORT_USER).pw_gid)
    os.chmod(ssh_dir, 0o700)
    authorized = ssh_dir / "authorized_keys"
    line = f'command="sudo -n {IMPORT_PATH} --serve",restrict {public_key}\n'
    _atomic_write(authorized, line, 0o600)
    os.chown(authorized, pwd.getpwnam(IMPORT_USER).pw_uid, pwd.getpwnam(IMPORT_USER).pw_gid)
    _atomic_write(
        SUDOERS_PATH,
        f"{IMPORT_USER} ALL=(root) NOPASSWD: {IMPORT_PATH} --serve\n",
        0o440,
    )
    subprocess.run(
        ["visudo", "-cf", str(SUDOERS_PATH)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return {"status": "initialized", "host_key_fingerprint": ""}


def serve() -> dict[str, Any]:
    payload = json.load(sys.stdin)
    action = payload.get("action") if isinstance(payload, dict) else None
    if action == "list":
        providers: list[dict[str, Any]] = []
        if CONFIG_PATH.is_file():
            with CONFIG_PATH.open("rb") as handle:
                config = _load_toml(handle)
            values = config.get("providers")
            if values is None and isinstance(config.get("service"), dict):
                values = config["service"].get("providers")
            if isinstance(values, list):
                providers.extend(value for value in values if isinstance(value, dict))
        for fragment_path in sorted(FRAGMENT_ROOT.glob("*.toml")):
            with fragment_path.open("rb") as handle:
                fragment = _load_toml(handle)
            values = fragment.get("providers")
            if isinstance(values, list):
                providers.extend(value for value in values if isinstance(value, dict))
        order = []
        if ORDER_PATH.is_file():
            try:
                raw_order = json.loads(ORDER_PATH.read_text(encoding="utf-8"))
                order = raw_order if isinstance(raw_order, list) else []
            except (OSError, json.JSONDecodeError):
                order = []
        positions = {value: index for index, value in enumerate(order) if isinstance(value, str)}
        providers.sort(
            key=lambda item: positions.get(
                str(item.get("id", item.get("provider_id", ""))),
                len(positions),
            )
        )
        public_providers = [_public_management_provider(item) for item in providers]
        return {"status": "ok", "providers": public_providers}
    import fcntl

    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if action == "delete":
            return _delete_and_restart(str(payload.get("provider_id") or ""))
        if action == "order":
            return _order_and_restart(list(payload.get("provider_ids") or []))
        return _import_and_restart(payload)


def _import_and_restart(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = _validate_payload(payload)
    provider_id = normalized["provider_id"]
    fragment_path = FRAGMENT_ROOT / f"{provider_id}.toml"
    credential_name = "imported_" + hashlib.sha256(provider_id.encode("utf-8")).hexdigest()[:24] + ".key"
    secret_path = SECRET_ROOT / credential_name
    old_fragment = fragment_path.read_bytes() if fragment_path.exists() else None
    old_secret = secret_path.read_bytes() if secret_path.exists() else None
    old_dropin = DROPIN_PATH.read_bytes() if DROPIN_PATH.exists() else None
    result = import_provider_fragment(
        normalized,
        config_path=CONFIG_PATH,
        secret_root=SECRET_ROOT,
        fragment_root=FRAGMENT_ROOT,
    )
    try:
        _write_import_dropin()
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "restart", "codex-provider-worker.service"], check=True)
    except Exception:
        _restore_file(fragment_path, old_fragment)
        _restore_file(secret_path, old_secret)
        _restore_file(DROPIN_PATH, old_dropin)
        try:
            subprocess.run(["systemctl", "daemon-reload"], check=False)
        except OSError:
            pass
        raise
    result["status"] = "imported"
    return result


def _write_import_dropin() -> None:
    lines = ["[Service]"]
    for fragment_path in sorted(FRAGMENT_ROOT.glob("*.toml")):
        with fragment_path.open("rb") as handle:
            raw = _load_toml(handle)
        providers = raw.get("providers")
        if not isinstance(providers, list):
            continue
        for provider in providers:
            if isinstance(provider, dict):
                name = provider.get("credential_name")
                if isinstance(name, str) and name:
                    lines.append(f"LoadCredential={name}:{SECRET_ROOT / name}")
    _atomic_write(DROPIN_PATH, "\n".join(lines) + "\n", 0o644)


def _restore_file(path: Path, content: bytes | None) -> None:
    if content is None:
        path.unlink(missing_ok=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bootstrap", action="store_true")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--public-key")
    parser.add_argument("--config-module")
    parser.add_argument("--claude-probe-module")
    args = parser.parse_args(argv)
    try:
        result = (
            bootstrap(args.public_key or "", args.config_module, args.claude_probe_module)
            if args.bootstrap
            else serve()
        )
    except (ValueError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "detail": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
