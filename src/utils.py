"""Utility helpers for PictoAgent."""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
import re
from typing import Any, Dict, TypeVar

from openai import OpenAI

from .config import load_config
from .models import (
    EXPENSE_CATEGORIES,
    ExpenseAnalysis,
    NutritionAnalysis,
    NutritionCorrectionResult,
    RecipeAnalysis,
    RoutingDecision,
)
from .openai_schema import build_strict_openai_schema
from .query_utils import _call_text_with_schema

SchemaModel = TypeVar(
    "SchemaModel",
    NutritionAnalysis,
    NutritionCorrectionResult,
    ExpenseAnalysis,
    RecipeAnalysis,
    RoutingDecision,
)
logger = logging.getLogger(__name__)
_IMAGE_TEXT_METADATA_KEYS = ("user_prompt", "comment", "caption")
_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}
_ITEM_COUNT_PATTERNS = (
    re.compile(r"(?<!\w)(?P<count>\d+)\s*(?:x|×)\b", re.IGNORECASE),
    re.compile(r"(?<!\w)(?:x|×)\s*(?P<count>\d+)\b", re.IGNORECASE),
    re.compile(
        r"\b(?P<count>\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+of\s+"
        r"(?:those|these|them|that|this|it)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?P<count>\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+times\b",
        re.IGNORECASE,
    ),
)
_CORRECTION_ITEM_COUNT_PATTERNS = _ITEM_COUNT_PATTERNS + (
    re.compile(
        r"\b(?:just|only)\s+(?P<count>\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b",
        re.IGNORECASE,
    ),
)


def route_image_task(image_path: str, metadata: Dict[str, Any] | None = None) -> RoutingDecision:
    """Decide whether an image should be handled as nutrition, expense, or recipe tracking."""
    prompt = (
        "You are an orchestrator for a personal tracking assistant. "
        "Choose task_type='expense' when the image is a receipt, bill, invoice, or proof of purchase. "
        "Choose task_type='recipe' when the image is a screenshot, recipe card, dish description, meal plan, "
        "or a dish idea that the user wants to save to their recipe collection. "
        "Metadata may explicitly say things like 'add this to the recipes' or 'add this to the collection'; "
        "that should strongly favor task_type='recipe'. "
        "Choose task_type='nutrition' when the image is food, a drink, a meal, or something to estimate calories for. "
        "Return only the structured result."
    )
    return _analyze_with_schema(image_path, metadata, prompt, RoutingDecision, "routing_decision")


def analyze_nutrition_image(image_path: str, metadata: Dict[str, Any] | None = None) -> NutritionAnalysis:
    """Analyze an image with OpenAI and return a validated nutrition record."""
    sanitized_metadata, item_count = _prepare_nutrition_metadata(metadata)
    prompt = (
        "You are a nutrition tracking assistant. "
        "First fill the ingredients field with the pictured food or drink broken into likely ingredients or components. "
        "Each ingredient name must be short and use at most 2 words. "
        "For each ingredient, estimate the amount using both the image and any user note in the metadata. "
        "Keep each amount short and compact, for example 6 pieces, 120 g, 250 ml, or ~25 g. "
        "Prefer counts when they are visually clear. Use ~ instead of words like about or approximately. "
        "For each ingredient, estimate that ingredient's calories for the stated amount. "
        "Then write the model-estimated summed total calories into the top-level calories field. "
        "Always set item_count=1 unless the application explicitly provides a different multiplier. "
        "Estimate the pictured item's category, macros, tags, and alcohol units based on the same ingredient-level estimate. "
        "Use the user note to disambiguate unclear ingredients, portion sizes, toppings, or hidden components. "
        "Return only the structured result. "
        "If the image is unclear, make the best conservative estimate and use category='unknown' when needed. "
        "Tags should describe the overall meal or drink."
    )
    analysis = _analyze_with_schema(
        image_path,
        sanitized_metadata,
        prompt,
        NutritionAnalysis,
        "nutrition_analysis",
    )
    return _apply_item_count_to_nutrition_analysis(analysis, item_count)


