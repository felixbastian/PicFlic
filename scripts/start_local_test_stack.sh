#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ENV_FILE="${ENV_FILE:-.env.devbot}"
PROXY_PORT="${PROXY_PORT:-5432}"
APP_PORT="${APP_PORT:-8080}"
CLEAR_WEBHOOK_ON_EXIT="${CLEAR_WEBHOOK_ON_EXIT:-1}"
TUNNEL_WAIT_SECONDS="${TUNNEL_WAIT_SECONDS:-90}"
TUNNEL_RETRIES="${TUNNEL_RETRIES:-3}"
TUNNEL_PROVIDER="${TUNNEL_PROVIDER:-auto}"
SKIP_WEBHOOK_SETUP="${SKIP_WEBHOOK_SETUP:-0}"
PUBLIC_DNS_WAIT_SECONDS="${PUBLIC_DNS_WAIT_SECONDS:-10}"
LOG_DIR="${LOG_DIR:-$ROOT_DIR/logs/local-test-stack}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "$ENV_FILE not found."
  echo "Create it from .env.devbot.example before launching the local test stack."
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
ACTIVE_TUNNEL_PROVIDER=""

mkdir -p "$LOG_DIR"

RUN_ID="$(date +%Y%m%d-%H%M%S)"
RUN_DIR="$LOG_DIR/$RUN_ID"
mkdir -p "$RUN_DIR"

APP_LOG="$RUN_DIR/app.log"
TUNNEL_LOG="$RUN_DIR/cloudflared.log"
PROXY_LOG="$RUN_DIR/cloudsql-proxy.log"
LATEST_APP_LOG="$LOG_DIR/app.log"
LATEST_TUNNEL_LOG="$LOG_DIR/cloudflared.log"
LATEST_PROXY_LOG="$LOG_DIR/cloudsql-proxy.log"
FAILED="0"

cleanup() {
  set +e
  if [[ "$CLEAR_WEBHOOK_ON_EXIT" == "1" && "$SKIP_WEBHOOK_SETUP" != "1" && -n "$PUBLIC_URL" ]]; then
    ENV_FILE="$ENV_FILE" ./scripts/clear_test_bot_webhook.sh >/dev/null 2>&1
  fi
  [[ -n "$TUNNEL_PID" ]] && kill "$TUNNEL_PID" >/dev/null 2>&1
  [[ -n "$APP_PID" ]] && kill "$APP_PID" >/dev/null 2>&1
  [[ -n "$PROXY_PID" ]] && kill "$PROXY_PID" >/dev/null 2>&1
  if [[ "$FAILED" == "1" ]]; then
    echo "Debug logs kept in $RUN_DIR"
  fi
}

trap cleanup EXIT INT TERM

has_cloudflared() {
  command -v cloudflared >/dev/null 2>&1
}

has_localtunnel() {
  command -v npx >/dev/null 2>&1
}

has_localhost_run() {
  command -v ssh >/dev/null 2>&1
}

stop_tunnel() {
  if [[ -n "$TUNNEL_PID" ]]; then
    kill "$TUNNEL_PID" >/dev/null 2>&1 || true
    wait "$TUNNEL_PID" 2>/dev/null || true
    TUNNEL_PID=""
  fi
}

extract_cloudflared_url() {
  local log_file="$1"
  grep -Eo 'https://[-a-z0-9]+\.trycloudflare\.com' "$log_file" \
    | grep -v '^https://api\.trycloudflare\.com$' \
    | head -n 1 || true
}

extract_localhost_run_url() {
  local log_file="$1"
  grep 'tunneled with tls termination' "$log_file" \
    | sed -E 's/.*(https:\/\/[^ ]+).*/\1/' \
    | tail -n 1 || true
}

extract_localtunnel_url() {
  local log_file="$1"
  grep 'your url is:' "$log_file" \
    | sed -E 's/.*(https:\/\/[^ ]+).*/\1/' \
    | tail -n 1 || true
}

wait_for_tunnel_url() {
  local provider="$1"
  local log_file="$2"
  local public_url=""

  for _ in $(seq 1 "$TUNNEL_WAIT_SECONDS"); do
    if [[ "$provider" == "cloudflared" ]]; then
      public_url="$(extract_cloudflared_url "$log_file")"
    elif [[ "$provider" == "localtunnel" ]]; then
      public_url="$(extract_localtunnel_url "$log_file")"
    else
      public_url="$(extract_localhost_run_url "$log_file")"
    fi

    if [[ -n "$public_url" ]]; then
      PUBLIC_URL="$public_url"
      return 0
    fi

    if ! kill -0 "$TUNNEL_PID" >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done

  return 1
}

start_cloudflared_tunnel() {
  : >"$TUNNEL_LOG"
  echo "Starting cloudflared tunnel"
  cloudflared tunnel --url "http://127.0.0.1:$APP_PORT" >"$TUNNEL_LOG" 2>&1 &
  TUNNEL_PID=$!
  ACTIVE_TUNNEL_PROVIDER="cloudflared"
  ln -sf "$TUNNEL_LOG" "$LATEST_TUNNEL_LOG"
  wait_for_tunnel_url "cloudflared" "$TUNNEL_LOG"
}

start_localhost_run_tunnel() {
  : >"$TUNNEL_LOG"
  echo "Starting localhost.run tunnel"
  ssh -o StrictHostKeyChecking=no \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=30 \
    -R "80:127.0.0.1:$APP_PORT" \
    nokey@localhost.run >"$TUNNEL_LOG" 2>&1 &
  TUNNEL_PID=$!
  ACTIVE_TUNNEL_PROVIDER="localhost.run"
  ln -sf "$TUNNEL_LOG" "$LATEST_TUNNEL_LOG"
  wait_for_tunnel_url "localhost.run" "$TUNNEL_LOG"
}

