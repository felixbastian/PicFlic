"""A lightweight agent that turns image inputs into stored nutritional records."""

from __future__ import annotations

from typing import Any, Dict

from langgraph.graph import StateGraph
from typing_extensions import NotRequired, TypedDict

from .db import SqliteDatabase
from .models import ImageAnalysis, ImageRecord
from .utils import analyze_image


class _PictoState(TypedDict):
    image_path: str
    metadata: Dict[str, Any]
    analysis: Dict[str, Any]
    record_id: NotRequired[str]


class _PictoContext(TypedDict):
    db: SqliteDatabase


def _load(state: _PictoState, runtime: Any) -> dict:
    # Ensure we always start with a clean analysis payload.
    return {"analysis": {}, "metadata": state.get("metadata", {})}


def _analyze(state: _PictoState, runtime: Any) -> dict:
    analysis = analyze_image(state["image_path"], state.get("metadata"))
    return {"analysis": analysis.to_dict()}


def _store(state: _PictoState, runtime: Any) -> dict:
    db: SqliteDatabase = runtime.context["db"]
    analysis = ImageAnalysis(**state["analysis"])
    record = ImageRecord.from_analysis(state["image_path"], analysis)
    db.store_record(record)
    return {"analysis": record.analysis.to_dict(), "record_id": record.id}


class PictoAgent:
    """An agent that processes a photo and stores a nutrition record.

    This class wraps a simple `langgraph.StateGraph` pipeline and exposes a
    small public API for interacting with the underlying storage.
    """

    def __init__(self, db: SqliteDatabase):
        self._db = db
        self._graph = self._build_graph()

    def _build_graph(self) -> StateGraph[_PictoState, _PictoContext, _PictoState, dict]:
        graph = StateGraph(state_schema=_PictoState, context_schema=_PictoContext)
        graph.add_node("load", _load)
        graph.add_node("analyze", _analyze)
        graph.add_node("store", _store)
        graph.add_edge("load", "analyze")
        graph.add_edge("analyze", "store")
        graph.set_entry_point("load")
        graph.set_finish_point("store")
        return graph.compile()

    def process_image(self, image_path: str, metadata: dict[str, Any] | None = None) -> dict:
        """Analyze an image and store the result in the database."""
        metadata = metadata or {}
        result = self._graph.invoke(
            {"image_path": image_path, "metadata": metadata, "analysis": {}},
            context={"db": self._db},
        )
        return result

    def list_records(self) -> list[ImageRecord]:
        return self._db.list_records()

    def get_record(self, record_id: str) -> ImageRecord | None:
        return self._db.get_record(record_id)
