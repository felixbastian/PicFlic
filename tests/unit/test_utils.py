from types import SimpleNamespace

from src.config import AppConfig
from src.models import NutritionAnalysis
from src.utils import analyze_nutrition_image


def test_nutrition_schema_lists_ingredients_first():
    schema = NutritionAnalysis.model_json_schema()

    assert list(schema["properties"])[:2] == ["ingredients", "category"]


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
