#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ENV_FILE="${ENV_FILE:-.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "$ENV_FILE not found."
  exit 1
fi

set -a
source "$ENV_FILE"
set +a

print_info() {
  local label="$1"
  local token="$2"

  if [[ -z "$token" ]]; then
    echo "$label: token not configured"
    return 0
  fi

  local response
  response="$(curl -fsS "https://api.telegram.org/bot${token}/getWebhookInfo")"
  echo "$label: $response"
}

print_info "Main bot" "${TELEGRAM_BOT_TOKEN:-}"
print_info "Vocab bot" "${VOCAB_TELEGRAM_BOT_TOKEN:-}"
print_info "Vocab conversation bot" "${VOCAB_CONVERSATION_TELEGRAM_BOT_TOKEN:-}"
