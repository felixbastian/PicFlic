"""Utility helpers for PictoAgent."""

from __future__ import annotations

import base64
import json
from typing import Dict, Any
from pathlib import Path

from openai import OpenAI

from .config import load_config
from .models import ImageAnalysis


def analyze_image(image_path: str, metadata: Dict[str, Any] | None = None) -> ImageAnalysis:
    """Analyze an image with OpenAI and return a validated nutrition record."""

    metadata = metadata or {}
    config = load_config()
    if not config.openai_api_key:
        raise ValueError(
            "Missing OpenAI API key. Set OPENAI_API_KEY in the environment or in a local .env file."
        )

    client = OpenAI(api_key=config.openai_api_key)

    prompt = (
        "You are a nutrition tracking assistant. "
        "Estimate the pictured item's category, calories, macros, tags, and alcohol units. "
        "Return only the structured result. "
        "If the image is unclear, make the best conservative estimate and use category='unknown' when needed."
    )

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
                "name": "image_analysis",
                "schema": ImageAnalysis.model_json_schema(),
                "strict": True,
            }
        },
    )

    return ImageAnalysis.model_validate_json(response.output_text)


def _guess_mime_type(image_path: Path) -> str:
    suffix = image_path.suffix.lower()
    if suffix == ".png":
        return "image/png"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    return "application/octet-stream"
