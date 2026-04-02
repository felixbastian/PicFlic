from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from openai import APIConnectionError, APITimeoutError

from src.agent import PictoAgent
from src.bot import (
    format_result_response,
    get_latest_expense_result,
    get_latest_tracking_result,
    get_recent_history,
    remember_latest_expense_result,
    remember_latest_tracking_result,
    remember_text_turn,
)
from src.config import load_config
from src.db import SqliteDatabase
from src.models import EXPENSE_CATEGORIES
from src.db import SqliteDatabase, validate_readonly_query


PROJECT_ROOT = Path(__file__).resolve().parents[3]
EXPENSE_IMAGE = PROJECT_ROOT / "sample_images" / "expense.jpeg"


def _require_openai_api_key() -> None:
    load_config.cache_clear()
    config = load_config()
    if not config.openai_api_key:
        pytest.skip("OPENAI_API_KEY is required for expense workflow end-to-end validation.")


def _make_context() -> SimpleNamespace:
    return SimpleNamespace(application=SimpleNamespace(bot_data={}), user_data={})


def _run_or_skip_for_openai_connectivity(fn):
    try:
        return fn()
    except (APIConnectionError, APITimeoutError) as exc:
        pytest.skip(f"OpenAI API is unreachable for expense workflow end-to-end validation: {exc}")


def _remember_expense_turn(
    context: SimpleNamespace,
    user_text: str,
    result: dict,
    *,
    workflow_type: str = "expense_tracking",
) -> None:
    remember_latest_tracking_result(context, result)
    remember_latest_expense_result(context, result)
    remember_text_turn(
        context,
        user_text,
        [format_result_response(result)],
        workflow_type=workflow_type,
    )


def _build_followup_metadata(context: SimpleNamespace) -> dict:
    metadata = {"recent_history": get_recent_history(context)}

    latest_tracking_result = get_latest_tracking_result(context)
    if latest_tracking_result is not None:
        metadata["latest_tracking_result"] = latest_tracking_result

    latest_expense_result = get_latest_expense_result(context)
    if latest_expense_result is not None:
        metadata["latest_expense_result"] = latest_expense_result

    return metadata


def test_process_image_tracks_an_expense_entry_end_to_end(tmp_path):
    assert EXPENSE_IMAGE.is_file()
    _require_openai_api_key()

    agent = PictoAgent(SqliteDatabase(tmp_path / "workflow.db"))

    result = _run_or_skip_for_openai_connectivity(lambda: agent.process_image(str(EXPENSE_IMAGE)))

    assert result["task_type"] == "expense"
    assert result["record_id"]
    assert result["analysis"]["description"].strip()
    assert result["analysis"]["expense_total_amount_in_euros"] > 0
    assert result["analysis"]["category"] in EXPENSE_CATEGORIES

    record = agent.get_record(result["record_id"])
    assert record is not None
    assert record.task_type == "expense"
    assert record.image_path == str(EXPENSE_IMAGE)
    assert record.analysis.expense_total_amount_in_euros > 0


def test_process_text_uses_message_history_to_route_delete_and_remove_latest_expense_end_to_end(tmp_path):
    assert EXPENSE_IMAGE.is_file()
    _require_openai_api_key()

    agent = PictoAgent(SqliteDatabase(tmp_path / "workflow.db"))
    context = _make_context()

    first_result = _run_or_skip_for_openai_connectivity(lambda: agent.process_image(str(EXPENSE_IMAGE)))
    _remember_expense_turn(context, "Sent a receipt photo.", first_result)

    metadata = _build_followup_metadata(context)
    assert metadata["recent_history"] == get_recent_history(context)
    assert [item["role"] for item in metadata["recent_history"]] == ["user", "assistant"]

    delete_result = _run_or_skip_for_openai_connectivity(
        lambda: agent.process_text(
            "Please delete this expense, it was a mistake.",
            metadata=metadata,
        )
    )

    assert delete_result["workflow_type"] == "delete_latest_entry"
    assert delete_result["task_type"] == "expense"
    assert delete_result["record_id"] == first_result["record_id"]

    agent.delete_record(delete_result["record_id"])

    assert agent.get_record(first_result["record_id"]) is None


def test_process_text_uses_message_history_to_change_previous_expense_category_end_to_end(tmp_path):
    assert EXPENSE_IMAGE.is_file()
    _require_openai_api_key()

    agent = PictoAgent(SqliteDatabase(tmp_path / "workflow.db"))
    context = _make_context()

    first_result = _run_or_skip_for_openai_connectivity(lambda: agent.process_image(str(EXPENSE_IMAGE)))
    _remember_expense_turn(context, "Sent a receipt photo.", first_result)

    correction_result = _run_or_skip_for_openai_connectivity(
        lambda: agent.process_text(
            "Actually put this under bakery instead.",
            metadata=_build_followup_metadata(context),
        )
    )

    assert correction_result["workflow_type"] == "expense_correction"
    assert correction_result["task_type"] == "expense"
    assert correction_result["record_id"] == first_result["record_id"]
    assert correction_result["analysis"]["category"] == "Bäcker"
    assert correction_result["analysis"]["description"].strip()
    assert correction_result["analysis"]["expense_total_amount_in_euros"] > 0

    agent.update_expense_record(correction_result["record_id"], correction_result["analysis"])

    updated_record = agent.get_record(first_result["record_id"])
    assert updated_record is not None
    assert updated_record.analysis.category == "Bäcker"


def test_process_text_uses_message_history_to_change_previous_expense_amount_end_to_end(tmp_path):
    assert EXPENSE_IMAGE.is_file()
    _require_openai_api_key()

    agent = PictoAgent(SqliteDatabase(tmp_path / "workflow.db"))
    context = _make_context()

    first_result = _run_or_skip_for_openai_connectivity(lambda: agent.process_image(str(EXPENSE_IMAGE)))
    _remember_expense_turn(context, "Sent a receipt photo.", first_result)

    correction_result = _run_or_skip_for_openai_connectivity(
        lambda: agent.process_text(
            "Actually the amount was 10 euros.",
            metadata=_build_followup_metadata(context),
        )
    )

    assert correction_result["workflow_type"] == "expense_correction"
    assert correction_result["task_type"] == "expense"
    assert correction_result["record_id"] == first_result["record_id"]
    assert correction_result["analysis"]["expense_total_amount_in_euros"] == pytest.approx(10.0, abs=0.5)
    assert correction_result["analysis"]["category"] in EXPENSE_CATEGORIES

    agent.update_expense_record(correction_result["record_id"], correction_result["analysis"])

    updated_record = agent.get_record(first_result["record_id"])
    assert updated_record is not None
    assert updated_record.analysis.expense_total_amount_in_euros == pytest.approx(10.0, abs=0.5)

def test_expense_text_query_uses_expense_workflow_end_to_end(tmp_path):
   
    agent = PictoAgent(SqliteDatabase(tmp_path / "workflow.db"))

    result = agent.process_text("What are the total expenses in January on groceries (Lebensmittel)?")

    assert result["workflow_type"] == "expense_query"
    assert result["explanation"].strip()
    assert "fact_expenses" in result["sql_query"].lower()
    assert result["response_template"].strip()
    guarded_query = validate_readonly_query(result["sql_query"], ("fact_expenses",))
    assert guarded_query.lower().startswith("select")
    assert "$1" in guarded_query

