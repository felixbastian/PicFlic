import os

import pytest

from src.agent import PictoAgent
from src.db import SqliteDatabase
from src.models import ImageAnalysis, MacroBreakdown


def _mock_analysis(image_path: str, metadata: dict | None = None) -> ImageAnalysis:
    if "beer" in image_path:
        return ImageAnalysis(
            category="drink",
            calories=150.0,
            macros=MacroBreakdown(carbs=12.0, protein=1.0, fat=0.0),
            tags=["alcoholic"],
            alcohol_units=1.5,
        )

    return ImageAnalysis(
        category="food",
        calories=850.0,
        macros=MacroBreakdown(carbs=80.0, protein=25.0, fat=45.0),
        tags=["fast_food"],
        alcohol_units=0.0,
    )


def test_process_image_stores_record(tmp_path, monkeypatch):
    monkeypatch.setattr("src.agent.analyze_image", _mock_analysis)
    db_path = tmp_path / "records.db"
    db = SqliteDatabase(db_path)
    agent = PictoAgent(db)

    result = agent.process_image("beer-pint.png")

    assert result["analysis"]["category"] == "drink"
    assert result["analysis"]["alcohol_units"] > 0

    records = agent.list_records()
    assert len(records) == 1
    assert records[0].image_path == "beer-pint.png"
    assert records[0].analysis.category == "drink"


def test_get_record_by_id(tmp_path, monkeypatch):
    monkeypatch.setattr("src.agent.analyze_image", _mock_analysis)
    db_path = tmp_path / "records.db"
    db = SqliteDatabase(db_path)
    agent = PictoAgent(db)

    first = agent.process_image("pizza.png")
    record_id = first["record_id"]

    record = agent.get_record(record_id)
    assert record is not None
    assert record.id == record_id
    assert record.analysis.category == "food"


def test_process_image_with_real_analyzer_fills_values(tmp_path):
    db_path = tmp_path / "records.db"
    db = SqliteDatabase(db_path)
    agent = PictoAgent(db)

    result = agent.process_image("sample_images/beer-pint.png")

    assert result["record_id"]
    assert result["analysis"]["category"]
    assert result["analysis"]["calories"] > 0
    assert result["analysis"]["macros"]
    assert sum(result["analysis"]["macros"].values()) > 0
    assert result["analysis"]["alcohol_units"] >= 0

    record = agent.get_record(result["record_id"])
    assert record is not None
    assert record.analysis.category
    assert record.analysis.calories > 0
