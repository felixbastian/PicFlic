from pathlib import Path

import pytest

from src.agent import PictoAgent
from src.config import load_config
from src.db import SqliteDatabase, validate_readonly_query
from src.models import EXPENSE_CATEGORIES


PROJECT_ROOT = Path(__file__).resolve().parents[2]
NUTRITION_IMAGE = PROJECT_ROOT / "sample_images" / "croissant.jpeg"
EXPENSE_IMAGE = PROJECT_ROOT / "sample_images" / "expense.jpeg"


def _require_openai_api_key() -> None:
    load_config.cache_clear()
    config = load_config()
    if not config.openai_api_key:
        pytest.skip("OPENAI_API_KEY is required for end-to-end workflow validation.")


def test_nutrition_image_uses_nutrition_workflow_end_to_end(tmp_path):
    assert NUTRITION_IMAGE.is_file()
    _require_openai_api_key()

    agent = PictoAgent(SqliteDatabase(tmp_path / "workflow.db"))

    result = agent.process_image(str(NUTRITION_IMAGE))

    assert result["task_type"] == "nutrition"
    assert result["record_id"]
    assert result["analysis"]["category"]
    assert result["analysis"]["calories"] > 0
    assert result["analysis"]["macros"]
    record = agent.get_record(result["record_id"])
    assert record is not None
    assert record.task_type == "nutrition"
    assert record.image_path == str(NUTRITION_IMAGE)


def test_expense_image_uses_expense_workflow_end_to_end(tmp_path):
    assert EXPENSE_IMAGE.is_file()
    _require_openai_api_key()

    agent = PictoAgent(SqliteDatabase(tmp_path / "workflow.db"))

    result = agent.process_image(str(EXPENSE_IMAGE))

    assert result["task_type"] == "expense"
    assert result["record_id"]
    assert result["analysis"]["expense_total_amount_in_euros"] > 0
    assert result["analysis"]["category"] in EXPENSE_CATEGORIES
    assert result["analysis"]["description"].strip()
    record = agent.get_record(result["record_id"])
    assert record is not None
    assert record.task_type == "expense"
    assert record.image_path == str(EXPENSE_IMAGE)


def test_expense_text_query_uses_expense_workflow_end_to_end(tmp_path):
    _require_openai_api_key()
    agent = PictoAgent(SqliteDatabase(tmp_path / "workflow.db"))

    result = agent.process_text("What are the total expenses in January on groceries (Lebensmittel)?")

    assert result["workflow_type"] == "expense_query"
    assert result["explanation"].strip()
    assert "fact_expenses" in result["sql_query"].lower()
    assert result["response_template"].strip()
    guarded_query = validate_readonly_query(result["sql_query"], ("fact_expenses",))
    assert guarded_query.lower().startswith("select")
    assert "$1" in guarded_query


def test_nutrition_text_query_uses_nutrition_workflow_end_to_end(tmp_path):
    _require_openai_api_key()
    agent = PictoAgent(SqliteDatabase(tmp_path / "workflow.db"))

    result = agent.process_text("How many calories have I consumed this month?")

    assert result["workflow_type"] == "nutrition_query"
    assert result["explanation"].strip()
    assert "fact_consumption" in result["sql_query"].lower()
    assert result["response_template"].strip()
    guarded_query = validate_readonly_query(result["sql_query"], ("fact_consumption",))
    assert guarded_query.lower().startswith("select")
    assert "$1" in guarded_query
