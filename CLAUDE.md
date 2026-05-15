# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Setup:**
```bash
python3.11 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
```

**Run API locally:**
```bash
uvicorn src.api:app --reload
```

**Run full local test stack** (API + Cloud SQL proxy + cloudflared tunnel + webhook):
```bash
scripts/start_local_test_stack.sh
```

**Tests:**
```bash
pytest                          # all tests
pytest tests/unit/test_foo.py   # single test file
```
Some tests require `OPENAI_API_KEY` in the environment.

**Docker:**
```bash
docker build -t pictoagent .
docker run -p 8080:8080 --env-file .env -v "$(pwd)/data:/app/data" pictoagent
```

**CLI analysis:**
```bash
python3.11 -m src.main analyze <image_path>
```

## Architecture

PicFlic is a Telegram-first personal tracking assistant. Users send photos or text to a Telegram bot; the backend extracts structured data (nutrition, expenses, recipes, vocabulary) via OpenAI and persists it to SQLite and optionally PostgreSQL.

### Request flow

Telegram → `POST /webhook/telegram` (`src/api.py`) → bot handlers (`src/bot/`) → LangGraph agent → DB write → Telegram reply

### LangGraph workflows (`src/agents/main_agent.py`)

Two compiled state graphs:

- **Image graph**: `load → route → analyze (nutrition | expense | recipe) → store`
- **Text graph**: `load_text → route_text → (query | vocabulary | recipe_collection | …)`

The router nodes call OpenAI to classify the incoming message, then dispatch to domain-specific analysis nodes that return typed Pydantic models.

### Bot layer (`src/bot/`)

Handles all Telegram interactions: message parsing, reply formatting, inline keyboard menus, correction flows, and deletion flows. State between bot turns is tracked in SQLite via a lightweight state machine.

### Data layer

- `src/db.py` — SQLite (default, local) and PostgreSQL (warehouse, optional via `asyncpg`)
- `src/db/` — DB implementations and migrations
- `src/models/` — Pydantic schemas for all domain objects (nutrition, expense, recipe, vocabulary, query)

### Key utilities

- `src/utils.py` — OpenAI calls using the Responses API with structured JSON schemas
- `src/query_utils.py` — Natural-language query handling over stored records

### Vocabulary subsystems

- `src/vocab_bot/` — Spaced-repetition review bot dispatched by a scheduled job (`POST /jobs/vocabulary-reviews/run`)
- `src/vocab_conversation_bot/` — Conversation-based vocabulary practice

## Configuration

Copy `.env.example` to `.env` (main) or `.env.devbot.example` to `.env.devbot` (test bot).

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | — | Required for all analysis |
| `PICTOAGENT_OPENAI_MODEL` | `gpt-5.4-mini` | Model used for extraction |
| `TELEGRAM_BOT_TOKEN` | — | Main bot |
| `VOCAB_TELEGRAM_BOT_TOKEN` | — | Vocabulary review bot |
| `VOCAB_CONVERSATION_TELEGRAM_BOT_TOKEN` | — | Vocab conversation bot |
| `PICTOAGENT_DATABASE_PATH` | `./data/pictoagent.db` | SQLite path |
| `PICTOAGENT_TIME_ZONE` | `Europe/Paris` | Timestamps |
| `DB_USER/PASSWORD/NAME/HOST`, `INSTANCE_CONNECTION_NAME` | — | PostgreSQL warehouse (optional) |
| `PICTOAGENT_REVIEW_JOB_SECRET` | — | Auth for `/jobs/vocabulary-reviews/run` |
