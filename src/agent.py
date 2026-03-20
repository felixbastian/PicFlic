"""An orchestrated agent that routes images and text to specialized trackers."""

from __future__ import annotations

from typing import Any, Dict

from langgraph.graph import StateGraph
from typing_extensions import NotRequired, TypedDict

from .db import SqliteDatabase
from .models import ExpenseAnalysis, ImageRecord, NutritionAnalysis, TrackingTaskType
from .query_utils import build_expense_query_plan, build_nutrition_query_plan, route_text_workflow
from .utils import analyze_expense_receipt, analyze_nutrition_image, route_image_task


class _PictoState(TypedDict):
    image_path: str
    metadata: Dict[str, Any]
    task_type: NotRequired[TrackingTaskType]
    analysis: Dict[str, Any]
    record_id: NotRequired[str]


class _TextState(TypedDict):
    text: str
    metadata: Dict[str, Any]
    workflow_type: NotRequired[str]
    explanation: NotRequired[str]
    sql_query: NotRequired[str]
    response_template: NotRequired[str]


class _PictoContext(TypedDict):
    db: SqliteDatabase


def _load(state: _PictoState, runtime: Any) -> dict:
    # Ensure we always start with a clean analysis payload.
    return {"analysis": {}, "metadata": state.get("metadata", {})}


def _route(state: _PictoState, runtime: Any) -> dict:
    decision = route_image_task(state["image_path"], state.get("metadata"))
    return {"task_type": decision.task_type}


def _analyze_nutrition(state: _PictoState, runtime: Any) -> dict:
    analysis = analyze_nutrition_image(state["image_path"], state.get("metadata"))
    return {"task_type": "nutrition", "analysis": analysis.to_dict()}


def _analyze_expense(state: _PictoState, runtime: Any) -> dict:
    analysis = analyze_expense_receipt(state["image_path"], state.get("metadata"))
    return {"task_type": "expense", "analysis": analysis.to_dict()}


def _next_step(state: _PictoState) -> str:
    return "analyze_expense" if state["task_type"] == "expense" else "analyze_nutrition"


def _store(state: _PictoState, runtime: Any) -> dict:
    db: SqliteDatabase = runtime.context["db"]
    task_type = state["task_type"]
    if task_type == "expense":
        analysis = ExpenseAnalysis.model_validate(state["analysis"])
    else:
        analysis = NutritionAnalysis.model_validate(state["analysis"])
    record = ImageRecord.from_analysis(state["image_path"], task_type, analysis)
    db.store_record(record)
    return {"task_type": task_type, "analysis": record.analysis.to_dict(), "record_id": record.id}


def _load_text(state: _TextState, runtime: Any) -> dict:
    return {"metadata": state.get("metadata", {})}


def _route_text(state: _TextState, runtime: Any) -> dict:
    decision = route_text_workflow(state["text"], state.get("metadata"))
    return {"workflow_type": decision.workflow_type}


def _build_expense_text_query(state: _TextState, runtime: Any) -> dict:
    plan = build_expense_query_plan(state["text"], state.get("metadata"))
    return plan.model_dump()


def _build_nutrition_text_query(state: _TextState, runtime: Any) -> dict:
    plan = build_nutrition_query_plan(state["text"], state.get("metadata"))
    return plan.model_dump()


def _echo_text(state: _TextState, runtime: Any) -> dict:
    return {"workflow_type": "echo"}


class PictoAgent:
    """An agent that routes photos and text to the correct specialized workflow."""

    def __init__(self, db: SqliteDatabase):
        self._db = db
        self._image_graph = self._build_image_graph()
        self._text_graph = self._build_text_graph()

    def _build_image_graph(self) -> StateGraph[_PictoState, _PictoContext, _PictoState, dict]:
        graph = StateGraph(state_schema=_PictoState, context_schema=_PictoContext)
        graph.add_node("load", _load)
        graph.add_node("route", _route)
        graph.add_node("analyze_nutrition", _analyze_nutrition)
        graph.add_node("analyze_expense", _analyze_expense)
        graph.add_node("store", _store)
        graph.add_edge("load", "route")
        graph.add_conditional_edges("route", _next_step)
        graph.add_edge("analyze_nutrition", "store")
        graph.add_edge("analyze_expense", "store")
        graph.set_entry_point("load")
        graph.set_finish_point("store")
        return graph.compile()

    def _build_text_graph(self) -> StateGraph[_TextState, _PictoContext, _TextState, dict]:
        graph = StateGraph(state_schema=_TextState, context_schema=_PictoContext)
        graph.add_node("load_text", _load_text)
        graph.add_node("route_text", _route_text)
        graph.add_node("build_expense_text_query", _build_expense_text_query)
        graph.add_node("build_nutrition_text_query", _build_nutrition_text_query)
        graph.add_node("echo_text", _echo_text)
        graph.add_edge("load_text", "route_text")
        graph.add_conditional_edges(
            "route_text",
            lambda state: state["workflow_type"],
            {
                "expense_query": "build_expense_text_query",
                "nutrition_query": "build_nutrition_text_query",
                "echo": "echo_text",
            },
        )
        graph.set_entry_point("load_text")
        graph.set_finish_point("build_expense_text_query")
        graph.set_finish_point("build_nutrition_text_query")
        graph.set_finish_point("echo_text")
        return graph.compile()

    def process_image(self, image_path: str, metadata: dict[str, Any] | None = None) -> dict:
        """Analyze an image and store the result in the database."""
        metadata = metadata or {}
        return self._image_graph.invoke(
            {"image_path": image_path, "metadata": metadata, "analysis": {}, "task_type": "nutrition"},
            context={"db": self._db},
        )

    def process_text(self, text: str, metadata: dict[str, Any] | None = None) -> dict:
        """Route a text message to echo or to a guarded SQL-planning workflow."""
        metadata = metadata or {}
        return self._text_graph.invoke(
            {"text": text, "metadata": metadata, "workflow_type": "echo"},
            context={"db": self._db},
        )

    def list_records(self) -> list[ImageRecord]:
        return self._db.list_records()

    def get_record(self, record_id: str) -> ImageRecord | None:
        return self._db.get_record(record_id)