def analyze_nutrition_text(text: str, metadata: Dict[str, Any] | None = None) -> NutritionAnalysis:
    """Analyze a text-only food or drink entry and return a validated nutrition record."""
    prompt = (
        "You are a nutrition tracking assistant. "
        "The user is providing a text description of food or drink to track, not a photo. "
        "First fill the ingredients field with the food or drink broken into likely ingredients or components. "
        "Each ingredient name must be short and use at most 2 words. "
        "For each ingredient, estimate the amount from the user's text. "
        "Keep each amount short and compact, for example 6 pieces, 120 g, 250 ml, or ~25 g. "
        "Prefer counts when they are explicit. Use ~ instead of words like about or approximately. "
        "For each ingredient, estimate that ingredient's calories for the stated amount. "
        "Keep the ingredients list and each ingredient's calories scoped to one item when the user clearly refers "
        "to multiple identical items, and use item_count to represent how many copies were consumed. "
        "Keep the top-level calories, macros, and alcohol_units scoped to one item as well when item_count > 1. "
        "If the user describes a mixed meal with different components, keep item_count=1 and represent the full meal "
        "through the ingredients list instead of forcing a multiplier. "
        "Estimate the entry's category, macros, tags, and alcohol units based on the same ingredient-level estimate. "
        "Return only the structured result. "
        "If the text is vague, make the best conservative estimate and use category='unknown' when needed. "
        "Tags should describe the overall meal or drink."
    )
    user_text = _build_text_nutrition_user_text(text, metadata or {})
    analysis = _call_text_with_schema(prompt, user_text, NutritionAnalysis, "nutrition_text_analysis")
    return _normalize_text_nutrition_analysis(analysis)


def revise_nutrition_analysis(
    correction_text: str,
    previous_analysis: NutritionAnalysis | Dict[str, Any],
) -> NutritionAnalysis:
    """Revise the previous nutrition entry after routing has already chosen correction mode."""
    previous = previous_analysis
    if isinstance(previous_analysis, dict):
        previous = NutritionAnalysis.model_validate(previous_analysis)

    prompt = (
        "You are a nutrition tracking assistant revising the user's most recent tracked food or drink entry. "
        "The main router has already determined that the new message is a correction for that same tracked item. "
        "Use the last user message to produce a fully revised nutrition analysis for the previous entry. "
        "The revised analysis must include ingredients first, with each ingredient name limited to at most 2 words. "
        "Each amount should stay compact, for example 6 pieces, 120 g, 250 ml, or ~25 g, and should use ~ instead "
        "of words like about or approximately. "
        "Keep the ingredients list and each ingredient's calories scoped to one item. "
        "Keep the top-level calories, macros, and alcohol_units scoped to one item as well. "
        "Use item_count to reflect how many copies of the same tracked item the entry represents. "
        "Preserve the previous item_count when the correction only changes what one item was like, and revise "
        "item_count only when the user's message explicitly changes the number of items. "
        "Use item_count=1 when there is only one item. "
        "Return only the fully revised structured result."
    )
    user_text = (
        f"User correction message: {correction_text}\n"
        f"Previous nutrition analysis: {json.dumps(previous.to_dict(), ensure_ascii=False)}"
    )
    revised = _call_text_with_schema(prompt, user_text, NutritionAnalysis, "nutrition_revision")
    return _normalize_corrected_nutrition_analysis(correction_text, previous, revised)


def revise_expense_analysis(
    correction_text: str,
    previous_analysis: ExpenseAnalysis | Dict[str, Any],
) -> ExpenseAnalysis:
    """Revise the previous expense entry after routing has already chosen correction mode."""
    previous = previous_analysis
    if isinstance(previous_analysis, dict):
        previous = ExpenseAnalysis.model_validate(previous_analysis)

    categories = ", ".join(EXPENSE_CATEGORIES)
    prompt = (
        "You are an expense tracking assistant revising the user's most recent tracked expense entry. "
        "The main router has already determined that the new message is a correction for that same tracked expense. "
        "Use the user's latest message plus the previous expense analysis to produce one fully revised expense "
        "analysis. "
        f"The category must be exactly one value from this list: {categories}. "
        "If the user names a category loosely or in another language, map it to the closest valid category from the "
        "allowed list. Use 'Sonstige' only when nothing else fits. "
        "Preserve the previous amount, description, and category unless the user's correction clearly changes them. "
        "Return only the fully revised structured result."
    )
    user_text = (
        f"User correction message: {correction_text}\n"
        f"Previous expense analysis: {json.dumps(previous.to_dict(), ensure_ascii=False)}"
    )
    return _call_text_with_schema(prompt, user_text, ExpenseAnalysis, "expense_revision")


