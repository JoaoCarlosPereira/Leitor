#!/usr/bin/env bash
set -euo pipefail
# Wrapper Linux/macOS — delega para o instalador cross-platform em Python.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if command -v python3 >/dev/null 2>&1; then
  exec python3 install.py "$@"
elif command -v python >/dev/null 2>&1; then
  exec python install.py "$@"
else
  echo "[ERR] Python não encontrado. Instale Python 3.11+." >&2
  exit 1
fi
