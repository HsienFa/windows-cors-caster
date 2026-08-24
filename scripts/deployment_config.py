#!/usr/bin/env python3
"""Create deployment-only configuration without exposing credentials."""

from __future__ import annotations

import argparse
import configparser
import os
import re
import secrets
import sys
import tempfile
from pathlib import Path


EXAMPLE_MARKERS = (
    "replace_with_",
    "replace-",
    "change_this",
    "change-this",
    "changeme",
    "example",
    "placeholder",
    "your-secret",
    "your_password",
    "your-password",
)

KNOWN_PASSWORDS = {
    "admin",
    "admin" + "123",
    "password",
    "letmein",
}


class DeploymentConfigError(ValueError):
    """Raised when deployment credentials or paths are unsafe."""


def looks_like_example(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    return not normalized or any(marker in normalized for marker in EXAMPLE_MARKERS)


def is_secure_password(value: str) -> bool:
    password = str(value or "").strip()
    if len(password) < 16 or "\n" in password or "\r" in password:
        return False
    if looks_like_example(password) or password.lower() in KNOWN_PASSWORDS:
        return False
    return len(set(password)) >= 6


def is_secure_secret(value: str) -> bool:
    secret_value = str(value or "").strip()
    if len(secret_value) < 32 or "\n" in secret_value or "\r" in secret_value:
        return False
    if looks_like_example(secret_value):
        return False
    return len(set(secret_value)) >= 8


def generate_password() -> str:
    return secrets.token_urlsafe(24)


def generate_secret() -> str:
    return secrets.token_urlsafe(48)


def _escape_ini_value(value: object) -> str:
    return str(value).replace("%", "%%")


def _set_section(parser: configparser.ConfigParser, name: str, values: dict[str, object]) -> None:
    parser[name] = {key: _escape_ini_value(value) for key, value in values.items()}


def build_runtime_config(
    *,
    ntrip_host: str,
    web_host: str,
    ntrip_port: int,
    web_port: int,
    database_path: str,
    log_dir: str,
    admin_username: str,
    admin_password: str,
    secret_value: str,
) -> configparser.ConfigParser:
    if not re.fullmatch(r"[A-Za-z0-9_-]{2,50}", admin_username):
        raise DeploymentConfigError("管理員名稱格式不安全")
    if not is_secure_password(admin_password):
        raise DeploymentConfigError("管理員密碼缺少或不安全")
    if not is_secure_secret(secret_value):
        raise DeploymentConfigError("Flask secret 缺少或不安全")
    if ntrip_host not in {"0.0.0.0", "127.0.0.1"}:
        raise DeploymentConfigError("NTRIP 監聽位址必須明確設定為容器或本機模式")
    if web_host not in {"0.0.0.0", "127.0.0.1"}:
        raise DeploymentConfigError("監聽位址必須明確設定為容器或本機模式")
    if not (1024 <= ntrip_port <= 65535 and 1024 <= web_port <= 65535):
        raise DeploymentConfigError("服務連接埠超出允許範圍")

    parser = configparser.ConfigParser()
    _set_section(parser, "app", {
        "name": "2RTK Ntrip Caster",
        "version": "2.2.0",
        "description": "Ntrip Caster",
        "author": "2rtk",
        "contact": "i@jia.by",
        "website": "https://2rtk.com",
    })
    _set_section(parser, "caster", {
        "country": "CHN",
        "latitude": "25.20341154",
        "longitude": "110.277492",
    })
    _set_section(parser, "development", {"debug_mode": "false"})
    _set_section(parser, "network", {
        "host": ntrip_host,
        "max_connections": "5000",
        "buffer_size": "81920",
        "max_buffer_size": "655360",
    })
    _set_section(parser, "ntrip", {
        "host": ntrip_host,
        "port": ntrip_port,
        "supported_versions": "1.0,2.0",
        "default_version": "1.0",
        "max_user_connections_per_mount": "3000",
        "max_users_per_mount": "3000",
        "max_connections_per_user": "3",
        "mount_timeout": "1800",
        "client_timeout": "300",
        "connection_timeout": "1800",
    })
    _set_section(parser, "web", {
        "host": web_host,
        "port": web_port,
        "realtime_push_interval": "3",
        "system_status_interval": "1",
        "page_refresh_interval": "30",
    })
    _set_section(parser, "map", {
        "provider": "osm",
        "google_maps_api_key": "",
        "default_latitude": "23.7",
        "default_longitude": "121.0",
        "default_zoom": "7",
    })
    _set_section(parser, "database", {
        "path": database_path,
        "pool_size": "10",
        "timeout": "30",
    })
    _set_section(parser, "logging", {
        "log_dir": log_dir,
        "main_log_file": "main.log",
        "ntrip_log_file": "ntrip.log",
        "error_log_file": "errors.log",
        "log_level": "WARNING",
        "log_format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        "max_log_size": "10485760",
        "backup_count": "5",
        "log_frequent_status": "false",
    })
    _set_section(parser, "security", {
        "secret_key": secret_value,
        "password_hash_rounds": "3",
        "session_timeout": "3600",
    })
    _set_section(parser, "admin", {
        "username": admin_username,
        "password": admin_password,
    })
    _set_section(parser, "tcp", {
        "keepalive_enabled": "true",
        "keepalive_idle": "60",
        "keepalive_interval": "10",
        "keepalive_count": "3",
        "socket_timeout": "120",
    })
    _set_section(parser, "data_forwarding", {
        "ring_buffer_size": "60",
        "broadcast_interval": "0.01",
        "data_send_timeout": "5",
        "client_health_check_interval": "120",
    })
    _set_section(parser, "rtcm", {
        "parse_interval": "5",
        "buffer_size": "1000",
        "parse_duration": "30",
    })
    _set_section(parser, "websocket", {
        "ping_timeout": "120",
        "ping_interval": "15",
        "enabled": "true",
    })
    _set_section(parser, "performance", {
        "thread_pool_size": "5000",
        "max_workers": "5000",
        "connection_queue_size": "5000",
        "max_memory_usage": "2048",
        "cpu_warning_threshold": "80",
        "memory_warning_threshold": "80",
    })
    return parser


def _atomic_write(path: Path, writer) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise DeploymentConfigError("拒絕寫入符號連結設定檔")

    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        os.chmod(temporary_path, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            writer(handle)
        os.replace(temporary_path, path)
        os.chmod(path, 0o600)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def write_runtime_config(args: argparse.Namespace) -> None:
    output = Path(args.output).expanduser().resolve()
    if output.exists() and not args.force:
        raise DeploymentConfigError("設定檔已存在；不會自動覆寫")

    admin_password = os.environ.get("NTRIP_ADMIN_PASSWORD", "")
    supplied_secret = os.environ.get("NTRIP_SECRET_KEY", "").strip()
    secret_value = supplied_secret or generate_secret()
    parser = build_runtime_config(
        ntrip_host=args.ntrip_host,
        web_host=args.web_host,
        ntrip_port=args.ntrip_port,
        web_port=args.web_port,
        database_path=args.database_path,
        log_dir=args.log_dir,
        admin_username=os.environ.get("NTRIP_ADMIN_USERNAME", "admin").strip(),
        admin_password=admin_password,
        secret_value=secret_value,
    )
    _atomic_write(output, parser.write)
    print(f"安全設定檔已建立：{output}")


def _read_env_values(content: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        normalized = value.strip()
        if len(normalized) >= 2 and normalized[0] == normalized[-1] and normalized[0] in {"'", '"'}:
            normalized = normalized[1:-1]
        values[key.strip()] = normalized
    return values


def _replace_env_value(content: str, key: str, value: str) -> str:
    replacement = f"{key}={value}"
    lines = content.splitlines()
    for index, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[index] = replacement
            break
    else:
        lines.append(replacement)
    return "\n".join(lines).rstrip() + "\n"


def prepare_env(args: argparse.Namespace) -> None:
    env_path = Path(args.env_file).expanduser().resolve()
    example_path = Path(args.example).expanduser().resolve()

    if env_path.exists():
        content = env_path.read_text(encoding="utf-8")
    else:
        if not example_path.is_file():
            raise DeploymentConfigError("找不到 .env 安全範例")
        content = example_path.read_text(encoding="utf-8")

    values = _read_env_values(content)
    app_password = values.get("NTRIP_ADMIN_PASSWORD", "")
    if not is_secure_password(app_password):
        content = _replace_env_value(content, "NTRIP_ADMIN_PASSWORD", generate_password())

    if args.monitoring:
        values = _read_env_values(content)
        grafana_password = values.get("GRAFANA_ADMIN_PASSWORD", "")
        if not is_secure_password(grafana_password):
            content = _replace_env_value(content, "GRAFANA_ADMIN_PASSWORD", generate_password())

    if args.environment is not None:
        content = _replace_env_value(content, "ENVIRONMENT", args.environment)
    if args.profiles is not None:
        content = _replace_env_value(content, "COMPOSE_PROFILES", args.profiles)

    _atomic_write(env_path, lambda handle: handle.write(content))
    print(f"Docker 本機認證資料已安全儲存於：{env_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    writer = subparsers.add_parser("write-config", help="建立 Docker 或 Linux 執行設定")
    writer.add_argument("--output", required=True)
    writer.add_argument("--ntrip-host", choices=("0.0.0.0", "127.0.0.1"), required=True)
    writer.add_argument("--web-host", choices=("0.0.0.0", "127.0.0.1"), required=True)
    writer.add_argument("--ntrip-port", type=int, default=2101)
    writer.add_argument("--web-port", type=int, default=5757)
    writer.add_argument("--database-path", default="data/2rtk.db")
    writer.add_argument("--log-dir", default="logs")
    writer.add_argument("--force", action="store_true")
    writer.set_defaults(handler=write_runtime_config)

    env = subparsers.add_parser("prepare-env", help="安全建立被忽略的 Docker .env")
    env.add_argument("--env-file", required=True)
    env.add_argument("--example", required=True)
    env.add_argument("--monitoring", action="store_true")
    env.add_argument("--environment", choices=("development", "testing", "production"))
    env.add_argument("--profiles")
    env.set_defaults(handler=prepare_env)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.handler(args)
    except DeploymentConfigError as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