def analyze_expense_receipt(image_path: str, metadata: Dict[str, Any] | None = None) -> ExpenseAnalysis:
    """Analyze a receipt image and extract the expense details."""
    categories = ", ".join(EXPENSE_CATEGORIES)
    prompt = (
        "You are an expense tracking assistant. "
        "The user is sending a receipt or proof of purchase. "
        "Extract the final total amount in euros, write a short description, "
        f"and choose exactly one category from this list: {categories}. "
        "Use 'Sonstige' if nothing else fits. Return only the structured result."
    )
    return _analyze_with_schema(image_path, metadata, prompt, ExpenseAnalysis, "expense_analysis")


def correct_nutrition_analysis(
    correction_text: str,
    previous_analysis: NutritionAnalysis | Dict[str, Any],
    metadata: Dict[str, Any] | None = None,
) -> NutritionCorrectionResult:
    """Decide whether a text message corrects the last nutrition analysis and revise it if needed."""
    previous = previous_analysis
    if isinstance(previous_analysis, dict):
        previous = NutritionAnalysis.model_validate(previous_analysis)

    prompt = (
        "You are a nutrition tracking assistant handling a follow-up message about the user's most recent food or "
        "drink entry. "
        "The previous nutrition analysis may have come from a photo or from a text-only log. "
        "Decide whether the new message is meant to correct or clarify the previous nutrition analysis. "
        "Only treat the message as a correction when it is clearly still about that same previously tracked dish or "
        "drink. "
        "Treat messages that change ingredients, amounts, portion sizes, preparation method, toppings, or drink size "
        "as corrections. "
        "If the message introduces a different dish, a different drink, a new meal, a second item, dessert, or a "
        "new standalone food log, do not treat it as a correction. Those should be handled as new nutrition entries "
        "instead. "
        "When the food or drink being described is different from the previous entry, return apply_correction=false "
        "and analysis=null even if the message contains quantities, ingredients, or calories. "
        "Examples of corrections: 'actually it was a small beer', 'add cheese to that pizza', 'it was 2 croissants "
        "not 3'. "
        "Examples that are not corrections: 'I also had pasta', 'for dessert I had ice cream', '2 croissants', "
        "'beer 500 ml' when the previous entry was a pizza. "
        "Do not treat casual replies, unrelated questions, or new standalone requests as corrections. "
        "If the message is a correction, return apply_correction=true and provide a fully revised nutrition analysis. "
        "The revised analysis must include ingredients first, with each ingredient name limited to at most 2 words. "
        "Each amount should stay compact, for example 6 pieces, 120 g, 250 ml, or ~25 g, and should use ~ instead "
        "of words like about or approximately. "
        "Keep the ingredients list and each ingredient's calories scoped to one item. "
        "Keep the top-level calories, macros, and alcohol_units scoped to one item as well. "
        "Use item_count to reflect how many copies of the same tracked item the entry represents. "
        "Preserve the previous item_count when the correction only changes what one item was like, and revise "
        "item_count only when the user's message explicitly changes the number of items. "
        "Use item_count=1 when there is only one item. "
        "If the message is not a correction, return apply_correction=false and analysis=null. "
        "Return only the structured result."
    )
    user_text = (
        f"User correction message: {correction_text}\n"
        f"Previous nutrition analysis: {json.dumps(previous.to_dict(), ensure_ascii=False)}\n"
        f"Metadata: {json.dumps(metadata or {}, ensure_ascii=False)}"
    )
    result = _call_text_with_schema(prompt, user_text, NutritionCorrectionResult, "nutrition_correction")
    if not result.apply_correction or result.analysis is None:
        return result

    normalized = _normalize_corrected_nutrition_analysis(correction_text, previous, result.analysis)
    return result.model_copy(update={"analysis": normalized})


