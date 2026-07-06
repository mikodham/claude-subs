#!/usr/bin/env bash
# Thin launcher for subs.py. Resolves python3 and execs the engine.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo '{"error":{"type":"no_python","message":"python3 not found on PATH"}}'
  exit 1
fi

exec "$PY" "${HERE}/subs.py" "$@"
