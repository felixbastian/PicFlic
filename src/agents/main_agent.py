"""Main orchestrated agent for image and text workflows."""

from __future__ import annotations

import logging
from typing import Any, Dict

from langgraph.graph import StateGraph
from typing_extensions import NotRequired, TypedDict

from ..db import SqliteDatabase
from ..models import ExpenseAnalysis, ImageRecord, NutritionAnalysis, RecipeAnalysis, TrackingTaskType
from ..query_utils import (
    build_expense_query_plan,
    build_nutrition_query_plan,
    build_recipe_collection_response,
    build_vocabulary_response,
    route_text_workflow,
)
from ..utils import (
    analyze_expense_receipt,
    analyze_nutrition_image,
    analyze_nutrition_text,
    analyze_recipe_image,
    revise_nutrition_analysis,
    route_image_task,
)

logger = logging.getLogger(__name__)


class _ImageState(TypedDict):
    image_path: str
    metadata: Dict[str, Any]
    task_type: NotRequired[TrackingTaskType]
    analysis: Dict[str, Any]
    record_id: NotRequired[str]


class _TextState(TypedDict):
    text: str
    metadata: Dict[str, Any]
    workflow_type: NotRequired[str]
    task_type: NotRequired[TrackingTaskType]
    analysis: NotRequired[Dict[str, Any]]
    record_id: NotRequired[str]
    meal_id: NotRequired[str]
    expense_id: NotRequired[str]
    dish_id: NotRequired[str]
    explanation: NotRequired[str]
    sql_query: NotRequired[str]
    response_template: NotRequired[str]
    assistant_reply: NotRequired[str]
    store_vocabulary: NotRequired[bool]
    french_word: NotRequired[str | None]
    english_description: NotRequired[str | None]
    name: NotRequired[str]
    description: NotRequired[str]
    carb_source: NotRequired[str | None]
    vegetarian: NotRequired[bool | None]
    meat: NotRequired[str | None]
    frequency_rotation: NotRequired[str | None]


class _MainContext(TypedDict):
    db: SqliteDatabase


