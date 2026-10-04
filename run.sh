#!/usr/bin/env bash
# K9X Inspector — run locally on INSPECTOR_PORT (default 8115).
set -euo pipefail
cd "$(dirname "$0")"
[[ -f .env ]] || { echo "Copy .env.example to .env and edit it first."; exit 1; }
PY=python3
[[ -x .venv/bin/python ]] && PY=.venv/bin/python
"$PY" -c 'import k9_aif_abb.k9_inspect' 2>/dev/null || {
  echo "k9-aif in $("$PY" -c 'import sys; print(sys.prefix)') has no k9_inspect (needs >= 1.15)."
  echo "Run: python3.11 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt"
  exit 1; }
port=$(grep -E '^INSPECTOR_PORT=' .env | tail -1 | cut -d= -f2)
exec "$PY" -m uvicorn inspector.api:app --host 0.0.0.0 --port "${port:-8115}"
