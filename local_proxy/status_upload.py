"""Securely export local providers to the remote status service over SSH."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import shlex
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping

from local_proxy.core import ProxyProvider
from local_proxy.shared_settings import data_directory


DEFAULT_HOST = "118.195.178.173"
DEFAULT_PORT = 22
DEFAULT_USER = "ubuntu"
IMPORT_USER = "codex-status-import"
DEFAULT_STATUS_URL = f"http://{DEFAULT_HOST}/codex-status/"
SETTINGS_VERSION = 1


class StatusUploadError(RuntimeError):
    pass


@dataclass(frozen=True)
class StatusUploadSettings:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    username: str = DEFAULT_USER
    status_url: str = DEFAULT_STATUS_URL
    host_key: str | None = None
    private_key_path: str | None = None
    initialized: bool = False


def settings_path(root: Path | None = None) -> Path:
    return (root or data_directory()) / "status-upload.json"


def _read_settings(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def load_settings(path: Path | None = None) -> StatusUploadSettings:
    value = _read_settings(path or settings_path())
    host = value.get("host") if isinstance(value.get("host"), str) else DEFAULT_HOST
    port = value.get("port")
    username = value.get("username") if isinstance(value.get("username"), str) else DEFAULT_USER
    status_url = value.get("status_url") if isinstance(value.get("status_url"), str) else f"http://{host}/codex-status/"
    return StatusUploadSettings(
        host=host.strip() or DEFAULT_HOST,
        port=port if isinstance(port, int) and 1 <= port <= 65535 else DEFAULT_PORT,
        username=username.strip() or DEFAULT_USER,
        status_url=status_url.strip() or f"http://{host}/codex-status/",
        host_key=value.get("host_key") if isinstance(value.get("host_key"), str) else None,
        private_key_path=value.get("private_key_path") if isinstance(value.get("private_key_path"), str) else None,
        initialized=value.get("initialized") is True,
    )


def save_settings(settings: StatusUploadSettings, path: Path | None = None) -> None:
    target = path or settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            {
                "schema_version": SETTINGS_VERSION,
                "host": settings.host,
                "port": settings.port,
                "username": settings.username,
                "status_url": settings.status_url,
                "host_key": settings.host_key,
                "private_key_path": settings.private_key_path,
                "initialized": settings.initialized,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    temporary.replace(target)


def _suggested_models(provider: ProxyProvider, service_id: str, recent_models: tuple[str, ...]) -> list[str]:
    defaults = getattr(provider, "default_models", {})
    values: list[str] = []
    if isinstance(defaults, Mapping):
        values.extend(str(value).strip() for value in defaults.values() if str(value).strip())
    values.extend(str(value).strip() for value in recent_models if str(value).strip())
    if service_id == "codex" and not values:
        values.extend(("gpt-5.6-sol", "gpt-5.5"))
    elif service_id == "claude" and not values:
        values.extend(("claude-sonnet-4-5", "claude-opus-4-1"))
    return list(dict.fromkeys(values))


def provider_upload_preview(
    provider: ProxyProvider,
    service_id: str,
    recent_models: tuple[str, ...] = (),
) -> dict[str, Any]:
    if service_id not in {"codex", "claude"}:
        return {"supported": False, "reason": "未知协议"}
    if provider.configured_headers:
        return {"supported": False, "reason": "该供应商使用自定义请求头，暂不支持上传"}
    if not provider.api_key:
        return {"supported": False, "reason": "没有可上传的标准 API Key 或 Auth Token"}
    if service_id == "claude" and getattr(provider, "compatible", True) is not True:
        return {"supported": False, "reason": "该供应商不是 Anthropic Messages 协议"}
    models = _suggested_models(provider, service_id, recent_models)
    return {"supported": True, "suggested_models": models, "protocol": service_id}


def build_provider_upload_payload(
    provider: ProxyProvider,
    service_id: str,
    models: list[str] | tuple[str, ...],
) -> dict[str, Any]:
    preview = provider_upload_preview(provider, service_id, tuple(models))
    if preview.get("supported") is not True:
        raise StatusUploadError(str(preview.get("reason") or "供应商不支持上传"))
    selected = list(dict.fromkeys(str(model).strip() for model in models if str(model).strip()))
    if not selected:
        raise StatusUploadError("至少选择一个检测模型")
    credential_kind = str(getattr(provider, "credential_kind", "api_key"))
    if credential_kind not in {"api_key", "auth_token"}:
        credential_kind = "api_key"
    payload: dict[str, Any] = {
        "provider_id": provider.provider_id,
        "name": provider.name,
        "base_url": provider.base_url,
        "protocol": service_id,
        "models": selected,
        "credential_kind": credential_kind,
        "credential": provider.api_key,
    }
    if service_id == "claude":
        payload["claude_base_url"] = provider.base_url
    return payload


class StatusUploadManager:
    """Owns SSH setup and upload operations; network calls run off the event loop."""

    def __init__(
        self,
        *,
        path: Path | None = None,
        ssh_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.path = path or settings_path()
        self._ssh_factory = ssh_factory
        self._lock = threading.RLock()

    def public_settings(self) -> dict[str, Any]:
        value = load_settings(self.path)
        return {
            "host": value.host,
            "port": value.port,
            "username": value.username,
            "status_url": value.status_url,
            "initialized": value.initialized,
            "host_key_fingerprint": value.host_key,
        }

    def _transport(self, settings: StatusUploadSettings, password: str | None = None):
        if self._ssh_factory is not None:
            return self._ssh_factory(settings, password)
        try:
            import paramiko
        except ImportError as exc:  # pragma: no cover - packaging failure
            raise StatusUploadError("当前安装包缺少 SSH 依赖 paramiko") from exc
        return _ParamikoTransport(settings, password)

    def bootstrap(self, host: str, port: int, username: str, password: str) -> dict[str, Any]:
        with self._lock:
            return self._bootstrap(host, port, username, password)

    def _bootstrap(self, host: str, port: int, username: str, password: str) -> dict[str, Any]:
        if not password:
            raise StatusUploadError("首次初始化需要服务器密码")
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise StatusUploadError("SSH 端口无效")
        target = StatusUploadSettings(
            host=host.strip() or DEFAULT_HOST,
            port=port,
            username=username.strip() or DEFAULT_USER,
            status_url=f"http://{host.strip() or DEFAULT_HOST}/codex-status/",
        )
        key_path = self.path.parent / "status-upload-ed25519"
        key_path.parent.mkdir(parents=True, exist_ok=True)
        public_key, private_key = _generate_keypair()
        with self._transport(target, password) as transport:
            host_key = transport.bootstrap(public_key)
        updated = StatusUploadSettings(
            host=target.host,
            port=target.port,
            username=target.username,
            status_url=target.status_url,
            host_key=host_key,
            private_key_path=str(key_path),
            initialized=True,
        )
        temporary_key = key_path.with_suffix(".tmp")
        old_key = key_path.read_bytes() if key_path.exists() else None
        try:
            temporary_key.write_text(private_key, encoding="utf-8")
            try:
                os.chmod(temporary_key, 0o600)
            except OSError:
                pass
            temporary_key.replace(key_path)
            save_settings(updated, self.path)
        except Exception:
            temporary_key.unlink(missing_ok=True)
            if old_key is None:
                key_path.unlink(missing_ok=True)
            else:
                key_path.write_bytes(old_key)
            raise
        return self.public_settings()

    def upload(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            settings = load_settings(self.path)
            if not settings.initialized or not settings.private_key_path:
                raise StatusUploadError("请先完成服务器 SSH 初始化")
            with self._transport(replace(settings, username=IMPORT_USER)) as transport:
                result = dict(transport.upload(payload))
            result.setdefault("status_url", settings.status_url)
            return result

    def manage(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            settings = load_settings(self.path)
            if not settings.initialized or not settings.private_key_path:
                raise StatusUploadError("请先完成服务器 SSH 初始化")
            with self._transport(replace(settings, username=IMPORT_USER)) as transport:
                result = dict(transport.manage(payload))
            result.setdefault("status_url", settings.status_url)
            return result


def _generate_keypair() -> tuple[str, str]:
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except ImportError as exc:  # pragma: no cover - packaging failure
        raise StatusUploadError("当前安装包缺少 Ed25519 依赖") from exc
    private = ed25519.Ed25519PrivateKey.generate()
    private_bytes = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.OpenSSH,
        serialization.NoEncryption(),
    )
    public_bytes = private.public_key().public_bytes(
        serialization.Encoding.OpenSSH,
        serialization.PublicFormat.OpenSSH,
    )
    return public_bytes.decode("ascii") + " codex-provider-hub", private_bytes.decode("ascii")


class _ParamikoTransport:
    def __init__(self, settings: StatusUploadSettings, password: str | None) -> None:
        self.settings = settings
        self.password = password
        self.client = None

    def __enter__(self):
        import paramiko

        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs: dict[str, Any] = {
            "hostname": self.settings.host,
            "port": self.settings.port,
            "username": self.settings.username,
            "look_for_keys": not bool(self.password),
            "allow_agent": not bool(self.password),
            "timeout": 20,
        }
        if self.password:
            kwargs["password"] = self.password
        elif self.settings.private_key_path:
            kwargs["key_filename"] = self.settings.private_key_path
        self.client.connect(**kwargs)
        remote_key = self.client.get_transport().get_remote_server_key()
        fingerprint = _host_key_fingerprint(remote_key)
        if self.settings.host_key and fingerprint != self.settings.host_key:
            self.client.close()
            raise StatusUploadError("SSH 主机指纹与首次初始化记录不一致，已拒绝连接")
        self._fingerprint = fingerprint
        return self

    def __exit__(self, *_exc: Any) -> None:
        if self.client is not None:
            self.client.close()

    def bootstrap(self, public_key: str) -> str:
        if self.client is None:
            raise StatusUploadError("SSH 未连接")
        root = Path(__file__).resolve().parents[1]
        script_path = root / "scripts" / "status_provider_import.py"
        config_path = _support_module(root, "config.py")
        claude_probe_path = _support_module(root, "claude_probe.py")
        sftp = self.client.open_sftp()
        nonce = secrets.token_hex(8)
        remote_script = f"/tmp/codex-status-bootstrap-{nonce}.py"
        remote_config = f"/tmp/codex-status-config-{nonce}.py"
        remote_claude_probe = f"/tmp/codex-status-claude-probe-{nonce}.py"
        remote_paths = (remote_script, remote_config, remote_claude_probe)
        try:
            sftp.put(str(script_path), remote_script)
            sftp.put(str(config_path), remote_config)
            sftp.put(str(claude_probe_path), remote_claude_probe)
            command = (
                f"sudo -S /usr/bin/python3 {remote_script} --bootstrap "
                f"--public-key {shlex.quote(public_key)} "
                f"--config-module {shlex.quote(remote_config)} "
                f"--claude-probe-module {shlex.quote(remote_claude_probe)}"
            )
            self._run(command, password=self.password)
        finally:
            for remote_path in remote_paths:
                try:
                    sftp.remove(remote_path)
                except OSError:
                    pass
            sftp.close()
        return self._fingerprint

    def upload(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.client is None:
            raise StatusUploadError("SSH 未连接")
        return self._run("codex-status-import-provider", json.dumps(payload, ensure_ascii=False))

    def manage(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.client is None:
            raise StatusUploadError("SSH 未连接")
        return self._run("codex-status-import-provider", json.dumps(payload, ensure_ascii=False))

    def _run(self, command: str, stdin_text: str = "", password: str | None = None) -> dict[str, Any]:
        stdin, stdout, stderr = self.client.exec_command(command, timeout=90)
        if password:
            stdin.write(password + "\n")
        if stdin_text:
            stdin.write(stdin_text)
        stdin.flush()
        shutdown_write = getattr(getattr(stdin, "channel", None), "shutdown_write", None)
        if callable(shutdown_write):
            shutdown_write()
        raw = stdout.read().decode("utf-8", "replace")
        error = stderr.read().decode("utf-8", "replace").strip()
        status = stdout.channel.recv_exit_status()
        if status != 0:
            try:
                detail = json.loads(raw).get("detail")
            except (json.JSONDecodeError, AttributeError):
                detail = None
            raise StatusUploadError(str(detail or error or raw.strip() or "远程导入命令失败"))
        result: Any = None
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            # Bootstrap commands can emit a successful tool diagnostic before
            # their final JSON response (for example, `visudo -c`).
            for line in reversed(raw.splitlines()):
                try:
                    result = json.loads(line)
                except json.JSONDecodeError:
                    continue
                break
        if result is None:
            raise StatusUploadError("远程导入命令返回了无效结果")
        if not isinstance(result, dict):
            raise StatusUploadError("远程导入命令返回格式无效")
        return result


def _host_key_fingerprint(key: Any) -> str:
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def _support_module(root: Path, name: str) -> Path:
    for candidate in (root / "status_bootstrap" / name, root / "provider_status" / name):
        if candidate.is_file():
            return candidate
    raise StatusUploadError(f"安装包缺少状态服务支持文件：{name}")