class MainAgent:
    """An agent that routes photos and text to the correct specialized workflow."""

    def __init__(self, db: SqliteDatabase):
        self._db = db
        self._image_graph = self._build_image_graph()
        self._text_graph = self._build_text_graph()

    def _build_image_graph(self) -> StateGraph[_ImageState, _MainContext, _ImageState, dict]:
        graph = StateGraph(state_schema=_ImageState, context_schema=_MainContext)
        graph.add_node("load", _load_image)
        graph.add_node("route", _route_image)
        graph.add_node("analyze_nutrition", _analyze_nutrition)
        graph.add_node("analyze_expense", _analyze_expense)
        graph.add_node("analyze_recipe", _analyze_recipe)
        graph.add_node("store", _store_image_record)
        graph.add_edge("load", "route")
        graph.add_conditional_edges("route", _next_image_step)
        graph.add_edge("analyze_nutrition", "store")
        graph.add_edge("analyze_expense", "store")
        graph.add_edge("analyze_recipe", "store")
        graph.set_entry_point("load")
        graph.set_finish_point("store")
        return graph.compile()

    def _build_text_graph(self) -> StateGraph[_TextState, _MainContext, _TextState, dict]:
        graph = StateGraph(state_schema=_TextState, context_schema=_MainContext)
        graph.add_node("load_text", _load_text)
        graph.add_node("route_text", _route_text)
        graph.add_node("build_delete_latest_entry", _build_delete_latest_entry)
        graph.add_node("build_expense_text_query", _build_expense_text_query)
        graph.add_node("build_nutrition_correction", _build_nutrition_correction)
        graph.add_node("build_nutrition_text_query", _build_nutrition_text_query)
        graph.add_node("analyze_nutrition_text", _analyze_nutrition_text)
        graph.add_node("store_nutrition_text_record", _store_nutrition_text_record)
        graph.add_node("build_vocabulary_text_response", _build_vocabulary_text_response)
        graph.add_node("build_recipe_collection_text_response", _build_recipe_collection_text_response)
        graph.add_node("echo_text", _echo_text)
        graph.add_edge("load_text", "route_text")
        graph.add_conditional_edges(
            "route_text",
            lambda state: state["workflow_type"],
            {
                "delete_latest_entry": "build_delete_latest_entry",
                "expense_query": "build_expense_text_query",
                "nutrition_correction": "build_nutrition_correction",
                "nutrition_query": "build_nutrition_text_query",
                "nutrition_tracking": "analyze_nutrition_text",
                "vocabulary": "build_vocabulary_text_response",
                "recipe_collection": "build_recipe_collection_text_response",
                "echo": "echo_text",
            },
        )
        graph.add_edge("analyze_nutrition_text", "store_nutrition_text_record")
        graph.set_entry_point("load_text")
        graph.set_finish_point("build_delete_latest_entry")
        graph.set_finish_point("build_expense_text_query")
        graph.set_finish_point("build_nutrition_correction")
        graph.set_finish_point("build_nutrition_text_query")
        graph.set_finish_point("store_nutrition_text_record")
        graph.set_finish_point("build_vocabulary_text_response")
        graph.set_finish_point("build_recipe_collection_text_response")
        graph.set_finish_point("echo_text")
        return graph.compile()

    def process_image(self, image_path: str, metadata: dict[str, Any] | None = None) -> dict:
        metadata = metadata or {}
        return self._image_graph.invoke(
            {"image_path": image_path, "metadata": metadata, "analysis": {}, "task_type": "nutrition"},
            context={"db": self._db},
        )

    def process_text(self, text: str, metadata: dict[str, Any] | None = None) -> dict:
        metadata = metadata or {}
        return self._text_graph.invoke(
            {"text": text, "metadata": metadata, "workflow_type": "echo"},
            context={"db": self._db},
        )

    def list_records(self) -> list[ImageRecord]:
        return self._db.list_records()

    def get_record(self, record_id: str) -> ImageRecord | None:
        return self._db.get_record(record_id)

    def update_nutrition_record(
        self,
        record_id: str,
        analysis: NutritionAnalysis | dict[str, Any],
    ) -> ImageRecord:
        record = self._db.get_record(record_id)
        if record is None:
            raise ValueError(f"Record {record_id} not found.")
        if record.task_type != "nutrition":
            raise ValueError(f"Record {record_id} is not a nutrition record.")

        normalized = analysis
        if isinstance(analysis, dict):
            normalized = NutritionAnalysis.model_validate(analysis)

        updated_record = ImageRecord(
            id=record.id,
            image_path=record.image_path,
            task_type=record.task_type,
            analysis=normalized,
            created_at=record.created_at,
        )
        self._db.store_record(updated_record)
        logger.info(
            "Updated local nutrition record",
            extra={"event": "agent_record_updated", "record_id": record_id, "task_type": "nutrition"},
        )
        return updated_record

    def delete_record(self, record_id: str) -> None:
        self._db.delete_record(record_id)
        logger.info(
            "Deleted local tracking record",
            extra={"event": "agent_record_deleted", "record_id": record_id},
        )
    
def _load_image(state: _ImageState, runtime: Any) -> dict:
    return {"analysis": {}, "metadata": state.get("metadata", {})}


def _route_image(state: _ImageState, runtime: Any) -> dict:
    logger.info(
        "Routing image workflow",
        extra={"event": "agent_route_image_input", "image_path": state["image_path"], "metadata": state.get("metadata", {})},
    )
    decision = route_image_task(state["image_path"], state.get("metadata"))
    logger.info(
        "Image workflow routed",
        extra={
            "event": "agent_route_image_output",
            "task_type": decision.task_type,
            "next_node": "analyze_expense" if decision.task_type == "expense" else "analyze_nutrition",
        },
    )
    return {"task_type": decision.task_type}


def _analyze_nutrition(state: _ImageState, runtime: Any) -> dict:
    analysis = analyze_nutrition_image(state["image_path"], state.get("metadata"))
    logger.info(
        "Completed nutrition analysis node",
        extra={"event": "agent_nutrition_analysis", "analysis": analysis.to_dict()},
    )
    return {"task_type": "nutrition", "analysis": analysis.to_dict()}


def _analyze_expense(state: _ImageState, runtime: Any) -> dict:
    analysis = analyze_expense_receipt(state["image_path"], state.get("metadata"))
    logger.info(
        "Completed expense analysis node",
        extra={"event": "agent_expense_analysis", "analysis": analysis.to_dict()},
    )
    return {"task_type": "expense", "analysis": analysis.to_dict()}


