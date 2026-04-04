from src.models import VocabularyDescriptionRefinement, VocabularyWorkflowResult
from src.query_utils import build_vocabulary_response


def test_build_vocabulary_response_refines_cognate_style_description(monkeypatch):
    calls: list[str] = []

    def _fake_call_text_with_schema(prompt, user_text, response_model, response_name):
        calls.append(response_name)
        if response_name == "vocabulary_response":
            return VocabularyWorkflowResult(
                workflow_type="vocabulary",
                assistant_reply="Modele means model. It can refer to a person posing or a standard example.",
                store_vocabulary=True,
                french_word="modèle",
                english_description="model; person posing or standard example.",
            )
        if response_name == "vocabulary_description_refinement":
            return VocabularyDescriptionRefinement(
                assistant_reply=(
                    "Modele means a person who poses for art or fashion, or an example others follow."
                ),
                english_description=(
                    "person who poses for art or fashion; example or pattern others follow."
                ),
            )
        raise AssertionError(f"Unexpected response_name: {response_name}")

    monkeypatch.setattr("src.query_utils._call_text_with_schema", _fake_call_text_with_schema)

    result = build_vocabulary_response("modèle", metadata={"recent_history": []})

    assert calls == ["vocabulary_response", "vocabulary_description_refinement"]
    assert result.english_description == (
        "person who poses for art or fashion; example or pattern others follow."
    )
    assert result.assistant_reply == (
        "Modele means a person who poses for art or fashion, or an example others follow."
    )


def test_build_vocabulary_response_keeps_simple_non_cognate_description(monkeypatch):
    calls: list[str] = []

    def _fake_call_text_with_schema(prompt, user_text, response_model, response_name):
        calls.append(response_name)
        return VocabularyWorkflowResult(
            workflow_type="vocabulary",
            assistant_reply="Bonjour means hello. It is a common greeting in French.",
            store_vocabulary=True,
            french_word="bonjour",
            english_description="hello; a common greeting in French.",
        )

    monkeypatch.setattr("src.query_utils._call_text_with_schema", _fake_call_text_with_schema)

    result = build_vocabulary_response("bonjour", metadata={"recent_history": []})

    assert calls == ["vocabulary_response"]
    assert result.english_description == "hello; a common greeting in French."
