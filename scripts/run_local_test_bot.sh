#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ENV_FILE="${ENV_FILE:-.env.devbot}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "$ENV_FILE not found. Create it before launching the local test bot."
  exit 1
fi

set -a
source "$ENV_FILE"
set +a

exec .venv/bin/uvicorn src.api:app --host 0.0.0.0 --port 8080 --reload
