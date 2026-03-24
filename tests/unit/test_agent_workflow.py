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


def test_vocabulary_text_uses_vocabulary_workflow_end_to_end(tmp_path):
    _require_openai_api_key()
    agent = PictoAgent(SqliteDatabase(tmp_path / "workflow.db"))

    result = agent.process_text("bonjour", metadata={"recent_history": []})

    assert result["workflow_type"] == "vocabulary"
    assert result["assistant_reply"].strip()
    assert result["store_vocabulary"] is True
    assert result["french_word"]
    assert "bonjour" in result["french_word"].lower()
    assert result["english_description"]


def test_vocabulary_followup_uses_recent_history_without_storing_again_end_to_end(tmp_path):
    _require_openai_api_key()
    agent = PictoAgent(SqliteDatabase(tmp_path / "workflow.db"))

    first_result = agent.process_text("bonjour", metadata={"recent_history": []})
    second_result = agent.process_text(
        "Can you give me an example sentence?",
        metadata={
            "recent_history": [
                {"role": "user", "text": "bonjour", "workflow": "vocabulary"},
                {
                    "role": "assistant",
                    "text": first_result["assistant_reply"],
                    "workflow": "vocabulary",
                },
            ]
        },
    )

    assert second_result["workflow_type"] == "vocabulary"
    assert second_result["assistant_reply"].strip()
    assert second_result["store_vocabulary"] is False
    assert second_result["french_word"] is None
    assert second_result["english_description"] is None


def test_recipe_text_uses_recipe_collection_workflow_end_to_end(tmp_path):
    _require_openai_api_key()
    agent = PictoAgent(SqliteDatabase(tmp_path / "workflow.db"))

    result = agent.process_text(
        "Add this to the recipes: lemon pasta with butter, parmesan, and black pepper. "
        "We should make it monthly."
    )

    assert result["workflow_type"] == "recipe_collection"
    assert result["name"].strip()
    assert result["description"].strip()
