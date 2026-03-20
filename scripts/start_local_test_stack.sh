#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ENV_FILE="${ENV_FILE:-.env.devbot}"
PROXY_PORT="${PROXY_PORT:-5432}"
APP_PORT="${APP_PORT:-8080}"
CLEAR_WEBHOOK_ON_EXIT="${CLEAR_WEBHOOK_ON_EXIT:-1}"
TUNNEL_WAIT_SECONDS="${TUNNEL_WAIT_SECONDS:-90}"
PUBLIC_DNS_WAIT_SECONDS="${PUBLIC_DNS_WAIT_SECONDS:-10}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "$ENV_FILE not found."
  echo "Create it from .env.devbot.example before launching the local test stack."
  exit 1
fi

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared is required. Install it with: brew install cloudflared"
  exit 1
fi

if [[ ! -x .venv/bin/uvicorn ]]; then
  echo ".venv/bin/uvicorn not found. Create the virtualenv and install dependencies first."
  exit 1
fi

set -a
source "$ENV_FILE"
set +a

if [[ -z "${TELEGRAM_BOT_TOKEN:-}" ]]; then
  echo "TELEGRAM_BOT_TOKEN is missing in $ENV_FILE"
  exit 1
fi

PROXY_PID=""
APP_PID=""
TUNNEL_PID=""
PUBLIC_URL=""

TMP_DIR="$(mktemp -d)"
APP_LOG="$TMP_DIR/app.log"
TUNNEL_LOG="$TMP_DIR/cloudflared.log"
PROXY_LOG="$TMP_DIR/cloudsql-proxy.log"
FAILED="0"

cleanup() {
  set +e
  if [[ "$CLEAR_WEBHOOK_ON_EXIT" == "1" && -n "$PUBLIC_URL" ]]; then
    ENV_FILE="$ENV_FILE" ./scripts/clear_test_bot_webhook.sh >/dev/null 2>&1
  fi
  [[ -n "$TUNNEL_PID" ]] && kill "$TUNNEL_PID" >/dev/null 2>&1
  [[ -n "$APP_PID" ]] && kill "$APP_PID" >/dev/null 2>&1
  [[ -n "$PROXY_PID" ]] && kill "$PROXY_PID" >/dev/null 2>&1
  if [[ "$FAILED" == "0" ]]; then
    rm -rf "$TMP_DIR"
  else
    echo "Debug logs kept in $TMP_DIR"
  fi
}

trap cleanup EXIT INT TERM

should_start_proxy="0"
if [[ -n "${DB_USER:-}" && -n "${DB_NAME:-}" && "${DB_HOST:-}" == "127.0.0.1" ]]; then
  should_start_proxy="1"
fi

if [[ "$should_start_proxy" == "1" ]]; then
  CONNECTION_NAME="${LOCAL_CLOUD_SQL_CONNECTION_NAME:-${INSTANCE_CONNECTION_NAME:-picflic-490614:europe-west1:picflic-database}}"
  if [[ ! -x ./src/db/cloud-sql-proxy ]]; then
    echo "./src/db/cloud-sql-proxy is missing or not executable."
    exit 1
  fi
  echo "Starting Cloud SQL proxy for $CONNECTION_NAME on port $PROXY_PORT"
  ./src/db/cloud-sql-proxy "$CONNECTION_NAME" --port "$PROXY_PORT" >"$PROXY_LOG" 2>&1 &
  PROXY_PID=$!
  sleep 2
fi

echo "Starting local API on http://127.0.0.1:$APP_PORT"
ENV_FILE="$ENV_FILE" .venv/bin/uvicorn src.api:app --host 0.0.0.0 --port "$APP_PORT" --reload >"$APP_LOG" 2>&1 &
APP_PID=$!

for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:$APP_PORT/health" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! curl -fsS "http://127.0.0.1:$APP_PORT/health" >/dev/null 2>&1; then
  echo "Local API did not become healthy."
  echo "--- app log ---"
  cat "$APP_LOG"
  exit 1
fi

echo "Starting cloudflared tunnel"
cloudflared tunnel --url "http://127.0.0.1:$APP_PORT" >"$TUNNEL_LOG" 2>&1 &
TUNNEL_PID=$!

for _ in $(seq 1 "$TUNNEL_WAIT_SECONDS"); do
  PUBLIC_URL="$(grep -Eo 'https://[-a-z0-9]+\.trycloudflare\.com' "$TUNNEL_LOG" | head -n 1 || true)"
  if [[ -n "$PUBLIC_URL" ]]; then
    break
  fi
  sleep 1
done

if [[ -z "$PUBLIC_URL" ]]; then
  FAILED="1"
  echo "Could not determine the cloudflared public URL."
  echo "--- cloudflared log ---"
  cat "$TUNNEL_LOG"
  exit 1
fi

echo "Waiting ${PUBLIC_DNS_WAIT_SECONDS}s for public DNS propagation"
sleep "$PUBLIC_DNS_WAIT_SECONDS"

echo "Setting Telegram webhook to $PUBLIC_URL/webhook/telegram"
WEBHOOK_RESPONSE="$(ENV_FILE="$ENV_FILE" ./scripts/set_test_bot_webhook.sh "$PUBLIC_URL")"
echo "$WEBHOOK_RESPONSE"
if ! grep -q '"ok":true' <<<"$WEBHOOK_RESPONSE"; then
  FAILED="1"
  echo "Telegram did not accept the webhook."
  exit 1
fi

WEBHOOK_INFO_URL="https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo"
for _ in $(seq 1 "$TUNNEL_WAIT_SECONDS"); do
  WEBHOOK_INFO="$(curl -fsS "$WEBHOOK_INFO_URL" || true)"
  if grep -q '"ok":true' <<<"$WEBHOOK_INFO" && grep -q "\"url\":\"${PUBLIC_URL//\//\\/}/webhook/telegram\"" <<<"$WEBHOOK_INFO"; then
    if ! grep -q '"pending_update_count":[1-9]' <<<"$WEBHOOK_INFO"; then
      break
    fi
  fi
  sleep 1
done

WEBHOOK_INFO="$(curl -fsS "$WEBHOOK_INFO_URL" || true)"
echo "$WEBHOOK_INFO"

echo
echo "Local test stack is running."
echo "Public URL: $PUBLIC_URL"
echo "Webhook URL: $PUBLIC_URL/webhook/telegram"
echo
echo "Logs:"
echo "  app:    $APP_LOG"
echo "  tunnel: $TUNNEL_LOG"
if [[ -n "$PROXY_PID" ]]; then
  echo "  proxy:  $PROXY_LOG"
fi
echo
echo "Press Ctrl+C to stop everything."

wait "$APP_PID"
