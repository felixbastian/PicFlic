#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ENV_FILE="${ENV_FILE:-.env.devbot}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "$ENV_FILE not found. Create it before clearing the webhook."
  exit 1
fi

set -a
source "$ENV_FILE"
set +a

if [[ -z "${TELEGRAM_BOT_TOKEN:-}" ]]; then
  echo "TELEGRAM_BOT_TOKEN is missing in $ENV_FILE"
  exit 1
fi

MAIN_RESPONSE="$(curl -fsS -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/deleteWebhook")"
echo "Main bot: $MAIN_RESPONSE"

if [[ -n "${VOCAB_TELEGRAM_BOT_TOKEN:-}" ]]; then
  VOCAB_RESPONSE="$(curl -fsS -X POST "https://api.telegram.org/bot${VOCAB_TELEGRAM_BOT_TOKEN}/deleteWebhook")"
  echo "Vocab bot: $VOCAB_RESPONSE"
fi
