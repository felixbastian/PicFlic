from pathlib import Path

import pytest

from src.agent import PictoAgent
from src.db import SqliteDatabase


PROJECT_ROOT = Path(__file__).resolve().parents[2]
NUTRITION_IMAGE = PROJECT_ROOT / "sample_images" / "croissant.jpeg"
EXPENSE_IMAGE = PROJECT_ROOT / "sample_images" / "expense.jpeg"






def test_vocabulary_text_uses_vocabulary_workflow_end_to_end(tmp_path):
   
    agent = PictoAgent(SqliteDatabase(tmp_path / "workflow.db"))

    result = agent.process_text("bonjour", metadata={"recent_history": []})

    assert result["workflow_type"] == "vocabulary"
    assert result["assistant_reply"].strip()
    assert result["store_vocabulary"] is True
    assert result["french_word"]
    assert "bonjour" in result["french_word"].lower()
    assert result["english_description"]


def test_vocabulary_followup_uses_recent_history_without_storing_again_end_to_end(tmp_path):
   
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
   
    agent = PictoAgent(SqliteDatabase(tmp_path / "workflow.db"))

    result = agent.process_text(
        "Add this to the recipes: lemon pasta with butter, parmesan, and black pepper. "
        "We should make it monthly."
    )

    assert result["workflow_type"] == "recipe_collection"
    assert result["name"].strip()
    assert result["description"].strip()