start_localtunnel_tunnel() {
  : >"$TUNNEL_LOG"
  echo "Starting localtunnel tunnel"
  npx --yes localtunnel --port "$APP_PORT" >"$TUNNEL_LOG" 2>&1 &
  TUNNEL_PID=$!
  ACTIVE_TUNNEL_PROVIDER="localtunnel"
  ln -sf "$TUNNEL_LOG" "$LATEST_TUNNEL_LOG"
  wait_for_tunnel_url "localtunnel" "$TUNNEL_LOG"
}

start_public_tunnel() {
  local cloudflare_attempt=0

  case "$TUNNEL_PROVIDER" in
    cloudflare)
      if ! has_cloudflared; then
        echo "cloudflared is required when TUNNEL_PROVIDER=cloudflare. Install it with: brew install cloudflared"
        return 1
      fi
      while [[ "$cloudflare_attempt" -lt "$TUNNEL_RETRIES" ]]; do
        cloudflare_attempt=$((cloudflare_attempt + 1))
        if start_cloudflared_tunnel; then
          return 0
        fi
        stop_tunnel
        echo "cloudflared quick tunnel attempt $cloudflare_attempt/$TUNNEL_RETRIES failed."
      done
      return 1
      ;;
    localtunnel)
      if ! has_localtunnel; then
        echo "npx is required when TUNNEL_PROVIDER=localtunnel."
        return 1
      fi
      start_localtunnel_tunnel
      return $?
      ;;
    localhost.run)
      if ! has_localhost_run; then
        echo "ssh is required when TUNNEL_PROVIDER=localhost.run."
        return 1
      fi
      start_localhost_run_tunnel
      return $?
      ;;
    auto)
      if has_cloudflared; then
        while [[ "$cloudflare_attempt" -lt "$TUNNEL_RETRIES" ]]; do
          cloudflare_attempt=$((cloudflare_attempt + 1))
          if start_cloudflared_tunnel; then
            return 0
          fi
          stop_tunnel
          echo "cloudflared quick tunnel attempt $cloudflare_attempt/$TUNNEL_RETRIES failed."
        done
        echo "Falling back from cloudflared because it did not produce a public URL."
      fi

      if has_localtunnel; then
        if start_localtunnel_tunnel; then
          return 0
        fi
        stop_tunnel
        echo "localtunnel fallback failed."
      fi

      echo "Falling back to localhost.run because cloudflared and localtunnel did not produce a public URL."
      if ! has_localhost_run; then
        echo "No tunnel provider is available. Install cloudflared, ensure npx is available, or ensure ssh is available."
        return 1
      fi
      start_localhost_run_tunnel
      return $?
      ;;
    *)
      echo "Unsupported TUNNEL_PROVIDER: $TUNNEL_PROVIDER"
      echo "Use one of: auto, cloudflare, localtunnel, localhost.run"
      return 1
      ;;
  esac
}

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
  ln -sf "$PROXY_LOG" "$LATEST_PROXY_LOG"
  sleep 2
fi

echo "Starting local API on http://127.0.0.1:$APP_PORT"
ENV_FILE="$ENV_FILE" .venv/bin/uvicorn src.api:app --host 0.0.0.0 --port "$APP_PORT" --reload >"$APP_LOG" 2>&1 &
APP_PID=$!
ln -sf "$APP_LOG" "$LATEST_APP_LOG"

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

if ! start_public_tunnel; then
  FAILED="1"
  echo "Could not determine the ${ACTIVE_TUNNEL_PROVIDER:-configured tunnel provider} public URL."
  echo "--- tunnel log ---"
  cat "$TUNNEL_LOG"
  exit 1
fi

if [[ -z "$PUBLIC_URL" ]]; then
  FAILED="1"
  echo "Could not determine the tunnel public URL."
  echo "--- tunnel log ---"
  cat "$TUNNEL_LOG"
  exit 1
fi

echo "Waiting ${PUBLIC_DNS_WAIT_SECONDS}s for public DNS propagation"
sleep "$PUBLIC_DNS_WAIT_SECONDS"

if ! curl -fsS "$PUBLIC_URL/health" >/dev/null 2>&1; then
  FAILED="1"
  echo "Public tunnel URL did not pass a /health check: $PUBLIC_URL"
  echo "--- tunnel log ---"
  cat "$TUNNEL_LOG"
  exit 1
fi

if [[ "$SKIP_WEBHOOK_SETUP" != "1" ]]; then
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
else
  echo "Skipping Telegram webhook setup because SKIP_WEBHOOK_SETUP=1"
fi

echo
echo "Local test stack is running."
echo "Tunnel provider: $ACTIVE_TUNNEL_PROVIDER"
echo "Public URL: $PUBLIC_URL"
if [[ "$SKIP_WEBHOOK_SETUP" != "1" ]]; then
  echo "Webhook URL: $PUBLIC_URL/webhook/telegram"
fi
echo
echo "Logs:"
echo "  app:    $APP_LOG"
echo "  tunnel: $TUNNEL_LOG"
if [[ -n "$PROXY_PID" ]]; then
  echo "  proxy:  $PROXY_LOG"
fi
echo
echo "Latest symlinks:"
echo "  app:    $LATEST_APP_LOG"
echo "  tunnel: $LATEST_TUNNEL_LOG"
if [[ -n "$PROXY_PID" ]]; then
  echo "  proxy:  $LATEST_PROXY_LOG"
fi
echo
echo "Press Ctrl+C to stop everything."

wait "$APP_PID"
