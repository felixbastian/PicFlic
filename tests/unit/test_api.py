from fastapi.testclient import TestClient

from src.agent import PictoAgent
from src.api import app, get_agent
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
        calories=500.0,
        macros=MacroBreakdown(carbs=40.0, protein=20.0, fat=18.0),
        tags=["meal"],
        alcohol_units=0.0,
    )


def test_analyze_and_fetch_records_via_api(tmp_path, monkeypatch):
    monkeypatch.setattr("src.agent.analyze_image", _mock_analysis)
    database = SqliteDatabase(tmp_path / "api-records.db")
    agent = PictoAgent(database)
    app.dependency_overrides[get_agent] = lambda: agent

    client = TestClient(app)
    analyze_response = client.post(
        "/records/analyze",
        json={"image_path": "sample_images/beer-pint.png", "metadata": {"source": "test"}},
    )

    assert analyze_response.status_code == 200
    record = analyze_response.json()
    assert record["analysis"]["category"] == "drink"
    assert record["analysis"]["calories"] > 0

    list_response = client.get("/records")
    assert list_response.status_code == 200
    records = list_response.json()
    assert len(records) == 1

    get_response = client.get(f"/records/{record['id']}")
    assert get_response.status_code == 200
    assert get_response.json()["id"] == record["id"]

    app.dependency_overrides.clear()


def test_get_missing_record_returns_404(tmp_path):
    database = SqliteDatabase(tmp_path / "api-records.db")
    agent = PictoAgent(database)
    app.dependency_overrides[get_agent] = lambda: agent

    client = TestClient(app)
    response = client.get("/records/missing-record")

    assert response.status_code == 404
    assert response.json()["detail"] == "Record not found."

    app.dependency_overrides.clear()
