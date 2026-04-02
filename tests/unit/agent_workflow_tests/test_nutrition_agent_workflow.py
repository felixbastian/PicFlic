from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from openai import APIConnectionError, APITimeoutError

from src.agent import PictoAgent
from src.bot import (
    format_result_response,
    get_latest_nutrition_result,
    get_latest_tracking_result,
    get_recent_history,
    remember_latest_nutrition_result,
    remember_latest_tracking_result,
    remember_text_turn,
)
from src.config import load_config
from src.db import SqliteDatabase


PROJECT_ROOT = Path(__file__).resolve().parents[3]
NUTRITION_IMAGE = PROJECT_ROOT / "sample_images" / "croissant.jpeg"
MINI_PASTRY_IMAGE = PROJECT_ROOT / "sample_images" / "mini-pastry.png"


def _require_openai_api_key() -> None:
    load_config.cache_clear()
    config = load_config()
    if not config.openai_api_key:
        pytest.skip("OPENAI_API_KEY is required for nutrition workflow end-to-end validation.")


def _make_context() -> SimpleNamespace:
    return SimpleNamespace(application=SimpleNamespace(bot_data={}), user_data={})


def _run_or_skip_for_openai_connectivity(fn):
    try:
        return fn()
    except (APIConnectionError, APITimeoutError) as exc:
        pytest.skip(f"OpenAI API is unreachable for nutrition workflow end-to-end validation: {exc}")


def _remember_nutrition_turn(
    context: SimpleNamespace,
    user_text: str,
    result: dict,
    *,
    workflow_type: str = "nutrition_tracking",
) -> None:
    remember_latest_tracking_result(context, result)
    remember_latest_nutrition_result(context, result)
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

    latest_nutrition_result = get_latest_nutrition_result(context)
    if latest_nutrition_result is not None:
        metadata["latest_nutrition_result"] = latest_nutrition_result

    return metadata


def test_process_text_tracks_a_simple_nutrition_entry_end_to_end(tmp_path):
    _require_openai_api_key()
    agent = PictoAgent(SqliteDatabase(tmp_path / "workflow.db"))

    result = _run_or_skip_for_openai_connectivity(
        lambda: agent.process_text("I ate a pain au chocolat.", metadata={"recent_history": []})
    )

    assert result["workflow_type"] == "nutrition_tracking"
    assert result["task_type"] == "nutrition"
    assert result["record_id"]
    assert result["analysis"]["ingredients"]
    assert result["analysis"]["calories"] > 0
    assert result["analysis"]["macros"]["carbs"] > 0
    assert result["analysis"]["macros"]["fat"] >= 0

    record = agent.get_record(result["record_id"])
    assert record is not None
    assert record.task_type == "nutrition"
    assert record.image_path == "text://I ate a pain au chocolat."
    assert record.analysis.calories > 0


def test_process_text_uses_message_history_to_route_delete_and_remove_entry_end_to_end(tmp_path):
    _require_openai_api_key()
    agent = PictoAgent(SqliteDatabase(tmp_path / "workflow.db"))
    context = _make_context()

    first_message = "I ate a pain au chocolat."
    first_result = _run_or_skip_for_openai_connectivity(
        lambda: agent.process_text(first_message, metadata={"recent_history": []})
    )
    _remember_nutrition_turn(context, first_message, first_result)

    metadata = _build_followup_metadata(context)
    assert metadata["recent_history"] == get_recent_history(context)
    assert [item["role"] for item in metadata["recent_history"]] == ["user", "assistant"]

    delete_result = _run_or_skip_for_openai_connectivity(
        lambda: agent.process_text(
            "Please remove that, it was a mistake.",
            metadata=metadata,
        )
    )

    assert delete_result["workflow_type"] == "delete_latest_entry"
    assert delete_result["task_type"] == "nutrition"
    assert delete_result["record_id"] == first_result["record_id"]

    agent.delete_record(delete_result["record_id"])

    assert agent.get_record(first_result["record_id"]) is None


