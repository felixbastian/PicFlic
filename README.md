# PictoAgent

A minimal personal agent framework for tracking daily life from photos (food, drinks, etc.) with a Telegram bot interface.

This project includes:
- A simple **agent** that takes an image path and produces a lightweight analysis of calories / macros / alcohol.
- A small **MCP-like layer** (Model Context Protocol) to read/write state from a database.
- A **langgraph-based pipeline** for the analysis.
- A **FastAPI REST API** for analyzing images and retrieving stored records.
- A **Telegram bot** for sending photos and getting analysis.
- Unit tests and a placeholder folder for sample images.

## Getting Started

1. Create a virtual environment:

   ```bash
   python3.11 -m venv .venv
   source .venv/bin/activate
   pip install -U pip
   pip install -r requirements.txt
   cp .env.example .env
   ```

2. Create a Telegram bot and get a token:

   1. Open Telegram and chat with [@BotFather](https://t.me/BotFather).
   2. Send `/start` and then `/newbot`.
   3. Follow the prompts to choose a name and username.
   4. When finished, BotFather will send a token (looks like `123456:ABC-DEF...`).

3. Configure OpenAI and Telegram credentials in `.env`:

   ```bash
   OPENAI_API_KEY=your_openai_api_key_here
   PICTOAGENT_OPENAI_MODEL=gpt-5
   PICTOAGENT_DATABASE_PATH=./data/pictoagent.db
   TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
   ```

3. Run the tests:

   ```bash
   pytest
   ```

4. Analyze an image and persist it:

   ```bash
   python3.11 -m src.main analyze sample_images/beer-pint.png
   ```

5. List stored records from the persistent database:

   ```bash
   python3.11 -m src.main list
   ```

6. Run the Telegram bot locally (polling mode):

   ```bash
   python3.11 -m src.main bot
   ```

7. Run the REST API locally:

   ```bash
   uvicorn src.api:app --reload
   ```

   The API includes a webhook endpoint for Telegram at `POST /webhook/telegram`.

## Webhook Setup (Production with Cloud Run)

For production deployment on Google Cloud Run, use webhook-based updates instead of polling:

1. Deploy the API to Cloud Run:

   ```bash
   gcloud run deploy pictoagent --source . --region us-central1 --allow-unauthenticated
   ```

2. After deployment, set the Telegram webhook to point to your Cloud Run URL:

   ```bash
   curl -X POST https://api.telegram.org/bot{TOKEN}/setWebhook \
     -d url=https://{CLOUD_RUN_URL}/webhook/telegram
   ```


   Replace `{TOKEN}` with your bot token and `{CLOUD_RUN_URL}` with your Cloud Run service URL.

3. Verify the webhook is set:

   ```bash
   curl https://api.telegram.org/bot{TOKEN}/getWebhookInfo
   ```

   If you also run the separate vocabulary bots, set their dedicated webhook routes too:

   ```bash
   curl -X POST https://api.telegram.org/bot{VOCAB_TOKEN}/setWebhook \
     -d url=https://{CLOUD_RUN_URL}/webhook/telegram/vocabulary

   curl -X POST https://api.telegram.org/bot{VOCAB_CONVERSATION_TOKEN}/setWebhook \
     -d url=https://{CLOUD_RUN_URL}/webhook/telegram/vocabulary-conversation
   ```

Now Telegram will send updates to your Cloud Run service via POST requests to the webhook endpoint.

## PostgreSQL Database (Self-hosted)

The API connects to a self-hosted PostgreSQL instance running on a GCP e2-micro VM (free tier). Set these environment variables on Cloud Run:

```bash
DB_USER=app_user
DB_PASSWORD=your_database_password
DB_NAME=app_db
DB_HOST=<vm-external-ip>
DB_PORT=5432
```

When deploying to Cloud Run:

```bash
gcloud run services update picflic-cloud-run \
  --region europe-west1 \
  --update-env-vars DB_USER=app_user,DB_NAME=app_db,DB_HOST=<vm-external-ip>,DB_PORT=5432
```

Pass `DB_PASSWORD` as a secret rather than committing it to the repo.

The VM runs PostgreSQL 14 on Ubuntu 22.04 in `us-central1-a` (GCP free tier). The schema is managed via the migration scripts in `src/db/migrations/`.

### Connecting to the database manually

SSH into the VM:

```bash
gcloud compute ssh picflic-db --zone=us-central1-a
```

Then connect to Postgres:

```bash
psql -h 127.0.0.1 -U app_user -d app_db
```

Useful queries:

```sql
-- list all tables
\dt

-- show the most recent consumption entry
SELECT * FROM fact_consumption ORDER BY created_at DESC LIMIT 1;

-- show the most recent vocabulary entry
SELECT * FROM fact_vocabulary ORDER BY created_at DESC LIMIT 1;
```

Or run a one-liner without entering the interactive shell:

```bash
psql -h 127.0.0.1 -U app_user -d app_db -c "SELECT * FROM fact_consumption ORDER BY created_at DESC LIMIT 1;"
```

## Local Dev Bot

For local end-to-end testing with a separate Telegram bot, use the workflow in [`TESTING.md`](./TESTING.md).

In short:

```bash
source .venv/bin/activate
cp .env.devbot.example .env.devbot
scripts/start_local_test_stack.sh
```

This starts the local API, a `cloudflared` tunnel, and the Telegram webhook in one go so you can test the real webhook path locally without pushing a Cloud Run deployment.
Keep `.env.devbot` on the dev-only bot tokens so local webhook tests never overwrite the production bots.

8. Call the API:

   ```bash
   curl http://127.0.0.1:8080/health
   curl -X POST http://127.0.0.1:8080/records/analyze \
     -H "Content-Type: application/json" \
     -d '{"image_path":"sample_images/beer-pint.png","metadata":{"source":"curl"}}'
   curl http://127.0.0.1:8080/records
   ```

## Project Layout

- `src/` — core library
- `tests/` — unit tests
- `sample_images/` — placeholder folder for user-supplied images

## How It Works

The `PictoAgent` runs a simple langgraph `StateGraph` pipeline:
1. Accept an image path
2. Analyze it with OpenAI using a structured `NutritionAnalysis` schema
3. Store a record in a local SQLite database via an MCP-style adapter

This is intentionally lightweight and designed to be extended.

## Persistent Storage

- The default persistent database path is `./data/pictoagent.db`.
- You can override it with `PICTOAGENT_DATABASE_PATH` in `.env`.
- The parent directory is created automatically when the database is opened.
- Unit tests should keep using `tmp_path / "records.db"` so test data never touches the persistent database.
- After installing the project, you can also use the console command `pictoagent analyze ...` or `pictoagent list`.

## Docker

Build the image:

```bash
docker build -t pictoagent .
```

Run the API with your local `.env` and persistent data directory mounted:

```bash
docker run --rm -p 8080:8080 \
  --env-file .env \
  -v "$(pwd)/data:/app/data" \
  -v "$(pwd)/sample_images:/app/sample_images" \
  pictoagent
```
