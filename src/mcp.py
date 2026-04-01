from __future__ import annotations

import json
import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .models import ImageRecord


class MCPAdapter(ABC):
    """A small interface to the Model Context Protocol (MCP) behavior."""

    @abstractmethod
    def write(self, key: str, value: Dict[str, Any]) -> None:
        """Write a JSON-serializable value to the context store."""

    @abstractmethod
    def read(self, key: str) -> Optional[Dict[str, Any]]:
        """Read a previously written value from the context store."""

    @abstractmethod
    def list_keys(self) -> Iterable[str]:
        """List keys currently stored in the context."""

    @abstractmethod
    def delete(self, key: str) -> None:
        """Delete a previously stored value from the context."""


class SqliteMCPAdapter(MCPAdapter):
    """A minimal MCP adapter that stores data in an SQLite key/value table."""

    def __init__(self, path: str | Path):
        # FastAPI request handlers and tests may access the same adapter from worker threads.
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._ensure_table()

    def _ensure_table(self) -> None:
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS mcp_context (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )"""
        )
        self._conn.commit()

    def write(self, key: str, value: Dict[str, Any]) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO mcp_context(key, value) VALUES(?, ?)",
            (key, json.dumps(value)),
        )
        self._conn.commit()

    def read(self, key: str) -> Optional[Dict[str, Any]]:
        cursor = self._conn.execute(
            "SELECT value FROM mcp_context WHERE key = ?", (key,)
        )
        row = cursor.fetchone()
        return json.loads(row[0]) if row else None

    def list_keys(self) -> Iterable[str]:
        cursor = self._conn.execute("SELECT key FROM mcp_context")
        return [r[0] for r in cursor.fetchall()]

    def delete(self, key: str) -> None:
        self._conn.execute("DELETE FROM mcp_context WHERE key = ?", (key,))
        self._conn.commit()


class DatabaseMCPAdapter(SqliteMCPAdapter):
    """A small helper that stores `ImageRecord` entries as well."""

    def write_record(self, record: ImageRecord) -> None:
        self.write(record.id, record.to_dict())

    def read_record(self, record_id: str) -> Optional[ImageRecord]:
        raw = self.read(record_id)
        return ImageRecord.from_dict(raw) if raw is not None else None

    def list_records(self) -> list[ImageRecord]:
        return [ImageRecord.from_dict(self.read(k)) for k in self.list_keys()]

    def delete_record(self, record_id: str) -> None:
        self.delete(record_id)
