# Implementation Overview

This document reflects the current codebase as inspected on 2026-03-31. It is meant to describe what is actually implemented today, even where that differs from older README notes.

## What PicFlic Is

PicFlic is a Telegram-first personal tracking assistant with a small FastAPI service behind it.

Today it supports three image domains:

- nutrition tracking from food and drink photos
- expense tracking from receipts
- recipe collection from screenshots, recipe cards, or dish ideas

It also supports several text-only workflows:

- expense questions against PostgreSQL
- nutrition questions against PostgreSQL
- French vocabulary capture and spaced review
- recipe collection from text
- plain echo for everything else

## System At A Glance

The current runtime is split into five main layers:

1. FastAPI app
   Receives Telegram webhooks, exposes a health check, and exposes a scheduled vocabulary review job.

2. Telegram bot handler layer
   Downloads photos, manages recent conversation state, formats replies, and decides when to persist to PostgreSQL.

3. `PictoAgent`
   Uses two LangGraph graphs:
   - one graph for image routing and extraction
   - one graph for text routing and structured planning

4. OpenAI structured extraction/planning
   Both image and text workflows rely on strict JSON-schema outputs from the OpenAI Responses API.

5. Storage
   - local SQLite for app-local `ImageRecord` persistence
   - PostgreSQL for user-facing warehouse data and queryable history

## Main Entry Points

The currently implemented API surface is small:

- `POST /webhook/telegram`
  Main production entry point for Telegram updates.
- `POST /jobs/vocabulary-reviews/run`
  Scheduler-triggered job that dispatches due vocabulary prompts.
- `GET /health`
  Lightweight health/config check.

There are not currently REST endpoints for generic image analysis or record listing, even though older docs still mention them.

## Main User Flows

### 1. Photo Message Flow

When a user sends a photo in Telegram:

1. FastAPI receives the Telegram webhook payload.
2. The app converts the payload into a Telegram `Update`.
3. If PostgreSQL is enabled and the update is a photo message, the app pre-resolves the warehouse user id.
4. The Telegram handler downloads the image to a temporary local file.
5. The caption, if present, is forwarded as image metadata.
6. `PictoAgent.process_image(...)` runs the image graph:
   - `load`
   - `route`
   - `analyze_nutrition` or `analyze_expense` or `analyze_recipe`
   - `store`
7. The graph stores a local `ImageRecord` in SQLite.
8. The bot optionally writes a summarized version into PostgreSQL:
   - nutrition -> `fact_consumption`
   - expense -> `fact_expenses`
   - recipe -> `fact_dishes`
9. The bot formats the reply and sends it back to Telegram.

The image graph is intentionally thin. Most domain intelligence lives in the OpenAI schema extraction functions, not inside LangGraph nodes.

### 2. Text Message Flow

When a user sends text in Telegram, the bot does not always route it straight into the generic text workflow. There is a precedence order:

1. If the user currently has a pending vocabulary review, the message is treated as the review answer.
2. Otherwise, if the message looks like a correction to the last nutrition photo result, the correction flow runs.
3. Otherwise, the normal text graph runs.

The normal text graph does this:

1. route the text into one of:
   - `echo`
   - `expense_query`
   - `nutrition_query`
   - `vocabulary`
   - `recipe_collection`
2. build a structured result for that workflow
3. execute any guarded database query if needed
4. format and send the Telegram response

### 3. Scheduled Vocabulary Review Flow

The scheduler job calls `POST /jobs/vocabulary-reviews/run` with a shared secret.

That job:

1. loads due vocabulary cards from PostgreSQL
2. sends at most one pending prompt per user
3. marks those cards as awaiting review

Later, when the user replies in Telegram, the normal message handler intercepts the answer before standard text routing and records the review result.

## Storage Model

PicFlic currently uses two storage layers with different responsibilities.

### Local SQLite

Local SQLite stores full `ImageRecord` payloads through a very small MCP-style adapter. This is primarily application-local state.

It is used for:

- saving the immediate image analysis result
- reloading a stored record by id
- updating a prior nutrition record after a correction

### PostgreSQL

PostgreSQL is the user-facing warehouse.

It is used for:

- associating Telegram users to warehouse users
- storing nutrition, expense, recipe, and vocabulary facts
- answering natural-language expense and nutrition questions
- driving spaced vocabulary review scheduling

This split is important:

- SQLite keeps the richer local record object.
- PostgreSQL keeps the durable per-user warehouse tables used by bot features.

## Deep Dive: Guarded SQL Query Flow

The text query flow is one of the more important safety boundaries in the project.

The application does not let the LLM execute arbitrary SQL directly. Instead:

1. the LLM returns a structured `SQLQueryPlan`
2. the plan includes:
   - a short explanation
   - one SQL statement
   - a response template
3. the application validates the SQL before execution
4. the query is executed with the current `user_id` bound as `$1`

The validator enforces several guardrails:

- only one statement
- no comments
- read-only `SELECT` shape
- `user_id = $1` must be present
- only the allowed fact table may be referenced

So the LLM is being used as a planner, but the application still owns the execution boundary.

## Deep Dive: Nutrition Correction Flow

Nutrition correction is more subtle than a normal follow-up message.

After a nutrition photo is processed, the bot stores the last result in Telegram `user_data`. If the next text looks like a correction, the app:

1. asks the LLM whether the new text is actually a correction
2. if yes, asks for a fully revised `NutritionAnalysis`
3. preserves or updates `item_count` based on the correction text
4. rescales calories, macros, and alcohol units to the effective count
5. updates the local SQLite record
6. updates PostgreSQL as well if a warehouse `meal_id` exists

This is what lets a user say something like "actually it was just one mini pizza" after a photo has already been tracked.

## Deep Dive: Vocabulary Review Loop

Vocabulary is not only a one-off translation feature. It already has a review lifecycle.

The implemented flow is:

1. user sends a French word or short phrase
2. the app explains it and optionally stores it in `fact_vocabulary`
3. a scheduled job later finds due cards
4. the bot sends a review prompt
5. the next incoming message for that user is treated as the answer
6. the answer advances, repeats, or shelves the card

The review logic is intentionally tolerant:

- case-insensitive
- accent-insensitive
- small spelling mistakes can still count as correct

## Logging And Observability

The app uses structured JSON logging with request-scoped context:

- process id
- user id
- Telegram user id
- update id
- action
- workflow

This makes it possible to follow a single webhook or review dispatch across multiple layers without manually correlating plain-text logs.

## Current Boundaries And Caveats

The current implementation has a few important boundaries to keep in mind:

- The bot experience is more complete than the generic REST API surface.
- PostgreSQL is optional for startup, but several bot features degrade without it:
  - text query workflows
  - recipe collection persistence from text
  - vocabulary persistence and review scheduling
- OpenAI is required for all routing, extraction, correction, and planning workflows.
- The current code is the source of truth; some older docs still describe endpoints and entrypoints that are no longer present.

## Recommended Reading Order

If you want a quick mental model, read the code in roughly this order:

1. `src/api.py`
2. `src/bot.py`
3. `src/agent.py`
4. `src/utils.py`
5. `src/query_utils.py`
6. `src/db.py`
7. `src/models/*`

For lower-level notes, caveats, and implementation quirks, see `implementation_details.md`.
