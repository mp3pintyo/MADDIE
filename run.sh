#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
. .venv/bin/activate
pip install -r requirements.txt >/dev/null
PORT="${MADDIE_PORT:-8000}"
exec uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --reload
