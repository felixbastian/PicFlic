# Implementation Details

This is a more granular working notes document derived from the current codebase on 2026-03-31. It is intentionally closer to engineering notes than polished documentation.

## Intent

Use this file for:

- lower-level architecture notes
- implementation quirks
- storage caveats
- code/documentation mismatches
- future cleanup candidates

## Module Map

### Core Runtime

- `src/api.py`
  FastAPI app, lifespan startup/shutdown, Telegram webhook entrypoint, vocabulary review job endpoint, health endpoint.

- `src/bot.py`
  Telegram handlers, reply formatting, persistence orchestration, recent-history memory, nutrition correction handoff, vocabulary review dispatch.

- `src/agent.py`
  `PictoAgent`, image graph, text graph, routing nodes, storage node, record update helper.

- `src/utils.py`
  Image-domain LLM helpers: image routing, nutrition analysis, expense extraction, recipe extraction, nutrition correction, item-count normalization.

- `src/query_utils.py`
  Text-domain LLM helpers: text routing, SQL planning, vocabulary response generation, recipe collection response generation.

- `src/db.py`
  SQLite wrapper, PostgreSQL wrapper, query validation, fact-table writes, vocabulary review state transitions.

- `src/mcp.py`
  Tiny SQLite-backed MCP-like abstraction over a `mcp_context` key/value table.

- `src/logging_config.py`
  JSON logger formatter and request-context filter.

- `src/logging_context.py`
  `contextvars` helpers for request-scoped log metadata.

### Models

- `src/models/nutrition.py`
  `NutritionAnalysis`, ingredient estimates, correction result.

- `src/models/expense.py`
  expense categories and `ExpenseAnalysis`.

- `src/models/recipe.py`
  recipe collection schema.

- `src/models/query.py`
  text workflow routing and SQL plan models.

- `src/models/vocabulary.py`
  vocabulary workflow result, due review model, review result model.

- `src/models/records.py`
  local `ImageRecord` dataclass and serialization/deserialization behavior.

### Tests

- `tests/unit/test_agent.py`
  graph routing and local persistence behavior.

- `tests/unit/test_bot.py`
  Telegram message handling, persistence branching, formatting, review flow, recent-history behavior.

- `tests/unit/test_db.py`
  PostgreSQL wrapper behavior and guarded query validation.

- `tests/unit/test_utils.py`
  schema shape, nutrition prompt behavior, item-count extraction, correction scaling.

- `tests/unit/test_agent_workflow.py`
  OpenAI-backed end-to-end workflow tests gated on `OPENAI_API_KEY`.

## Runtime Bootstrap Notes

### FastAPI Lifespan

`src/api.py` does the following on startup:

1. configure structured logging
2. load config
3. create the default `PictoAgent`
4. optionally connect PostgreSQL if warehouse config is present
5. optionally create and initialize the Telegram application if a token is present

Two globals are used:

- `_bot_application`
- `_db`

On shutdown:

- the Telegram application is stopped if initialized
- PostgreSQL is disconnected if connected

### Config Shape

`load_config()` reads:

- `OPENAI_API_KEY`
- `PICTOAGENT_OPENAI_MODEL`
- `PICTOAGENT_DATABASE_PATH`
- `TELEGRAM_BOT_TOKEN`
- `DB_USER`
- `DB_PASSWORD`
- `DB_NAME`
- `DB_HOST`
- `DB_PORT`
- `INSTANCE_CONNECTION_NAME`
- `PICTOAGENT_REVIEW_JOB_SECRET`

`postgres_enabled` is true only when:

- `db_user` exists
- `db_name` exists
- and either `db_host` or `instance_connection_name` exists

If `instance_connection_name` is set, PostgreSQL host is rewritten to `/cloudsql/<instance_connection_name>`.

## Image Workflow Details

### Image Graph Shape

The image graph in `PictoAgent` is:

1. `load`
2. `route`
3. one of:
   - `analyze_nutrition`
   - `analyze_expense`
   - `analyze_recipe`
4. `store`

State fields:

- `image_path`
- `metadata`
- `task_type`
- `analysis`
- `record_id`

### Routing

Image routing is LLM-based, not rule-based.

The current top-level choices are:

- `nutrition`
- `expense`
- `recipe`

