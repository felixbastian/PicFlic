"""Database helpers for PictoAgent."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Iterable, Optional
import asyncpg
import logging

from .config import AppConfig
from .models import ImageRecord
from .mcp import DatabaseMCPAdapter

logger = logging.getLogger(__name__)


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
