"""Database helpers for PictoAgent."""

from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

import asyncpg

from .config import AppConfig
from .models import ExpenseAnalysis, ImageRecord
from .mcp import DatabaseMCPAdapter

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
            logger.info(f"Connected to PostgreSQL database at {self.host}:{self.port}")
        except Exception as e:
            logger.error(f"Failed to connect to PostgreSQL: {e}")
            raise

    async def disconnect(self) -> None:
        """Close connection pool."""
        if self._pool:
            await self._pool.close()
            logger.info("Disconnected from PostgreSQL database")

    async def get_or_create_user(
        self,
        telegram_user_id: int,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
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
            existing_user = await conn.fetchval(
                "SELECT user_id FROM dim_user WHERE username = $1",
                username or str(telegram_user_id),
            )

            if existing_user:
                logger.info(f"User {username} already exists with ID {existing_user}")
                return existing_user

            # Create new user
            user_id = str(uuid.uuid4())
            try:
                await conn.execute(
                    """
                    INSERT INTO dim_user (user_id, username, first_name, last_name)
                    VALUES ($1, $2, $3, $4)
                    """,
                    user_id,
                    username or str(telegram_user_id),
                    first_name,
                    last_name,
                )
                logger.info(f"Created new user {username} with ID {user_id}")
                return user_id
            except Exception as e:
                logger.error(f"Failed to create user: {e}")
                raise

    async def store_consumption(self, user_id: str, analysis: ImageRecord | dict | "NutritionAnalysis") -> str:
        """Persist a nutrition analysis to fact_consumption for a user."""
        if not self._pool:
            raise RuntimeError("Database not connected. Call connect() first.")

        from .models import NutritionAnalysis

        if isinstance(analysis, ImageRecord):
            normalized = analysis.analysis
            meal_id = analysis.id
        elif isinstance(analysis, dict):
            normalized = NutritionAnalysis.model_validate(analysis)
            meal_id = str(uuid.uuid4())
        else:
            normalized = analysis
            meal_id = str(uuid.uuid4())

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

    async def execute_guarded_query(
        self,
        query: str,
        user_id: str,
        allowed_tables: Sequence[str],
    ) -> dict[str, Any]:
        """Execute a validated read-only query for a given user and return one row."""
        if not self._pool:
            raise RuntimeError("Database not connected. Call connect() first.")

        statement = validate_readonly_query(query, allowed_tables)

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(statement, user_id)
            if row is None:
                return {}
            return dict(row)
