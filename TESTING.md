# Local Test Bot

Use the dedicated Telegram test bot locally so you can exercise the webhook flow without deploying Cloud Run.

## Files

- `.env.devbot`
  - local-only environment file for the Telegram test bot
- `scripts/run_local_test_bot.sh`
  - starts the FastAPI webhook server on `http://127.0.0.1:8080`
- `scripts/start_local_test_stack.sh`
  - one-command launcher for Cloud SQL proxy, local API, `cloudflared`, and webhook setup
- `scripts/set_test_bot_webhook.sh`
  - points the Telegram test bot at your public tunnel URL
- `scripts/clear_test_bot_webhook.sh`
  - removes the webhook when you are done

## Quick start

1. Activate the virtualenv:

   ```bash
   source .venv/bin/activate
   ```

2. Create `.env.devbot` from the example and fill it in:

   ```bash
   cp .env.devbot.example .env.devbot
   ```

   - set `OPENAI_API_KEY`
   - keep the dev bot token in `TELEGRAM_BOT_TOKEN`
   - if you want local `fact_consumption` writes, also set:
     - `DB_USER`
     - `DB_PASSWORD`
     - `DB_NAME`
     - `DB_HOST`
     - `DB_PORT`

3. Start everything with one command:

   ```bash
   scripts/start_local_test_stack.sh
   ```

That script:

- starts the Cloud SQL Auth Proxy when your local DB settings point at `127.0.0.1`
- starts the local FastAPI app
- starts `cloudflared`
- waits until the public `/health` endpoint is reachable
- reads the public URL
- sets the Telegram webhook automatically

Press `Ctrl+C` to stop the stack again.

## Manual fallback

If you want to run the pieces separately instead, use the steps below.

1. Start the local webhook server:

   ```bash
   ./src/db/cloud-sql-proxy picflic-490614:europe-west1:picflic-database --port 5432
   ```

   ```bash
   scripts/run_local_test_bot.sh
   ```
   

2. Expose port `8080` publicly with one of these:

   ```bash
   cloudflared tunnel --url http://127.0.0.1:8080
   ```

3. Point the dev bot webhook to that public URL:

   ```bash
   scripts/set_test_bot_webhook.sh https://your-public-url
   ```

4. Open Telegram and send the dev bot:

   - a photo
   - a photo with a caption
   - a plain text message

5. When you are done, remove the webhook:

   ```bash
   scripts/clear_test_bot_webhook.sh
   ```

## What this tests

This local setup uses the same webhook endpoint as production:

- `POST /webhook/telegram`
- Telegram update parsing
- photo download and analysis
- caption-to-LLM metadata forwarding
- optional PostgreSQL writes to `fact_consumption`
- daily calorie total reply
