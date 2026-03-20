"""Utility helpers for PictoAgent."""

from __future__ import annotations

import base64
import json
from typing import Dict, Any, TypeVar
from pathlib import Path

from openai import OpenAI

from .config import load_config
from .models import EXPENSE_CATEGORIES, ExpenseAnalysis, NutritionAnalysis, RoutingDecision

SchemaModel = TypeVar("SchemaModel", NutritionAnalysis, ExpenseAnalysis, RoutingDecision)


def route_image_task(image_path: str, metadata: Dict[str, Any] | None = None) -> RoutingDecision:
    """Decide whether an image should be handled as nutrition or expense tracking."""
    prompt = (
        "You are an orchestrator for a personal tracking assistant. "
        "Choose task_type='expense' when the image is a receipt, bill, invoice, or proof of purchase. "
        "Choose task_type='nutrition' when the image is food, a drink, a meal, or something to estimate calories for. "
        "Return only the structured result."
    )
    return _analyze_with_schema(image_path, metadata, prompt, RoutingDecision, "routing_decision")


def analyze_nutrition_image(image_path: str, metadata: Dict[str, Any] | None = None) -> NutritionAnalysis:
    """Analyze an image with OpenAI and return a validated nutrition record."""
    prompt = (
        "You are a nutrition tracking assistant. "
        "Estimate the pictured item's category, calories, macros, tags, and alcohol units. "
        "Return only the structured result. "
        "If the image is unclear, make the best conservative estimate and use category='unknown' when needed."
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

    user_text = (
        f"Image path: {image_path}\n"
        f"Filename: {Path(image_path).name}\n"
        f"Metadata: {json.dumps(metadata)}"
    )

    content: list[dict[str, Any]] = [
        {"type": "input_text", "text": user_text},
    ]

    image_file = Path(image_path)
    if image_file.is_file():
        mime_type = _guess_mime_type(image_file)
        image_bytes = image_file.read_bytes()
        image_data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
        content.append({"type": "input_image", "image_url": image_data_url})

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
