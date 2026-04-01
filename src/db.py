"""Database helpers for PictoAgent."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

import asyncpg

from .config import AppConfig
from .models import (
    DueVocabularyReview,
    ExpenseAnalysis,
    ImageRecord,
    ReferencedVocabularyReview,
    RecipeAnalysis,
    VocabularyReviewResult,
    VocabularyReviewStage,
)
from .mcp import DatabaseMCPAdapter
from .vocabulary_review import normalize_review_text

logger = logging.getLogger(__name__)

_DISALLOWED_SQL_PATTERN = re.compile(
    r"\b("
    r"insert|update|delete|drop|alter|truncate|create|grant|revoke|comment|copy|vacuum|"
    r"analyze|do|call|execute|merge|attach|detach|refresh|set|reset|discard"
    r")\b",
    re.IGNORECASE,
)
_TABLE_REFERENCE_PATTERN = re.compile(r"\b(?:from|join)\s+([a-zA-Z_][\w\.]*)", re.IGNORECASE)
_USER_FILTER_PATTERN = re.compile(r"\buser_id\s*=\s*\$1\b", re.IGNORECASE)
_REVIEW_STAGE_INTERVAL_SQL: dict[VocabularyReviewStage, str] = {
    "day": "INTERVAL '1 day'",
    "three_days": "INTERVAL '3 days'",
    "week": "INTERVAL '7 days'",
    "month": "INTERVAL '1 month'",
}
_REVIEW_STAGE_FLAG_COLUMN: dict[VocabularyReviewStage, str] = {
    "day": "correct_day",
    "three_days": "correct_three_days",
    "week": "correct_week",
    "month": "correct_month",
}
_REVIEW_STAGE_NEXT: dict[VocabularyReviewStage, VocabularyReviewStage | None] = {
    "day": "three_days",
    "three_days": "week",
    "week": "month",
    "month": None,
}


def _normalize_due_vocabulary_review_row(row: Any) -> dict[str, Any]:
    """Normalize asyncpg row values for DueVocabularyReview validation."""
    normalized = dict(row)
    for field_name in ("vocabulary_id", "user_id"):
        field_value = normalized.get(field_name)
        if isinstance(field_value, uuid.UUID):
            normalized[field_name] = str(field_value)
    return normalized


def validate_readonly_query(query: str, allowed_tables: Sequence[str]) -> str:
    """Validate a generated SQL query against conservative read-only guardrails."""
    normalized = query.strip()
    if not normalized:
        raise ValueError("Query cannot be empty.")

    statement = normalized.rstrip(";").strip()
    if not statement:
        raise ValueError("Query cannot be empty.")
    if ";" in statement:
        raise ValueError("Only a single SQL statement is allowed.")
    if "--" in statement or "/*" in statement or "*/" in statement:
        raise ValueError("SQL comments are not allowed.")
    if not re.match(r"^(select|with)\b", statement, re.IGNORECASE):
        raise ValueError("Only read-only SELECT queries are allowed.")
    if _DISALLOWED_SQL_PATTERN.search(statement):
        raise ValueError("Only read-only SELECT queries are allowed.")
    if not _USER_FILTER_PATTERN.search(statement):
        raise ValueError("Query must filter on user_id = $1.")

    allowed = {table.lower() for table in allowed_tables}
    referenced_tables = {
        table_name.split(".")[-1].strip('"').lower()
        for table_name in _TABLE_REFERENCE_PATTERN.findall(statement)
    }
    if not referenced_tables:
        raise ValueError("Query must reference one of the allowed fact tables.")

    disallowed_tables = referenced_tables - allowed
    if disallowed_tables:
        raise ValueError(
            f"Query references disallowed tables: {', '.join(sorted(disallowed_tables))}."
        )

    logger.info(
        "Validated read-only query",
        extra={
            "event": "db_query_validated",
            "allowed_tables": list(allowed_tables),
            "sql_query": statement,
        },
    )
    return statement


class SqliteDatabase:
    """A small database wrapper using the MCP adapter."""

    def __init__(self, path: str | Path):
        database_path = Path(path)
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._mcp = DatabaseMCPAdapter(database_path)

    def store_record(self, record: ImageRecord) -> None:
        self._mcp.write_record(record)

    def get_record(self, record_id: str) -> Optional[ImageRecord]:
        return self._mcp.read_record(record_id)

    def list_records(self) -> list[ImageRecord]:
        return self._mcp.list_records()

    def list_ids(self) -> Iterable[str]:
        return self._mcp.list_keys()


class PostgresDatabase:
    """PostgreSQL database wrapper for handling user data."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 5432,
        user: str = "app_user",
        password: str = "",
        database: str = "app_db",
        cloud_sql_connection_string: str | None = None,
    ):
        """
        Initialize PostgreSQL database connection.
        
        Args:
            host: Database host (default: 127.0.0.1 for local proxy)
            port: Database port
            user: Database user
            password: Database password
            database: Database name
            cloud_sql_connection_string: Cloud SQL connection string (e.g., 'project:region:instance')
                                        If provided, uses Cloud SQL Auth Proxy
        """
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self.cloud_sql_connection_string = cloud_sql_connection_string
        self._pool: Optional[asyncpg.Pool] = None

    @classmethod
    def from_config(cls, config: AppConfig) -> "PostgresDatabase":
        """Build a PostgreSQL client from application config."""
        host = config.db_host or "127.0.0.1"
        if config.instance_connection_name:
            host = f"/cloudsql/{config.instance_connection_name}"

        return cls(
            host=host,
            port=config.db_port,
            user=config.db_user or "app_user",
            password=config.db_password or "",
            database=config.db_name or "app_db",
            cloud_sql_connection_string=config.instance_connection_name,
        )

    async def connect(self) -> None:
        """Initialize connection pool."""
        try:
            connect_kwargs = {
                "host": self.host,
                "user": self.user,
                "password": self.password,
                "database": self.database,
                "min_size": 1,
                "max_size": 10,
            }
            if not str(self.host).startswith("/cloudsql/"):
                connect_kwargs["port"] = self.port

            self._pool = await asyncpg.create_pool(
                **connect_kwargs,
            )
            logger.info(
                "Connected to PostgreSQL database",
                extra={"event": "db_connected", "host": self.host, "port": self.port, "database": self.database},
            )
        except Exception as e:
            logger.error(
                "Failed to connect to PostgreSQL: %s",
                e,
                extra={"event": "db_connect_failed", "host": self.host, "port": self.port, "database": self.database},
            )
            raise

    async def disconnect(self) -> None:
        """Close connection pool."""
        if self._pool:
            await self._pool.close()
            logger.info("Disconnected from PostgreSQL database", extra={"event": "db_disconnected"})

    async def get_or_create_user(
        self,
        telegram_user_id: int,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        has_vocab_bot_activated: bool | None = None,
    ) -> str:
        """
        Get or create a user in dim_user table.
        
        Args:
            telegram_user_id: Telegram user ID
            username: Telegram username
            first_name: User's first name
            last_name: User's last name
            
        Returns:
            UUID of the user
        """
        if not self._pool:
            raise RuntimeError("Database not connected. Call connect() first.")

        async with self._pool.acquire() as conn:
            # Check if user exists
            existing_user = await conn.fetchrow(
                """
                SELECT user_id
                FROM dim_user
                WHERE telegram_user_id = $1 OR username = $2
                ORDER BY CASE WHEN telegram_user_id = $1 THEN 0 ELSE 1 END
                LIMIT 1
                """,
                telegram_user_id,
                username or str(telegram_user_id),
            )

            if existing_user:
                resolved_user_id = str(existing_user["user_id"])
                await conn.execute(
                    """
                    UPDATE dim_user
                    SET telegram_user_id = $2,
                        username = $3,
                        first_name = $4,
                        last_name = $5,
                        has_vocab_bot_activated = COALESCE($6, has_vocab_bot_activated)
                    WHERE user_id = $1
                    """,
                    resolved_user_id,
                    telegram_user_id,
                    username or str(telegram_user_id),
                    first_name,
                    last_name,
                    has_vocab_bot_activated,
                )
                logger.info(
                    "Warehouse user already exists",
                    extra={"event": "warehouse_user_exists", "username": username, "resolved_user_id": resolved_user_id},
                )
                return resolved_user_id

            # Create new user
            user_id = str(uuid.uuid4())
            try:
                await conn.execute(
                    """
                    INSERT INTO dim_user (
                        user_id,
                        telegram_user_id,
                        username,
                        first_name,
                        last_name,
                        has_vocab_bot_activated
                    )
                    VALUES ($1, $2, $3, $4, $5, COALESCE($6, FALSE))
                    """,
                    user_id,
                    telegram_user_id,
                    username or str(telegram_user_id),
                    first_name,
                    last_name,
                    has_vocab_bot_activated,
                )
                logger.info(
                    "Created warehouse user",
                    extra={"event": "warehouse_user_created", "username": username, "resolved_user_id": user_id},
                )
                return user_id
            except Exception as e:
                logger.error("Failed to create user: %s", e, extra={"event": "warehouse_user_create_failed"})
                raise

    async def has_vocab_bot_activated(self, user_id: str) -> bool:
        """Return whether the user has activated the separate vocabulary bot."""
        if not self._pool:
            raise RuntimeError("Database not connected. Call connect() first.")

        async with self._pool.acquire() as conn:
            activated = await conn.fetchval(
                """
                SELECT COALESCE(has_vocab_bot_activated, FALSE)
                FROM dim_user
                WHERE user_id = $1
                """,
                user_id,
            )
            return bool(activated)

    async def store_consumption(
        self,
        user_id: str,
        analysis: ImageRecord | dict | "NutritionAnalysis",
        meal_id: str | None = None,
    ) -> str:
        """Persist a nutrition analysis to fact_consumption for a user."""
        if not self._pool:
            raise RuntimeError("Database not connected. Call connect() first.")

        from .models import NutritionAnalysis

        if isinstance(analysis, ImageRecord):
            normalized = analysis.analysis
            meal_id = analysis.id
        elif isinstance(analysis, dict):
            normalized = NutritionAnalysis.model_validate(analysis)
            meal_id = meal_id or str(uuid.uuid4())
        else:
            normalized = analysis
            meal_id = meal_id or str(uuid.uuid4())

        async with self._pool.acquire() as conn:
            try:
                await conn.execute(
                    """
                    INSERT INTO fact_consumption (
                        meal_id,
                        user_id,
                        category,
                        calories,
                        tags,
                        alcohol_units
                    )
                    VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                    meal_id,
                    user_id,
                    normalized.category,
                    int(round(normalized.calories)),
                    normalized.tags,
                    normalized.alcohol_units,
                )
                logger.info("Stored fact_consumption row %s for user %s", meal_id, user_id)
                return meal_id
            except Exception as e:
                logger.error("Failed to store consumption for user %s: %s", user_id, e)
                raise

    async def update_consumption(
        self,
        meal_id: str,
        user_id: str,
        analysis: "NutritionAnalysis" | dict,
    ) -> None:
        """Update an existing nutrition analysis row in fact_consumption."""
        if not self._pool:
            raise RuntimeError("Database not connected. Call connect() first.")

        from .models import NutritionAnalysis

        normalized = analysis
        if isinstance(analysis, dict):
            normalized = NutritionAnalysis.model_validate(analysis)

        async with self._pool.acquire() as conn:
            try:
                await conn.execute(
                    """
                    UPDATE fact_consumption
                    SET category = $3,
                        calories = $4,
                        tags = $5,
                        alcohol_units = $6
                    WHERE meal_id = $1 AND user_id = $2
                    """,
                    meal_id,
                    user_id,
                    normalized.category,
                    int(round(normalized.calories)),
                    normalized.tags,
                    normalized.alcohol_units,
                )
                logger.info("Updated fact_consumption row %s for user %s", meal_id, user_id)
            except Exception as e:
                logger.error("Failed to update consumption %s for user %s: %s", meal_id, user_id, e)
                raise

    async def get_daily_calories(self, user_id: str) -> int:
        """Return the user's total calories for the current database day."""
        if not self._pool:
            raise RuntimeError("Database not connected. Call connect() first.")

        async with self._pool.acquire() as conn:
            total = await conn.fetchval(
                """
                SELECT COALESCE(SUM(calories), 0)
                FROM fact_consumption
                WHERE user_id = $1
                  AND created_at >= CURRENT_DATE
                  AND created_at < CURRENT_DATE + INTERVAL '1 day'
                """,
                user_id,
            )
            return int(total or 0)

    async def store_expense(self, user_id: str, analysis: ExpenseAnalysis | dict) -> str:
        """Persist an expense analysis to fact_expenses for a user."""
        if not self._pool:
            raise RuntimeError("Database not connected. Call connect() first.")

        normalized = analysis
        if isinstance(analysis, dict):
            normalized = ExpenseAnalysis.model_validate(analysis)

        expense_id = str(uuid.uuid4())
        async with self._pool.acquire() as conn:
            try:
                await conn.execute(
                    """
                    INSERT INTO fact_expenses (
                        expense_id,
                        user_id,
                        description,
                        expense_total_amount_in_euros,
                        category
                    )
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    expense_id,
                    user_id,
                    normalized.description,
                    normalized.expense_total_amount_in_euros,
                    normalized.category,
                )
                logger.info("Stored fact_expenses row %s for user %s", expense_id, user_id)
                return expense_id
            except Exception as e:
                logger.error("Failed to store expense for user %s: %s", user_id, e)
                raise

    async def store_vocabulary(self, user_id: str, french_word: str, english_description: str) -> str:
        """Persist a vocabulary entry to fact_vocabulary for a user."""
        if not self._pool:
            raise RuntimeError("Database not connected. Call connect() first.")

        vocabulary_id = str(uuid.uuid4())
        async with self._pool.acquire() as conn:
            try:
                await conn.execute(
                    """
                    INSERT INTO fact_vocabulary (
                        vocabulary_id,
                        user_id,
                        french_word,
                        english_description
                    )
                    VALUES ($1, $2, $3, $4)
                    """,
                    vocabulary_id,
                    user_id,
                    french_word,
                    english_description,
                )
                logger.info(
                    "Stored fact_vocabulary row %s for user %s",
                    vocabulary_id,
                    user_id,
                    extra={
                        "event": "vocabulary_stored",
                        "vocabulary_id": vocabulary_id,
                        "french_word": french_word,
                    },
                )
                return vocabulary_id
            except Exception as e:
                logger.error("Failed to store vocabulary for user %s: %s", user_id, e)
                raise

    async def store_dish(self, user_id: str, analysis: RecipeAnalysis | dict) -> str:
        """Persist a recipe or dish idea to fact_dishes for a user."""
        if not self._pool:
            raise RuntimeError("Database not connected. Call connect() first.")

        normalized = analysis
        if isinstance(analysis, dict):
            normalized = RecipeAnalysis.model_validate(analysis)

        dish_id = str(uuid.uuid4())
        async with self._pool.acquire() as conn:
            try:
                await conn.execute(
                    """
                    INSERT INTO fact_dishes (
                        dish_id,
                        user_id,
                        picture_link,
                        name,
                        description,
                        carb_source,
                        vegetarian,
                        meat,
                        frequency_rotation
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    """,
                    dish_id,
                    user_id,
                    None,
                    normalized.name,
                    normalized.description,
                    normalized.carb_source,
                    normalized.vegetarian,
                    normalized.meat,
                    normalized.frequency_rotation,
                )
                logger.info(
                    "Stored fact_dishes row %s for user %s",
                    dish_id,
                    user_id,
                    extra={"event": "dish_stored", "dish_id": dish_id, "dish_name": normalized.name},
                )
                return dish_id
            except Exception as e:
                logger.error("Failed to store dish for user %s: %s", user_id, e)
                raise

    async def list_due_vocabulary_reviews(self, limit: int = 100) -> list[DueVocabularyReview]:
        """Return at most one due vocabulary review per user."""
        if not self._pool:
            raise RuntimeError("Database not connected. Call connect() first.")

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT ON (v.user_id)
                    v.vocabulary_id,
                    v.user_id,
                    u.telegram_user_id,
                    v.french_word,
                    v.english_description,
                    v.current_review_stage,
                    v.next_review_at
                FROM fact_vocabulary v
                JOIN dim_user u ON u.user_id = v.user_id
                WHERE v.finished = FALSE
                  AND v.shelf = FALSE
                  AND v.awaiting_review = FALSE
                  AND v.current_review_stage IS NOT NULL
                  AND v.next_review_at IS NOT NULL
                  AND v.next_review_at <= CURRENT_TIMESTAMP
                  AND u.telegram_user_id IS NOT NULL
                  AND COALESCE(u.has_vocab_bot_activated, FALSE) = TRUE
                  AND NOT EXISTS (
                      SELECT 1
                      FROM fact_vocabulary pending
                      WHERE pending.user_id = v.user_id
                        AND pending.awaiting_review = TRUE
                        AND pending.finished = FALSE
                        AND pending.shelf = FALSE
                  )
                ORDER BY v.user_id, v.next_review_at ASC, v.created_at ASC
                LIMIT $1
                """,
                limit,
            )
            due_reviews = [
                DueVocabularyReview.model_validate(_normalize_due_vocabulary_review_row(row))
                for row in rows
            ]
            logger.info(
                "Loaded due vocabulary reviews",
                extra={"event": "vocabulary_due_loaded", "due_review_count": len(due_reviews)},
            )
            return due_reviews

    async def list_stale_vocabulary_review_reminders(
        self,
        limit: int = 100,
        resend_after: timedelta = timedelta(hours=1),
    ) -> list[DueVocabularyReview]:
        """Return at most one stale pending vocabulary review per user for reminder delivery."""
        if not self._pool:
            raise RuntimeError("Database not connected. Call connect() first.")

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT ON (v.user_id)
                    v.vocabulary_id,
                    v.user_id,
                    u.telegram_user_id,
                    v.french_word,
                    v.english_description,
                    v.current_review_stage,
                    v.next_review_at
                FROM fact_vocabulary v
                JOIN dim_user u ON u.user_id = v.user_id
                WHERE v.finished = FALSE
                  AND v.shelf = FALSE
                  AND v.awaiting_review = TRUE
                  AND (
                      v.last_review_prompted_at IS NULL
                      OR v.last_review_prompted_at <= CURRENT_TIMESTAMP - $1::INTERVAL
                  )
                  AND u.telegram_user_id IS NOT NULL
                  AND COALESCE(u.has_vocab_bot_activated, FALSE) = TRUE
                ORDER BY v.user_id, v.last_review_prompted_at ASC NULLS FIRST, v.created_at ASC
                LIMIT $2
                """,
                resend_after,
                limit,
            )
            stale_reviews = [
                DueVocabularyReview.model_validate(_normalize_due_vocabulary_review_row(row))
                for row in rows
            ]
            logger.info(
                "Loaded stale pending vocabulary reviews",
                extra={"event": "vocabulary_stale_pending_loaded", "stale_review_count": len(stale_reviews)},
            )
            return stale_reviews

    async def get_next_due_vocabulary_review_for_user(self, user_id: str) -> DueVocabularyReview | None:
        """Return the next due vocabulary review for a specific user, if any."""
        if not self._pool:
            raise RuntimeError("Database not connected. Call connect() first.")

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    v.vocabulary_id,
                    v.user_id,
                    u.telegram_user_id,
                    v.french_word,
                    v.english_description,
                    v.current_review_stage,
                    v.next_review_at
                FROM fact_vocabulary v
                JOIN dim_user u ON u.user_id = v.user_id
                WHERE v.user_id = $1
                  AND v.finished = FALSE
                  AND v.shelf = FALSE
                  AND v.awaiting_review = FALSE
                  AND v.current_review_stage IS NOT NULL
                  AND v.next_review_at IS NOT NULL
                  AND v.next_review_at <= CURRENT_TIMESTAMP
                  AND u.telegram_user_id IS NOT NULL
                  AND COALESCE(u.has_vocab_bot_activated, FALSE) = TRUE
                  AND NOT EXISTS (
                      SELECT 1
                      FROM fact_vocabulary pending
                      WHERE pending.user_id = v.user_id
                        AND pending.awaiting_review = TRUE
                        AND pending.finished = FALSE
                        AND pending.shelf = FALSE
                  )
                ORDER BY v.next_review_at ASC, v.created_at ASC
                LIMIT 1
                """,
                user_id,
            )
            if row is None:
                return None
            return DueVocabularyReview.model_validate(_normalize_due_vocabulary_review_row(row))

    async def mark_vocabulary_review_prompted(self, vocabulary_id: str) -> None:
        """Mark a vocabulary item as awaiting the user's answer."""
        if not self._pool:
            raise RuntimeError("Database not connected. Call connect() first.")

        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE fact_vocabulary
                SET awaiting_review = TRUE,
                    last_review_prompted_at = CURRENT_TIMESTAMP
                WHERE vocabulary_id = $1
                """,
                vocabulary_id,
            )
            logger.info(
                "Marked vocabulary review as prompted",
                extra={"event": "vocabulary_review_prompted", "vocabulary_id": vocabulary_id},
            )

    async def get_pending_vocabulary_review(self, telegram_user_id: int) -> DueVocabularyReview | None:
        """Return the currently pending vocabulary review for a Telegram user, if any."""
        if not self._pool:
            raise RuntimeError("Database not connected. Call connect() first.")

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    v.vocabulary_id,
                    v.user_id,
                    u.telegram_user_id,
                    v.french_word,
                    v.english_description,
                    v.current_review_stage,
                    v.next_review_at
                FROM fact_vocabulary v
                JOIN dim_user u ON u.user_id = v.user_id
                WHERE u.telegram_user_id = $1
                  AND v.awaiting_review = TRUE
                  AND v.finished = FALSE
                  AND v.shelf = FALSE
                  AND COALESCE(u.has_vocab_bot_activated, FALSE) = TRUE
                ORDER BY v.last_review_prompted_at DESC NULLS LAST, v.created_at ASC
                LIMIT 1
                """,
                telegram_user_id,
            )
            if row is None:
                return None
            return DueVocabularyReview.model_validate(_normalize_due_vocabulary_review_row(row))

    async def get_recent_prompted_vocabulary_review_by_prompt(
        self,
        telegram_user_id: int,
        prompt_text: str,
        limit: int = 25,
    ) -> ReferencedVocabularyReview | None:
        """Resolve a quoted review prompt back to a previously prompted vocabulary item."""
        if not self._pool:
            raise RuntimeError("Database not connected. Call connect() first.")

        normalized_prompt = prompt_text.strip()
        if not normalized_prompt:
            return None

        from .vocabulary_review import build_review_prompt_text

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    v.vocabulary_id,
                    v.user_id,
                    u.telegram_user_id,
                    v.french_word,
                    v.english_description
                FROM fact_vocabulary v
                JOIN dim_user u ON u.user_id = v.user_id
                WHERE u.telegram_user_id = $1
                  AND v.last_review_prompted_at IS NOT NULL
                  AND v.shelf = FALSE
                  AND COALESCE(u.has_vocab_bot_activated, FALSE) = TRUE
                ORDER BY v.last_review_prompted_at DESC NULLS LAST, v.created_at DESC
                LIMIT $2
                """,
                telegram_user_id,
                limit,
            )

        for row in rows:
            reference = ReferencedVocabularyReview.model_validate(_normalize_due_vocabulary_review_row(row))
            if build_review_prompt_text(reference.english_description) == normalized_prompt:
                return reference

        return None

    async def get_recent_prompted_vocabulary_review_by_french_word(
        self,
        telegram_user_id: int,
        french_word: str,
        limit: int = 25,
    ) -> ReferencedVocabularyReview | None:
        """Resolve a quoted bot feedback message back to a recent vocabulary item by its French word."""
        if not self._pool:
            raise RuntimeError("Database not connected. Call connect() first.")

        normalized_word = normalize_review_text(french_word)
        if not normalized_word:
            return None

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    v.vocabulary_id,
                    v.user_id,
                    u.telegram_user_id,
                    v.french_word,
                    v.english_description
                FROM fact_vocabulary v
                JOIN dim_user u ON u.user_id = v.user_id
                WHERE u.telegram_user_id = $1
                  AND v.last_review_prompted_at IS NOT NULL
                  AND v.shelf = FALSE
                  AND COALESCE(u.has_vocab_bot_activated, FALSE) = TRUE
                ORDER BY v.last_review_prompted_at DESC NULLS LAST, v.created_at DESC
                LIMIT $2
                """,
                telegram_user_id,
                limit,
            )

        for row in rows:
            reference = ReferencedVocabularyReview.model_validate(_normalize_due_vocabulary_review_row(row))
            if normalize_review_text(reference.french_word) == normalized_word:
                return reference

        return None

    async def record_vocabulary_review_result(
        self,
        vocabulary_id: str,
        *,
        correct: bool = False,
        shelved: bool = False,
    ) -> VocabularyReviewResult:
        """Persist the result of a user's vocabulary review answer."""
        if not self._pool:
            raise RuntimeError("Database not connected. Call connect() first.")

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT vocabulary_id, user_id, french_word, current_review_stage
                    FROM fact_vocabulary
                    WHERE vocabulary_id = $1
                    FOR UPDATE
                    """,
                    vocabulary_id,
                )
                if row is None:
                    raise ValueError(f"Unknown vocabulary_id: {vocabulary_id}")

                current_stage = row["current_review_stage"]
                if shelved:
                    await conn.execute(
                        """
                        UPDATE fact_vocabulary
                        SET shelf = TRUE,
                            awaiting_review = FALSE,
                            current_review_stage = NULL,
                            next_review_at = NULL
                        WHERE vocabulary_id = $1
                        """,
                        vocabulary_id,
                    )
                    return VocabularyReviewResult(
                        vocabulary_id=vocabulary_id,
                        user_id=str(row["user_id"]),
                        french_word=row["french_word"],
                        correct=False,
                        shelved=True,
                        finished=False,
                        current_review_stage=None,
                        next_review_at=None,
                    )

                if current_stage not in _REVIEW_STAGE_INTERVAL_SQL:
                    raise ValueError(f"Vocabulary {vocabulary_id} has no active review stage.")

                stage = current_stage
                if correct:
                    flag_column = _REVIEW_STAGE_FLAG_COLUMN[stage]
                    next_stage = _REVIEW_STAGE_NEXT[stage]
                    if next_stage is None:
                        await conn.execute(
                            f"""
                            UPDATE fact_vocabulary
                            SET {flag_column} = TRUE,
                                finished = TRUE,
                                awaiting_review = FALSE,
                                current_review_stage = NULL,
                                next_review_at = NULL
                            WHERE vocabulary_id = $1
                            """,
                            vocabulary_id,
                        )
                        return VocabularyReviewResult(
                            vocabulary_id=vocabulary_id,
                            user_id=str(row["user_id"]),
                            french_word=row["french_word"],
                            correct=True,
                            shelved=False,
                            finished=True,
                            current_review_stage=None,
                            next_review_at=None,
                        )

                    await conn.execute(
                        f"""
                        UPDATE fact_vocabulary
                        SET {flag_column} = TRUE,
                            awaiting_review = FALSE,
                            current_review_stage = '{next_stage}',
                            next_review_at = CURRENT_TIMESTAMP + {_REVIEW_STAGE_INTERVAL_SQL[next_stage]}
                        WHERE vocabulary_id = $1
                        """,
                        vocabulary_id,
                    )
                    updated = await conn.fetchrow(
                        """
                        SELECT next_review_at, current_review_stage
                        FROM fact_vocabulary
                        WHERE vocabulary_id = $1
                        """,
                        vocabulary_id,
                    )
                    return VocabularyReviewResult(
                        vocabulary_id=vocabulary_id,
                        user_id=str(row["user_id"]),
                        french_word=row["french_word"],
                        correct=True,
                        shelved=False,
                        finished=False,
                        current_review_stage=updated["current_review_stage"],
                        next_review_at=updated["next_review_at"],
                    )

                await conn.execute(
                    f"""
                    UPDATE fact_vocabulary
                    SET awaiting_review = FALSE,
                        next_review_at = CURRENT_TIMESTAMP + {_REVIEW_STAGE_INTERVAL_SQL[stage]}
                    WHERE vocabulary_id = $1
                    """,
                    vocabulary_id,
                )
                updated = await conn.fetchrow(
                    """
                    SELECT next_review_at, current_review_stage
                    FROM fact_vocabulary
                    WHERE vocabulary_id = $1
                    """,
                    vocabulary_id,
                )
                return VocabularyReviewResult(
                    vocabulary_id=vocabulary_id,
                    user_id=str(row["user_id"]),
                    french_word=row["french_word"],
                    correct=False,
                    shelved=False,
                    finished=False,
                    current_review_stage=updated["current_review_stage"],
                    next_review_at=updated["next_review_at"],
                )

    async def execute_guarded_query(
        self,
        query: str,
        user_id: str,
        allowed_tables: Sequence[str],
        max_rows: int = 20,
    ) -> list[dict[str, Any]]:
        """Execute a validated read-only query for a given user and return compact result rows."""
        if not self._pool:
            raise RuntimeError("Database not connected. Call connect() first.")

        statement = validate_readonly_query(query, allowed_tables)

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(statement, user_id)
            trimmed_rows = [dict(row) for row in rows[:max_rows]]
            logger.info(
                "Executed guarded query",
                extra={
                    "event": "db_query_executed",
                    "allowed_tables": list(allowed_tables),
                    "sql_query": statement,
                    "query_result": trimmed_rows,
                    "row_count": len(rows),
                    "truncated": len(rows) > max_rows,
                },
            )
            return trimmed_rows