def analyze_recipe_image(image_path: str, metadata: Dict[str, Any] | None = None) -> RecipeAnalysis:
    """Analyze a recipe screenshot or dish card and extract a structured recipe entry."""
    prompt = (
        "You are a recipe collection assistant. "
        "The user is sending a recipe screenshot, meal plan, dish card, or dish description to save. "
        "Extract a short dish name, a concise description, carb_source, vegetarian, meat, and frequency_rotation. "
        "Use null for unknown optional fields. "
        "If vegetarian is true, meat must be null. "
        "Return only the structured result."
    )
    return _analyze_with_schema(image_path, metadata, prompt, RecipeAnalysis, "recipe_analysis")


def analyze_image(image_path: str, metadata: Dict[str, Any] | None = None) -> NutritionAnalysis:
    """Backward-compatible alias for nutrition analysis."""
    return analyze_nutrition_image(image_path, metadata)


def _analyze_with_schema(
    image_path: str,
    metadata: Dict[str, Any] | None,
    prompt: str,
    response_model: type[SchemaModel],
    response_name: str,
) -> SchemaModel:
    """Run an image+text prompt against OpenAI with a strict JSON schema."""

    metadata = metadata or {}
    config = load_config()
    if not config.openai_api_key:
        raise ValueError(
            "Missing OpenAI API key. Set OPENAI_API_KEY in the environment or in a local .env file."
        )

    client = OpenAI(api_key=config.openai_api_key)

    user_text = _build_image_user_text(image_path, metadata)

    content: list[dict[str, Any]] = [
        {"type": "input_text", "text": user_text},
    ]

    image_file = Path(image_path)
    if image_file.is_file():
        mime_type = _guess_mime_type(image_file)
        image_bytes = image_file.read_bytes()
        image_data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
        content.append({"type": "input_image", "image_url": image_data_url})

    logger.info(
        "Sending image LLM request",
        extra={
            "event": "llm_image_request",
            "response_name": response_name,
            "system_prompt": prompt,
            "user_prompt_text": user_text,
            "metadata": metadata,
            "image_path": image_path,
            "has_image": image_file.is_file(),
        },
    )

    response = client.responses.create(
        model=config.openai_model,
        input=[
            {
                "role": "system",
                "content": [{"type": "input_text", "text": prompt}],
            },
            {
                "role": "user",
                "content": content,
            },
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": response_name,
                "schema": build_strict_openai_schema(response_model),
                "strict": True,
            }
        },
    )
    logger.info(
        "Received image LLM response",
        extra={
            "event": "llm_image_response",
            "response_name": response_name,
            "model": config.openai_model,
            "response_text": response.output_text,
        },
    )

    return response_model.model_validate_json(response.output_text)


def _guess_mime_type(image_path: Path) -> str:
    suffix = image_path.suffix.lower()
    if suffix == ".png":
        return "image/png"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    return "application/octet-stream"


def _build_image_user_text(image_path: str, metadata: Dict[str, Any]) -> str:
    sanitized_metadata = dict(metadata)
    user_note = _extract_image_user_note(sanitized_metadata)

    lines = [
        f"Image path: {image_path}",
        f"Filename: {Path(image_path).name}",
    ]
    if user_note:
        lines.append(f"User note: {user_note}")
    lines.append(f"Metadata: {json.dumps(sanitized_metadata, ensure_ascii=False)}")
    return "\n".join(lines)


def _build_text_nutrition_user_text(text: str, metadata: Dict[str, Any]) -> str:
    return "\n".join(
        [
            f"User message: {text.strip()}",
            f"Metadata: {json.dumps(metadata, ensure_ascii=False)}",
        ]
    )


