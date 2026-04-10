#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ENV_FILE="${ENV_FILE:-.env.devbot}"

if [[ $# -ne 1 ]]; then
  echo "Usage: scripts/set_test_bot_webhook.sh <public-base-url>"
  echo "Example: scripts/set_test_bot_webhook.sh https://abcd-1234.ngrok-free.app"
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "$ENV_FILE not found. Create it before setting the webhook."
  exit 1
fi

set -a
source "$ENV_FILE"
set +a

if [[ -z "${TELEGRAM_BOT_TOKEN:-}" ]]; then
  echo "TELEGRAM_BOT_TOKEN is missing in $ENV_FILE"
  exit 1
fi

BASE_URL="${1%/}"

if [[ "$BASE_URL" == "https://api.trycloudflare.com" ]]; then
  echo "Refusing to set webhook to Cloudflare's API host. Pass the public tunnel URL instead."
  exit 1
fi

MAIN_RESPONSE="$(curl -fsS -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  -d "url=${BASE_URL}/webhook/telegram")"
echo "Main bot: $MAIN_RESPONSE"

if ! grep -q '"ok":true' <<<"$MAIN_RESPONSE"; then
  exit 1
fi

if [[ -n "${VOCAB_TELEGRAM_BOT_TOKEN:-}" ]]; then
  VOCAB_RESPONSE="$(curl -fsS -X POST "https://api.telegram.org/bot${VOCAB_TELEGRAM_BOT_TOKEN}/setWebhook" \
    -d "url=${BASE_URL}/webhook/telegram/vocabulary")"
  echo "Vocab bot: $VOCAB_RESPONSE"

  if ! grep -q '"ok":true' <<<"$VOCAB_RESPONSE"; then
    exit 1
  fi
fi

if [[ -n "${VOCAB_CONVERSATION_TELEGRAM_BOT_TOKEN:-}" ]]; then
  VOCAB_CONVERSATION_RESPONSE="$(
    curl -fsS -X POST "https://api.telegram.org/bot${VOCAB_CONVERSATION_TELEGRAM_BOT_TOKEN}/setWebhook" \
      -d "url=${BASE_URL}/webhook/telegram/vocabulary-conversation"
  )"
  echo "Vocab conversation bot: $VOCAB_CONVERSATION_RESPONSE"

  if ! grep -q '"ok":true' <<<"$VOCAB_CONVERSATION_RESPONSE"; then
    exit 1
  fi
fi
