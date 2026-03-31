"""Public API for PictoAgent."""

from .agents import MainAgent, PictoAgent, VocabularyAgent
from .config import AppConfig, load_config
from .db import SqliteDatabase
from .mcp import MCPAdapter, SqliteMCPAdapter
from .models import (
    ExpenseAnalysis,
    ImageRecord,
    IngredientEstimate,
    MacroBreakdown,
    NutritionAnalysis,
    NutritionCorrectionResult,
    RoutingDecision,
)


def create_default_agent() -> PictoAgent:
    """Create an agent backed by the configured on-disk SQLite database."""

    config = load_config()
    return PictoAgent(SqliteDatabase(config.database_path))


def create_default_vocabulary_agent() -> VocabularyAgent:
    """Create the dedicated vocabulary review agent."""

    return VocabularyAgent()


__all__ = [
    "AppConfig",
    "ExpenseAnalysis",
    "ImageRecord",
    "IngredientEstimate",
    "MCPAdapter",
    "MainAgent",
    "MacroBreakdown",
    "NutritionAnalysis",
    "NutritionCorrectionResult",
    "PictoAgent",
    "RoutingDecision",
    "SqliteDatabase",
    "SqliteMCPAdapter",
    "create_default_agent",
    "create_default_vocabulary_agent",
    "load_config",
    "VocabularyAgent",
]
