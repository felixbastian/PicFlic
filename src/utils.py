"""Utility helpers for PictoAgent."""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
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
    prompt = (
        "You are a nutrition tracking assistant. "
        "First fill the ingredients field with the pictured food or drink broken into likely ingredients or components. "
        "Each ingredient name must be short and use at most 2 words. "
        "For each ingredient, estimate the amount using both the image and any user note in the metadata. "
        "Keep each amount short and compact, for example 6 pieces, 120 g, 250 ml, or ~25 g. "
        "Prefer counts when they are visually clear. Use ~ instead of words like about or approximately. "
        "For each ingredient, estimate that ingredient's calories for the stated amount. "
        "Then write the model-estimated summed total calories into the top-level calories field. "
        "Estimate the pictured item's category, macros, tags, and alcohol units based on the same ingredient-level estimate. "
        "Use the user note to disambiguate unclear ingredients, portion sizes, toppings, or hidden components. "
        "Return only the structured result. "
        "If the image is unclear, make the best conservative estimate and use category='unknown' when needed. "
        "Tags should describe the overall meal or drink."
    )
    return _analyze_with_schema(image_path, metadata, prompt, NutritionAnalysis, "nutrition_analysis")


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
        "drink photo analysis. "
        "Decide whether the new message is meant to correct or clarify the previous nutrition analysis. "
        "Treat messages that change ingredients, amounts, portion sizes, preparation method, toppings, or drink size "
        "as corrections. "
        "Do not treat casual replies, unrelated questions, or new standalone requests as corrections. "
        "If the message is a correction, return apply_correction=true and provide a fully revised nutrition analysis. "
        "The revised analysis must include ingredients first, with each ingredient name limited to at most 2 words. "
        "Each amount should stay compact, for example 6 pieces, 120 g, 250 ml, or ~25 g, and should use ~ instead "
        "of words like about or approximately. "
        "The revised analysis must include each ingredient's amount and calories, and the "
        "top-level calories field must be the model-estimated total for the corrected analysis. "
        "If the message is not a correction, return apply_correction=false and analysis=null. "
        "Return only the structured result."
    )
    user_text = (
        f"User correction message: {correction_text}\n"
        f"Previous nutrition analysis: {json.dumps(previous.to_dict(), ensure_ascii=False)}\n"
        f"Metadata: {json.dumps(metadata or {}, ensure_ascii=False)}"
    )
    return _call_text_with_schema(prompt, user_text, NutritionCorrectionResult, "nutrition_correction")


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


def transcribe_audio(audio_path: str) -> str:
    """Transcribe an audio file with OpenAI's Audio API."""
    config = load_config()
    if not config.openai_api_key:
        raise ValueError(
            "Missing OpenAI API key. Set OPENAI_API_KEY in the environment or in a local .env file."
        )

    client = OpenAI(api_key=config.openai_api_key)
    with Path(audio_path).open("rb") as audio_file:
        response = client.audio.transcriptions.create(
            file=audio_file,
            model=config.openai_transcription_model,
        )

    transcription_text = response if isinstance(response, str) else response.text
    logger.info(
        "Received audio transcription",
        extra={
            "event": "llm_audio_transcription",
            "audio_path": audio_path,
            "model": config.openai_transcription_model,
            "transcription_preview": transcription_text[:200],
        },
    )
    return transcription_text.strip()


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
                "schema": response_model.model_json_schema(),
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


def _extract_image_user_note(metadata: Dict[str, Any]) -> str | None:
    for key in _IMAGE_TEXT_METADATA_KEYS:
        value = metadata.pop(key, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
