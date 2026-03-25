from types import SimpleNamespace

from src.config import AppConfig
from src.models import NutritionAnalysis, NutritionCorrectionResult
from src.openai_schema import build_strict_openai_schema
from src.utils import analyze_nutrition_image, correct_nutrition_analysis


def test_nutrition_schema_lists_ingredients_first():
    schema = NutritionAnalysis.model_json_schema()

    assert list(schema["properties"])[:2] == ["ingredients", "category"]


def test_nutrition_strict_schema_requires_item_count():
    schema = build_strict_openai_schema(NutritionAnalysis)

    assert list(schema["properties"])[:2] == ["ingredients", "category"]
    assert "item_count" in schema["required"]


def test_analyze_nutrition_image_uses_ingredient_first_prompt_and_user_note(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    class _FakeResponses:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                output_text=(
                    '{"ingredients":[{"name":"chicken","amount":"120 g","calories":198.0},'
                    '{"name":"avocado","amount":"half avocado","calories":110.0}],'
                    '"category":"food","calories":420.0,"macros":{"carbs":30.0,"protein":18.0,"fat":22.0},'
                    '"tags":["salad"],"alcohol_units":0.0}'
                )
            )

    class _FakeOpenAI:
        def __init__(self, api_key: str):
            assert api_key == "test-key"
            self.responses = _FakeResponses()

    monkeypatch.setattr(
        "src.utils.load_config",
        lambda: AppConfig(
            openai_api_key="test-key",
            openai_model="test-model",
            database_path=tmp_path / "test.db",
        ),
    )
    monkeypatch.setattr("src.utils.OpenAI", _FakeOpenAI)

    result = analyze_nutrition_image(
        "meal.jpg",
        metadata={
            "user_prompt": "Chicken salad with extra avocado",
            "source": "telegram",
        },
    )

    assert result.category == "food"
    assert result.calories == 420.0
    assert result.ingredients[0].name == "chicken"

    system_text = captured["input"][0]["content"][0]["text"]
    assert "First fill the ingredients field" in system_text
    assert "at most 2 words" in system_text
    assert "Use ~ instead of words like about" in system_text
    assert "top-level calories field" in system_text
    assert "user note" in system_text.lower()

    user_text = captured["input"][1]["content"][0]["text"]
    assert "User note: Chicken salad with extra avocado" in user_text
    assert 'Metadata: {"source": "telegram"}' in user_text


def test_analyze_nutrition_image_extracts_item_count_and_scales_result(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    class _FakeResponses:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                output_text=(
                    '{"ingredients":[{"name":"croissant","amount":"1 piece","calories":230.0}],'
                    '"category":"food","calories":230.0,"macros":{"carbs":26.0,"protein":5.0,"fat":12.0},'
                    '"tags":["pastry"],"alcohol_units":0.0}'
                )
            )

    class _FakeOpenAI:
        def __init__(self, api_key: str):
            assert api_key == "test-key"
            self.responses = _FakeResponses()

    monkeypatch.setattr(
        "src.utils.load_config",
        lambda: AppConfig(
            openai_api_key="test-key",
            openai_model="test-model",
            database_path=tmp_path / "test.db",
        ),
    )
    monkeypatch.setattr("src.utils.OpenAI", _FakeOpenAI)

    result = analyze_nutrition_image(
        "meal.jpg",
        metadata={
            "user_prompt": "3 of those with chocolate filling",
            "source": "telegram",
        },
    )

    assert result.item_count == 3
    assert result.calories == 690.0
    assert result.ingredients[0].amount == "1 piece"
    assert result.ingredients[0].calories == 230.0
    assert result.macros.carbs == 78.0
    assert result.macros.protein == 15.0
    assert result.macros.fat == 36.0

    user_text = captured["input"][1]["content"][0]["text"]
    assert "3 of those" not in user_text
    assert "User note: with chocolate filling" in user_text
    assert 'Metadata: {"source": "telegram"}' in user_text


def test_correct_nutrition_analysis_preserves_previous_item_count_for_single_item_feedback(monkeypatch):
    monkeypatch.setattr(
        "src.utils._call_text_with_schema",
        lambda prompt, user_text, response_model, response_name: NutritionCorrectionResult(
            apply_correction=True,
            analysis=NutritionAnalysis.model_validate(
                {
                    "ingredients": [{"name": "pizza", "amount": "1 mini pizza", "calories": 500.0}],
                    "category": "food",
                    "calories": 500.0,
                    "item_count": 1,
                    "macros": {"carbs": 60.0, "protein": 20.0, "fat": 18.0},
                    "tags": ["pizza"],
                    "alcohol_units": 0.0,
                }
            ),
        ),
    )

    previous = NutritionAnalysis.model_validate(
        {
            "ingredients": [{"name": "pizza", "amount": "1 pizza", "calories": 1000.0}],
            "category": "food",
            "calories": 2000.0,
            "item_count": 2,
            "macros": {"carbs": 240.0, "protein": 80.0, "fat": 72.0},
            "tags": ["pizza"],
            "alcohol_units": 0.0,
        }
    )

    result = correct_nutrition_analysis("hey no its mini pizza", previous)

    assert result.apply_correction is True
    assert result.analysis is not None
    assert result.analysis.item_count == 2
    assert result.analysis.ingredients[0].amount == "1 mini pizza"
    assert result.analysis.ingredients[0].calories == 500.0
    assert result.analysis.calories == 1000.0
    assert result.analysis.macros.carbs == 120.0
    assert result.analysis.macros.protein == 40.0
    assert result.analysis.macros.fat == 36.0


def test_correct_nutrition_analysis_respects_explicit_single_item_change(monkeypatch):
    monkeypatch.setattr(
        "src.utils._call_text_with_schema",
        lambda prompt, user_text, response_model, response_name: NutritionCorrectionResult(
            apply_correction=True,
            analysis=NutritionAnalysis.model_validate(
                {
                    "ingredients": [{"name": "pizza", "amount": "1 mini pizza", "calories": 500.0}],
                    "category": "food",
                    "calories": 1000.0,
                    "item_count": 2,
                    "macros": {"carbs": 120.0, "protein": 40.0, "fat": 36.0},
                    "tags": ["pizza"],
                    "alcohol_units": 0.0,
                }
            ),
        ),
    )

    previous = NutritionAnalysis.model_validate(
        {
            "ingredients": [{"name": "pizza", "amount": "1 pizza", "calories": 1000.0}],
            "category": "food",
            "calories": 2000.0,
            "item_count": 2,
            "macros": {"carbs": 240.0, "protein": 80.0, "fat": 72.0},
            "tags": ["pizza"],
            "alcohol_units": 0.0,
        }
    )

    result = correct_nutrition_analysis("actually just one mini pizza", previous)

    assert result.apply_correction is True
    assert result.analysis is not None
    assert result.analysis.item_count == 1
    assert result.analysis.ingredients[0].amount == "1 mini pizza"
    assert result.analysis.ingredients[0].calories == 500.0
    assert result.analysis.calories == 500.0
    assert result.analysis.macros.carbs == 60.0
    assert result.analysis.macros.protein == 20.0
    assert result.analysis.macros.fat == 18.0