def _analyze_recipe(state: _ImageState, runtime: Any) -> dict:
    analysis = analyze_recipe_image(state["image_path"], state.get("metadata"))
    logger.info(
        "Completed recipe analysis node",
        extra={"event": "agent_recipe_analysis", "analysis": analysis.to_dict()},
    )
    return {"task_type": "recipe", "analysis": analysis.to_dict()}


def _next_image_step(state: _ImageState) -> str:
    if state["task_type"] == "expense":
        return "analyze_expense"
    if state["task_type"] == "recipe":
        return "analyze_recipe"
    return "analyze_nutrition"


def _store_image_record(state: _ImageState, runtime: Any) -> dict:
    db: SqliteDatabase = runtime.context["db"]
    task_type = state["task_type"]
    record = _store_tracking_record(state["image_path"], task_type, state["analysis"])
    db.store_record(record)
    logger.info(
        "Stored image workflow record",
        extra={"event": "agent_record_stored", "record_id": record.id, "task_type": task_type},
    )
    return {"task_type": task_type, "analysis": record.analysis.to_dict(), "record_id": record.id}


def _load_text(state: _TextState, runtime: Any) -> dict:
    return {"metadata": state.get("metadata", {})}


def _route_text(state: _TextState, runtime: Any) -> dict:
    logger.info(
        "Routing text workflow",
        extra={"event": "agent_route_text_input", "text": state["text"], "metadata": state.get("metadata", {})},
    )
    decision = route_text_workflow(state["text"], state.get("metadata"))
    logger.info(
        "Text workflow routed",
        extra={"event": "agent_route_text_output", "workflow_type": decision.workflow_type},
    )
    return {"workflow_type": decision.workflow_type}


def _build_expense_text_query(state: _TextState, runtime: Any) -> dict:
    plan = build_expense_query_plan(state["text"], state.get("metadata"))
    logger.info(
        "Built expense query plan",
        extra={
            "event": "agent_expense_query_plan",
            "explanation": plan.explanation,
            "sql_query": plan.sql_query,
            "response_template": plan.response_template,
        },
    )
    return plan.model_dump()


def _build_nutrition_text_query(state: _TextState, runtime: Any) -> dict:
    plan = build_nutrition_query_plan(state["text"], state.get("metadata"))
    logger.info(
        "Built nutrition query plan",
        extra={
            "event": "agent_nutrition_query_plan",
            "explanation": plan.explanation,
            "sql_query": plan.sql_query,
            "response_template": plan.response_template,
        },
    )
    return plan.model_dump()


def _build_nutrition_correction(state: _TextState, runtime: Any) -> dict:
    latest_result = _get_latest_nutrition_result_metadata(state.get("metadata"))
    if latest_result is None:
        raise ValueError("Nutrition correction requested without latest nutrition context.")

    analysis = revise_nutrition_analysis(state["text"], latest_result["analysis"])
    logger.info(
        "Built nutrition correction result",
        extra={
            "event": "agent_nutrition_correction",
            "record_id": latest_result.get("record_id"),
            "meal_id": latest_result.get("meal_id"),
            "analysis": analysis.to_dict(),
        },
    )
    return {
        "workflow_type": "nutrition_correction",
        "task_type": "nutrition",
        "analysis": analysis.to_dict(),
        "record_id": str(latest_result.get("record_id") or "").strip(),
        "meal_id": str(latest_result.get("meal_id") or "").strip(),
    }


def _build_delete_latest_entry(state: _TextState, runtime: Any) -> dict:
    latest_result = _get_latest_tracking_result_metadata(state.get("metadata"))
    if latest_result is None:
        return {
            "workflow_type": "delete_latest_entry",
            "task_type": "",
            "record_id": "",
            "meal_id": "",
            "expense_id": "",
            "dish_id": "",
        }

    task_type = str(latest_result.get("task_type") or "").strip()
    if task_type not in {"nutrition", "expense", "recipe"}:
        return {
            "workflow_type": "delete_latest_entry",
            "task_type": "",
            "record_id": "",
            "meal_id": "",
            "expense_id": "",
            "dish_id": "",
        }

    logger.info(
        "Built delete-latest-entry result",
        extra={
            "event": "agent_delete_latest_entry",
            "task_type": task_type,
            "record_id": latest_result.get("record_id"),
            "meal_id": latest_result.get("meal_id"),
            "expense_id": latest_result.get("expense_id"),
            "dish_id": latest_result.get("dish_id"),
        },
    )
    return {
        "workflow_type": "delete_latest_entry",
        "task_type": task_type,
        "record_id": str(latest_result.get("record_id") or "").strip(),
        "meal_id": str(latest_result.get("meal_id") or "").strip(),
        "expense_id": str(latest_result.get("expense_id") or "").strip(),
        "dish_id": str(latest_result.get("dish_id") or "").strip(),
    }


