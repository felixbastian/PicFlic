"""Public API for PictoAgent."""

from .agent import PictoAgent
from .config import AppConfig, load_config
from .db import SqliteDatabase
from .mcp import MCPAdapter, SqliteMCPAdapter
from .models import ExpenseAnalysis, ImageRecord, MacroBreakdown, NutritionAnalysis, RoutingDecision


def create_default_agent() -> PictoAgent:
    """Create an agent backed by the configured on-disk SQLite database."""

    config = load_config()
    return PictoAgent(SqliteDatabase(config.database_path))


__all__ = [
    "AppConfig",
    "ExpenseAnalysis",
    "ImageRecord",
    "MCPAdapter",
    "MacroBreakdown",
    "NutritionAnalysis",
    "PictoAgent",
    "RoutingDecision",
    "SqliteDatabase",
    "SqliteMCPAdapter",
    "create_default_agent",
    "load_config",
]
