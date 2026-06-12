"""Testes para resolução dinâmica de porta do Leitor."""

from __future__ import annotations

import socket

from port_utils import find_available_port, is_port_available, resolve_bind_config


def test_is_port_available_para_porta_livre() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        _, free_port = sock.getsockname()
    assert is_port_available(free_port, "127.0.0.1")


def test_find_available_port_retorna_inicial_quando_livre() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        _, free_port = sock.getsockname()
    assert find_available_port(free_port, "127.0.0.1") == free_port


def test_find_available_port_avanca_quando_ocupada() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        busy_port = sock.getsockname()[1]
        resolved = find_available_port(busy_port, "127.0.0.1")
        assert resolved > busy_port


def test_resolve_bind_config_usa_porta_preferida(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("APP_HOST=127.0.0.1\nAPP_PORT=0\n", encoding="utf-8")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        free_port = sock.getsockname()[1]
    host, port = resolve_bind_config(env_file, preferred_port=free_port)
    assert host == "127.0.0.1"
    assert port == free_port