def _analyze_nutrition_text(state: _TextState, runtime: Any) -> dict:
    analysis = analyze_nutrition_text(state["text"], state.get("metadata"))
    logger.info(
        "Completed nutrition text analysis node",
        extra={"event": "agent_nutrition_text_analysis", "analysis": analysis.to_dict()},
    )
    return {
        "workflow_type": "nutrition_tracking",
        "task_type": "nutrition",
        "analysis": analysis.to_dict(),
    }


def _store_nutrition_text_record(state: _TextState, runtime: Any) -> dict:
    db: SqliteDatabase = runtime.context["db"]
    record = _store_tracking_record(_build_text_record_source(state["text"]), "nutrition", state["analysis"])
    db.store_record(record)
    logger.info(
        "Stored text nutrition workflow record",
        extra={
            "event": "agent_record_stored",
            "record_id": record.id,
            "task_type": "nutrition",
            "workflow_type": "nutrition_tracking",
        },
    )
    return {
        "workflow_type": "nutrition_tracking",
        "task_type": "nutrition",
        "analysis": record.analysis.to_dict(),
        "record_id": record.id,
    }


def _echo_text(state: _TextState, runtime: Any) -> dict:
    logger.info("Selected echo text workflow", extra={"event": "agent_echo_workflow"})
    return {"workflow_type": "echo"}


def _build_vocabulary_text_response(state: _TextState, runtime: Any) -> dict:
    result = build_vocabulary_response(state["text"], state.get("metadata"))
    logger.info(
        "Built vocabulary workflow result",
        extra={
            "event": "agent_vocabulary_result",
            "store_vocabulary": result.store_vocabulary,
            "french_word": result.french_word,
            "english_description": result.english_description,
        },
    )
    return result.model_dump()


def _build_recipe_collection_text_response(state: _TextState, runtime: Any) -> dict:
    result = build_recipe_collection_response(state["text"], state.get("metadata"))
    logger.info(
        "Built recipe collection result",
        extra={
            "event": "agent_recipe_collection_result",
            "recipe_name": result.name,
            "carb_source": result.carb_source,
            "vegetarian": result.vegetarian,
            "meat": result.meat,
            "frequency_rotation": result.frequency_rotation,
        },
    )
    return result.model_dump()


def _get_latest_nutrition_result_metadata(metadata: Dict[str, Any] | None) -> Dict[str, Any] | None:
    if not isinstance(metadata, dict):
        return None
    latest_result = metadata.get("latest_nutrition_result")
    if not isinstance(latest_result, dict):
        return None
    if not isinstance(latest_result.get("analysis"), dict):
        return None
    return latest_result


def _get_latest_tracking_result_metadata(metadata: Dict[str, Any] | None) -> Dict[str, Any] | None:
    if not isinstance(metadata, dict):
        return None
    latest_result = metadata.get("latest_tracking_result")
    if not isinstance(latest_result, dict):
        return None
    task_type = str(latest_result.get("task_type") or "").strip()
    if not task_type:
        return None
    return latest_result


def _store_tracking_record(
    source_reference: str,
    task_type: TrackingTaskType,
    analysis_payload: Dict[str, Any],
) -> ImageRecord:
    if task_type == "expense":
        analysis = ExpenseAnalysis.model_validate(analysis_payload)
    elif task_type == "recipe":
        analysis = RecipeAnalysis.model_validate(analysis_payload)
    else:
        analysis = NutritionAnalysis.model_validate(analysis_payload)
    return ImageRecord.from_analysis(source_reference, task_type, analysis)


def _build_text_record_source(text: str) -> str:
    normalized = " ".join(text.strip().split())
    if not normalized:
        return "text://nutrition-entry"
    if len(normalized) > 180:
        normalized = f"{normalized[:177].rstrip()}..."
    return f"text://{normalized}"


PictoAgent = MainAgent

__all__ = ["MainAgent", "PictoAgent"]
