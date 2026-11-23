#!/usr/bin/env bash
# Локальный запуск дашборда.
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
mkdir -p data/inbox
exec python3 -m uvicorn app.main:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8080}" "$@"
