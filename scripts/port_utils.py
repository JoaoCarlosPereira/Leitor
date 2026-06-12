"""Utilitários para resolução de host/porta do servidor Leitor."""

from __future__ import annotations

import socket
from pathlib import Path

DEFAULT_APP_HOST = "0.0.0.0"
DEFAULT_APP_PORT = 8000
PORT_SCAN_LIMIT = 100


def is_port_available(port: int, host: str = DEFAULT_APP_HOST) -> bool:
    """Retorna True se a porta puder ser vinculada localmente."""
    bind_host = "" if host in ("0.0.0.0", "") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((bind_host, port))
            return True
        except OSError:
            return False


def find_available_port(
    start: int,
    host: str = DEFAULT_APP_HOST,
    limit: int = PORT_SCAN_LIMIT,
) -> int:
    """Busca a primeira porta livre a partir de ``start``."""
    for offset in range(limit):
        port = start + offset
        if port > 65535:
            break
        if is_port_available(port, host):
            return port
    raise RuntimeError(
        f"Nenhuma porta livre entre {start} e {min(start + limit - 1, 65535)}"
    )


def _read_env_file(env_path: Path) -> dict[str, str]:
    if not env_path.exists():
        return {}
    values: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def _write_env_value(env_path: Path, key: str, value: str) -> None:
    lines = env_path.read_text(encoding="utf-8").splitlines()
    updated: list[str] = []
    found = False
    for line in lines:
        if line.startswith(f"{key}="):
            updated.append(f"{key}={value}")
            found = True
        else:
            updated.append(line)
    if not found:
        updated.append(f"{key}={value}")
    env_path.write_text("\n".join(updated) + "\n", encoding="utf-8")


def resolve_bind_config(
    env_path: Path | None = None,
    *,
    preferred_port: int | None = None,
    preferred_host: str | None = None,
    update_env: bool = False,
) -> tuple[str, int]:
    """Resolve host/porta do Leitor, escolhendo alternativa se a porta estiver ocupada."""
    root = Path(__file__).resolve().parent.parent
    env_file = env_path or (root / ".env")
    env = _read_env_file(env_file)

    host = preferred_host or env.get("APP_HOST", DEFAULT_APP_HOST)
    start_port = preferred_port
    if start_port is None:
        start_port = int(env.get("APP_PORT", str(DEFAULT_APP_PORT)))

    resolved_port = find_available_port(start_port, host)

    if update_env and env_file.exists() and resolved_port != start_port:
        _write_env_value(env_file, "APP_PORT", str(resolved_port))

    return host, resolved_port