Important routing hint:

- recipe routing is strongly favored when metadata says things like "add this to the recipes" or "add this to the collection"

### Nutrition-Specific Notes

Nutrition extraction has a few extra steps beyond plain schema extraction:

- it strips user-note metadata keys out of the generic metadata payload
- it tries to infer `item_count` from phrases like:
  - `3 x`
  - `x 3`
  - `3 of those`
  - `3 times`
- it sanitizes the remaining caption before sending it to the LLM
- it rescales calories, macros, and alcohol units after extraction if `item_count > 1`

Important nuance:

- ingredient-level amounts and ingredient-level calories stay scoped to one item
- top-level calories/macros/alcohol are rescaled to the effective number of items

### Nutrition Correction Notes

Correction flow in `src.utils.correct_nutrition_analysis(...)`:

1. ask the LLM whether a follow-up text is a correction
2. if yes, get a fully revised `NutritionAnalysis`
3. resolve the effective `item_count`
4. infer whether the revised analysis already represents one item or multiple items
5. rescale totals accordingly

Resolution order for `item_count`:

1. explicit count in correction text
2. count already returned by the corrected analysis
3. previous analysis count
4. fallback to `1`

### Expense And Recipe Notes

- expense extraction is schema-based and limited to a fixed category set
- recipe extraction supports both image-based and text-based collection flows
- recipe storage in PostgreSQL writes into `fact_dishes`

## Text Workflow Details

### Message Handling Precedence In Telegram

This order matters and is easy to forget:

1. pending vocabulary review answer
2. nutrition correction attempt
3. standard text graph

So not every incoming text goes through `agent.process_text(...)`.

### Text Graph Shape

The text graph is:

1. `load_text`
2. `route_text`
3. one of:
   - `build_expense_text_query`
   - `build_nutrition_text_query`
   - `build_vocabulary_text_response`
   - `build_recipe_collection_text_response`
   - `echo_text`

Current workflow types:

- `echo`
- `expense_query`
- `nutrition_query`
- `vocabulary`
- `recipe_collection`

### Recent History Memory

Telegram `user_data` stores short recent conversation context under `_picflic_recent_messages`.

Current limit:

- 3 history items total, not 3 full turns

Stored shape:

- `role`
- `text`
- optional `workflow`

This memory is mainly used to help the LLM interpret vocabulary follow-ups and nutrition corrections.

### Query Planning And Execution

Query workflow behavior:

1. LLM builds a `SQLQueryPlan`
2. the app sends the plan explanation to the user first
3. the app validates the SQL
4. the app executes the query against PostgreSQL
5. the app formats either:
   - a single-row response
   - or a compact multi-row breakdown

Allowed table mapping:

- `expense_query` -> `fact_expenses`
- `nutrition_query` -> `fact_consumption`

## Storage Details

### Local SQLite

The local SQLite adapter stores data in:

- table: `mcp_context`
- columns:
  - `key TEXT PRIMARY KEY`
  - `value TEXT NOT NULL`

The `DatabaseMCPAdapter` stores each `ImageRecord` as a JSON blob keyed by the record id.

### `ImageRecord` Serialization Quirk

`ImageRecord.to_dict()` intentionally drops `item_count` from nutrition analyses before storing the JSON payload.

Result:

- a nutrition record may have totals that reflect multiple items
- but when reloaded from SQLite, `item_count` falls back to `1`

This behavior is covered by tests and appears intentional right now, but it is easy to misread later because the stored total calories can represent multiple items while the restored object says `item_count == 1`.

### Temporary File Path Caveat

For Telegram photos, the image is downloaded to a temp file, processed, stored in SQLite, and then deleted.

That means local `ImageRecord.image_path` for Telegram-originated photos will usually point at a file that no longer exists after the request finishes.

This is important if we ever want to re-open or reprocess a stored Telegram image later.

### PostgreSQL Tables Actually Used By Runtime

Currently used in application logic:

- `dim_user`
- `fact_consumption`
- `fact_expenses`
- `fact_dishes`
- `fact_vocabulary`

Present in migrations but not actively used by current runtime behavior:

- `dim_models`
- `fact_usage`

### Warehouse Data Granularity

The warehouse stores less detail than the local SQLite record.

Examples:

- nutrition facts store category, rounded calories, tags, and alcohol units
- ingredient lists are not persisted to `fact_consumption`
- macros are not persisted to `fact_consumption`
- recipes store summary fields, not a full recipe instruction model

This means:

- local SQLite is richer for one-off record inspection
- PostgreSQL is better for cross-user or historical queries

### Nutrition Persistence Note

When the bot persists a nutrition result:

- PostgreSQL `meal_id` is set to the local `record_id` when available
- calories are rounded to an integer before warehouse storage

So the Telegram reply can show float calories from the local analysis while warehouse totals and later SQL answers use rounded integers.

## PostgreSQL Schema / Migration Notes

Observed migration progression:

1. `001_init_db.sql`
   database/user bootstrap

2. `002_init_schema.sql`
   creates:
   - `dim_user`
   - `dim_models`
   - `fact_usage`
   - `fact_consumption`
   - `fact_dishes`

3. `003_insert.sql`
   seed data

4. `004_init_schema.sql`
   adds `expense_category` enum and `fact_expenses`

5. `005_init_schema.sql`
   adds `fact_vocabulary`

6. `006_edit_schema.sql`
   adds review-state columns to `fact_vocabulary` if missing

7. `007_edit_schema.sql`
   adds `telegram_user_id` to `dim_user`

8. `008_edit_schema.sql`
   adds recipe enum types and recipe classification columns to `fact_dishes`

## Vocabulary Review Lifecycle Notes

Vocabulary review stages:

- `day`
- `three_days`
- `week`
- `month`

Behavior:

- due reviews are selected from `fact_vocabulary`
- only one due review per user is dispatched at a time
- dispatch marks `awaiting_review = TRUE`
- the user's next Telegram text is treated as the answer
- correct answers advance the stage
- incorrect answers keep the same stage and schedule another review
- shelf requests stop future review for that word

Matching behavior:

- lowercased
- accents stripped
- punctuation normalized
- small spelling errors tolerated with `SequenceMatcher`

## Logging Notes

Structured logs include:

- timestamp
- level
- logger
- message
- process id
- user id
- Telegram user id
- update id
- action
- workflow
- extra event payload

This is useful because one Telegram message can touch:

- FastAPI
- bot handler
- agent graph
- LLM helper
- PostgreSQL

## Query Safety Notes

`validate_readonly_query(...)` enforces:

- query cannot be empty
- only one statement
- no SQL comments
- query must start with `SELECT` or `WITH`
- destructive keywords are rejected
- `user_id = $1` must be present
- only allowed tables may appear in `FROM` / `JOIN`

Important mismatch to keep in mind:

- the LLM prompt says not to use CTEs
- the validator still accepts `WITH`

So the prompt is stricter than the validator right now.

## Current Documentation / Implementation Mismatches

### README / Packaging Drift

Older docs still mention:

- `src.main`
- CLI commands like `python3.11 -m src.main analyze ...`
- API endpoints such as `/records/analyze` and `/records`

But the currently inspected codebase does not include `src/main.py`, and `src/api.py` currently exposes only:

- `/webhook/telegram`
- `/jobs/vocabulary-reviews/run`
- `/health`

### Terminology Drift

The repo uses both names:

- PictoAgent
- PicFlic

That is mostly cosmetic right now, but it is visible across docs and deployment notes.

## Test Coverage Snapshot

The current tests cover a good portion of the runtime surface:

- image graph routing
- text graph routing
- local SQLite persistence
- Telegram message branching
- PostgreSQL write methods
- vocabulary review dispatch and answer handling
- nutrition correction behavior
- query validation
- formatting helpers

There are also OpenAI-backed end-to-end tests for the real workflows, but they are skipped unless `OPENAI_API_KEY` is present.

## Cleanup Candidates / Open Questions

- Decide whether the local SQLite nutrition record should keep `item_count` instead of dropping it.
- Decide whether storing deleted temp-file paths in `ImageRecord.image_path` is acceptable long-term.
- Decide whether the README should be updated to the current FastAPI + webhook reality.
- Decide whether `src.main` should be restored or removed from packaging/docs references.
- Decide whether query validation should explicitly ban `WITH` to match the prompt.
- Decide whether more nutrition detail should be persisted in PostgreSQL if future queries need ingredients or macros.
