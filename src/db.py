"""Database helpers for PictoAgent."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from .models import ImageRecord
from .mcp import DatabaseMCPAdapter


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