def _extract_image_user_note(metadata: Dict[str, Any]) -> str | None:
    for key in _IMAGE_TEXT_METADATA_KEYS:
        value = metadata.pop(key, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _prepare_nutrition_metadata(metadata: Dict[str, Any] | None) -> tuple[Dict[str, Any], int]:
    normalized = dict(metadata or {})
    item_count = 1

    for key in _IMAGE_TEXT_METADATA_KEYS:
        value = normalized.get(key)
        if not isinstance(value, str) or not value.strip():
            continue

        item_count, sanitized_note = _extract_item_count_from_caption(value)
        if sanitized_note:
            normalized[key] = sanitized_note
        else:
            normalized.pop(key, None)
        break

    return normalized, item_count


def _extract_item_count_from_caption(caption: str) -> tuple[int, str | None]:
    normalized_caption = caption.strip()
    if not normalized_caption:
        return 1, None

    for pattern in _ITEM_COUNT_PATTERNS:
        match = pattern.search(normalized_caption)
        if match is None:
            continue

        raw_count = match.group("count").strip().lower()
        item_count = int(raw_count) if raw_count.isdigit() else _NUMBER_WORDS.get(raw_count, 1)
        if item_count <= 1:
            break

        sanitized_caption = f"{normalized_caption[:match.start()]} {normalized_caption[match.end():]}"
        sanitized_caption = re.sub(r"\s+", " ", sanitized_caption)
        sanitized_caption = re.sub(r"\s+([,.;:!?])", r"\1", sanitized_caption)
        sanitized_caption = re.sub(r"(^|[\s])[,;:/-]+", " ", sanitized_caption)
        sanitized_caption = sanitized_caption.strip(" ,;:/-")
        return item_count, sanitized_caption or None

    return 1, normalized_caption


def _apply_item_count_to_nutrition_analysis(
    analysis: NutritionAnalysis,
    item_count: int,
) -> NutritionAnalysis:
    return _rescale_nutrition_analysis_totals(analysis, from_count=1, to_count=item_count)


def _normalize_text_nutrition_analysis(analysis: NutritionAnalysis) -> NutritionAnalysis:
    effective_count = max(1, int(analysis.item_count))
    source_count = _infer_analysis_total_item_count(analysis, effective_count)
    return _rescale_nutrition_analysis_totals(
        analysis,
        from_count=source_count,
        to_count=effective_count,
    )


def _normalize_corrected_nutrition_analysis(
    correction_text: str,
    previous_analysis: NutritionAnalysis,
    corrected_analysis: NutritionAnalysis,
) -> NutritionAnalysis:
    effective_count = _resolve_corrected_item_count(
        correction_text,
        previous_analysis,
        corrected_analysis,
    )
    source_count = _infer_analysis_total_item_count(corrected_analysis, effective_count)
    return _rescale_nutrition_analysis_totals(
        corrected_analysis,
        from_count=source_count,
        to_count=effective_count,
    )


def _resolve_corrected_item_count(
    correction_text: str,
    previous_analysis: NutritionAnalysis,
    corrected_analysis: NutritionAnalysis,
) -> int:
    explicit_count = _find_explicit_item_count(correction_text)
    if explicit_count is not None:
        return max(1, explicit_count)
    if corrected_analysis.item_count > 1:
        return corrected_analysis.item_count
    if previous_analysis.item_count > 1:
        return previous_analysis.item_count
    return 1


def _find_explicit_item_count(text: str) -> int | None:
    normalized_text = text.strip()
    if not normalized_text:
        return None

    for pattern in _CORRECTION_ITEM_COUNT_PATTERNS:
        match = pattern.search(normalized_text)
        if match is None:
            continue
        return _parse_count_token(match.group("count"))
    return None


def _parse_count_token(raw_count: str) -> int:
    normalized = raw_count.strip().lower()
    if normalized.isdigit():
        return int(normalized)
    return _NUMBER_WORDS.get(normalized, 1)


def _infer_analysis_total_item_count(
    analysis: NutritionAnalysis,
    effective_count: int,
) -> int:
    ingredient_calories = sum(float(ingredient.calories) for ingredient in analysis.ingredients)
    candidates = [1]
    if analysis.item_count > 1:
        candidates.append(int(analysis.item_count))
    if effective_count > 1:
        candidates.append(int(effective_count))

    if ingredient_calories <= 0:
        return max(candidates)

    return min(
        dict.fromkeys(candidates),
        key=lambda candidate: abs(float(analysis.calories) - ingredient_calories * candidate),
    )


def _rescale_nutrition_analysis_totals(
    analysis: NutritionAnalysis,
    from_count: int,
    to_count: int,
) -> NutritionAnalysis:
    normalized_from = max(1, int(from_count))
    normalized_to = max(1, int(to_count))
    scale = normalized_to / normalized_from

    return analysis.model_copy(
        update={
            "calories": analysis.calories * scale,
            "item_count": normalized_to,
            "macros": analysis.macros.model_copy(
                update={
                    "carbs": analysis.macros.carbs * scale,
                    "protein": analysis.macros.protein * scale,
                    "fat": analysis.macros.fat * scale,
                }
            ),
            "alcohol_units": analysis.alcohol_units * scale,
        }
    )
