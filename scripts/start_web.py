#!/usr/bin/env python3
"""Inicia o servidor web do Leitor com porta dinâmica se necessário."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from port_utils import resolve_bind_config  # noqa: E402


def main() -> int:
    host, port = resolve_bind_config(update_env=True)
    print(f"Leitor — http://{host}:{port}")
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                host,
                "--port",
                str(port),
                "--workers",
                "1",
                "--access-log",
            ],
            cwd=ROOT,
            check=True,
        )
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
