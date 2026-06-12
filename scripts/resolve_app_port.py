#!/usr/bin/env python3
"""Imprime a porta disponível do Leitor (uso em scripts shell)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from port_utils import resolve_bind_config  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve APP_PORT do Leitor")
    parser.add_argument(
        "--update-env",
        action="store_true",
        help="Atualiza APP_PORT no .env se uma porta alternativa for usada",
    )
    parser.add_argument(
        "--preferred",
        type=int,
        default=None,
        help="Porta preferida (padrão: APP_PORT do .env ou 8000)",
    )
    args = parser.parse_args()
    _, port = resolve_bind_config(
        preferred_port=args.preferred,
        update_env=args.update_env,
    )
    print(port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