def test_process_text_uses_message_history_to_scale_previous_entry_end_to_end(tmp_path):
    _require_openai_api_key()
    agent = PictoAgent(SqliteDatabase(tmp_path / "workflow.db"))
    context = _make_context()

    first_message = "Ate a mini croissant"
    first_result = _run_or_skip_for_openai_connectivity(
        lambda: agent.process_text(first_message, metadata={"recent_history": []})
    )
    _remember_nutrition_turn(context, first_message, first_result)

    correction_result = _run_or_skip_for_openai_connectivity(
        lambda: agent.process_text(
            "actually I ate 10",
            metadata=_build_followup_metadata(context),
        )
    )

    original_calories = first_result["analysis"]["calories"]

    assert correction_result["workflow_type"] == "nutrition_correction"
    assert correction_result["task_type"] == "nutrition"
    assert correction_result["record_id"] == first_result["record_id"]
    assert correction_result["analysis"]["item_count"] == 10
    assert correction_result["analysis"]["calories"] == pytest.approx(
        original_calories * 10,
        rel=0.2,
        abs=40.0,
    )
    assert correction_result["analysis"]["calories"] / correction_result["analysis"]["item_count"] == pytest.approx(
        original_calories,
        rel=0.2,
        abs=10.0,
    )

    agent.update_nutrition_record(correction_result["record_id"], correction_result["analysis"])

    updated_record = agent.get_record(first_result["record_id"])
    assert updated_record is not None
    assert updated_record.analysis.calories == pytest.approx(
        original_calories * 10,
        rel=0.2,
        abs=40.0,
    )


def test_process_text_uses_message_history_to_add_ingredient_to_image_entry_end_to_end(tmp_path):
    assert NUTRITION_IMAGE.is_file()
    _require_openai_api_key()

    agent = PictoAgent(SqliteDatabase(tmp_path / "workflow.db"))
    context = _make_context()

    first_result = _run_or_skip_for_openai_connectivity(lambda: agent.process_image(str(NUTRITION_IMAGE)))
    _remember_nutrition_turn(
        context,
        "Sent a photo of the pastry.",
        first_result,
    )

    correction_result = _run_or_skip_for_openai_connectivity(
        lambda: agent.process_text(
            "actually there was also blue cheese inside",
            metadata=_build_followup_metadata(context),
        )
    )

    corrected_ingredient_names = [
        ingredient["name"].lower()
        for ingredient in correction_result["analysis"]["ingredients"]
    ]

    assert correction_result["workflow_type"] == "nutrition_correction"
    assert correction_result["task_type"] == "nutrition"
    assert correction_result["record_id"] == first_result["record_id"]
    assert any("blue cheese" in ingredient_name for ingredient_name in corrected_ingredient_names)
    assert len(correction_result["analysis"]["ingredients"]) >= len(first_result["analysis"]["ingredients"])
    assert correction_result["analysis"]["calories"] > first_result["analysis"]["calories"]

    agent.update_nutrition_record(correction_result["record_id"], correction_result["analysis"])

    updated_record = agent.get_record(first_result["record_id"])
    assert updated_record is not None
    assert any("blue cheese" in ingredient.name.lower() for ingredient in updated_record.analysis.ingredients)
    assert updated_record.analysis.calories > first_result["analysis"]["calories"]


def test_process_image_caption_scales_item_count_end_to_end(tmp_path):
    assert NUTRITION_IMAGE.is_file()
    _require_openai_api_key()

    agent = PictoAgent(SqliteDatabase(tmp_path / "workflow.db"))

    baseline_result = _run_or_skip_for_openai_connectivity(lambda: agent.process_image(str(NUTRITION_IMAGE)))
    captioned_result = _run_or_skip_for_openai_connectivity(
        lambda: agent.process_image(
            str(NUTRITION_IMAGE),
            metadata={"caption": "2 of those"},
        )
    )

    assert baseline_result["task_type"] == "nutrition"
    assert captioned_result["task_type"] == "nutrition"
    assert captioned_result["analysis"]["item_count"] == 2
    assert captioned_result["analysis"]["calories"] > baseline_result["analysis"]["calories"]
    assert captioned_result["analysis"]["calories"] == pytest.approx(
        baseline_result["analysis"]["calories"] * 2,
        rel=0.35,
        abs=150.0,
    )

    response = format_result_response(captioned_result)
    assert "<b>Amount:</b> 2" in response
    assert "<b>Calories:</b> 2 * " in response

    stored_record = agent.get_record(captioned_result["record_id"])
    assert stored_record is not None
    assert stored_record.analysis.calories == pytest.approx(
        captioned_result["analysis"]["calories"],
        rel=0.01,
        abs=1.0,
    )


def test_process_image_recognizes_mini_pastry_as_small_portion_end_to_end(tmp_path):
    assert MINI_PASTRY_IMAGE.is_file()
    _require_openai_api_key()

    agent = PictoAgent(SqliteDatabase(tmp_path / "workflow.db"))

    result = _run_or_skip_for_openai_connectivity(lambda: agent.process_image(str(MINI_PASTRY_IMAGE)))

    assert result["task_type"] == "nutrition"
    assert result["record_id"]
    assert result["analysis"]["ingredients"]
    assert result["analysis"]["calories"] > 0
    assert result["analysis"]["calories"] < 201

    stored_record = agent.get_record(result["record_id"])
    assert stored_record is not None
    assert stored_record.analysis.calories < 201
