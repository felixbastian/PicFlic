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
   - set `PICTOAGENT_TIME_ZONE` to your local wall-clock zone, for example `Europe/Paris`
   - keep the `PicFlicBot-Dev` token in `TELEGRAM_BOT_TOKEN`
   - keep the `DevVocabTrainBot` token in `VOCAB_TELEGRAM_BOT_TOKEN`
   - keep `VOCAB_TELEGRAM_BOT_USERNAME=DevVocabTrainBot`
   - set `PICTOAGENT_REVIEW_JOB_SECRET` if you want to trigger the vocabulary review job endpoint locally
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
- sets the main and vocabulary Telegram webhooks automatically
- writes logs to `./logs/local-test-stack/<timestamp>/`
- also updates stable symlinks at:
  - `./logs/local-test-stack/app.log`
  - `./logs/local-test-stack/cloudflared.log`
  - `./logs/local-test-stack/cloudsql-proxy.log`

Press `Ctrl+C` to stop the stack again.

To watch logs while it is running:

```bash
tail -f logs/local-test-stack/app.log
```

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
   - a vocabulary review reply in `DevVocabTrainBot`

5. When you are done, remove the webhook:

   ```bash
   scripts/clear_test_bot_webhook.sh
   ```

## What this tests

This local setup uses the same webhook endpoint as production:

- `POST /webhook/telegram`
- `POST /webhook/telegram/vocabulary`
- Telegram update parsing
- photo download and analysis
- caption-to-LLM metadata forwarding
- optional PostgreSQL writes to `fact_consumption`
- vocabulary review reply handling
- daily calorie total reply
