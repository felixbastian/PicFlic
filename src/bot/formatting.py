"""Formatting helpers for Telegram bot replies."""

from __future__ import annotations

from decimal import Decimal
from html import escape
from string import Formatter
from typing import Any, Mapping

from .constants import QUERY_TEMPLATE_FIELDS


def format_result_response(result: dict, persistence_note: str | None = None) -> str:
    """Format the specialist result for Telegram."""
    task_type = result["task_type"]
    analysis = result["analysis"]
    if task_type == "expense":
        lines: list[str] = []
        if persistence_note:
            lines.append(persistence_note)
        lines.extend(
            [
                f"Total: EUR {float(analysis['expense_total_amount_in_euros']):.2f}",
                f"Category: {analysis['category']}",
                f"Description: {analysis['description']}",
            ]
        )
        return "\n".join(lines)

    if task_type == "recipe":
        return format_recipe_response(analysis, persistence_note or "Recipe added to your collection.")

    lines: list[str] = []
    ingredients = analysis.get("ingredients", [])
    if ingredients:
        lines.append("<b>Ingredients</b>")
        for ingredient in ingredients:
            lines.append(
                f"- {_format_ingredient_name(ingredient['name'])} : "
                f"{_format_ingredient_amount(ingredient['amount'])} ({ingredient['calories']} kcal)"
            )

    if lines:
        lines.append("")

    item_count = int(analysis.get("item_count") or 1)
    total_calories = float(analysis["calories"])
    if item_count > 1:
        lines.append(f"<b>Amount:</b> {item_count}")
        single_item_calories = total_calories / item_count
        lines.append(f"<b>Calories:</b> {item_count} * {single_item_calories} = {total_calories}")
    else:
        lines.append(f"<b>Calories:</b> {total_calories}")
    lines.append(f"<b>Tags:</b> {escape(', '.join(str(tag) for tag in analysis.get('tags', [])))}")
    if persistence_note:
        update_note, total_note = _split_nutrition_persistence_note(persistence_note)
        if update_note:
            lines.append("")
            lines.append(update_note)
        if total_note:
            lines.append("")
            lines.append(total_note)
        elif not update_note:
            lines.append("")
            lines.append(persistence_note)
    return "\n".join(lines)


def format_recipe_response(result: Mapping[str, Any], persistence_note: str | None = None) -> str:
    """Format a recipe collection result for Telegram."""
    lines: list[str] = []
    if persistence_note:
        lines.append(persistence_note)
    lines.append(f"Name: {result['name']}")
    lines.append(f"Description: {result['description']}")
    if result.get("carb_source"):
        lines.append(f"Carb source: {result['carb_source']}")
    if result.get("vegetarian") is not None:
        lines.append(f"Vegetarian: {'yes' if result['vegetarian'] else 'no'}")
    if result.get("meat"):
        lines.append(f"Meat: {result['meat']}")
    if result.get("frequency_rotation"):
        lines.append(f"Frequency: {result['frequency_rotation']}")
    return "\n".join(lines)


def format_vocabulary_response(assistant_reply: str, persistence_note: str | None = None) -> str:
    """Format the vocabulary reply for Telegram."""
    if not persistence_note:
        return assistant_reply
    return f"{assistant_reply}\n\n{persistence_note}"


def format_query_response(result: Mapping[str, Any], row: Mapping[str, Any]) -> str:
    """Render a planned SQL query result into a short Telegram message."""
    template = result.get("response_template") or (
        "The total is {result_value} {result_unit} for {result_label} in {period_label}."
    )
    referenced_fields = {
        field_name
        for _, field_name, _, _ in Formatter().parse(template)
        if field_name is not None and field_name != ""
    }
    if not referenced_fields.issubset(QUERY_TEMPLATE_FIELDS):
        template = "The total is {result_value} {result_unit} for {result_label} in {period_label}."

    payload = {
        "result_value": _format_query_value(row.get("result_value")),
        "result_unit": str(row.get("result_unit") or "").strip(),
        "result_label": str(row.get("result_label") or "the requested data").strip(),
        "period_label": str(row.get("period_label") or "the requested period").strip(),
    }
    rendered = template.format(**payload)
    return " ".join(rendered.split())


def format_multirow_query_response(
    result: Mapping[str, Any],
    rows: list[Mapping[str, Any]],
    max_lines: int = 10,
) -> str:
    """Render a compact multi-row query response for grouped breakdowns."""
    if not rows:
        return "No results found for your query."

    period_label = str(rows[0].get("period_label") or "the requested period").strip()
    visible_rows = rows[:max_lines]
    lines = [f"Breakdown for {period_label}:"]
    for row in visible_rows:
        result_label = str(row.get("result_label") or "unknown").strip()
        result_value = _format_query_value(row.get("result_value"))
        result_unit = str(row.get("result_unit") or "").strip()
        if result_unit:
            lines.append(f"{result_label}: {result_value} {result_unit}")
        else:
            lines.append(f"{result_label}: {result_value}")

    if len(rows) > max_lines:
        lines.append(f"... and {len(rows) - max_lines} more rows.")

    return "\n".join(lines)


def _format_query_value(value: Any) -> str:
    if value is None:
        return "0"
    if isinstance(value, Decimal):
        return str(int(value)) if value == value.to_integral_value() else f"{value:.2f}"
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else f"{value:.2f}"
    return str(value)


def _split_nutrition_persistence_note(persistence_note: str) -> tuple[str | None, str | None]:
    normalized = persistence_note.strip()
    if not normalized:
        return None, None

    marker = "Today's total calories:"
    if marker in normalized:
        prefix, _, total_value = normalized.partition(marker)
        update_note = prefix.strip().rstrip(".")
        total_note = f"<b>Today total calories:</b> {escape(total_value.strip())}"
        return update_note or None, total_note

    return normalized, None


def _format_ingredient_name(name: Any) -> str:
    normalized = " ".join(str(name).strip().split())
    if not normalized:
        return ""
    parts = normalized.split()
    shortened = " ".join(parts[:2])
    pretty = shortened[:1].upper() + shortened[1:]
    return escape(pretty)


def _format_ingredient_amount(amount: Any) -> str:
    normalized = " ".join(str(amount).strip().split())
    lowered = normalized.lower()
    for prefix in ("about ", "approximately ", "approx. ", "approx "):
        if lowered.startswith(prefix):
            normalized = f"~{normalized[len(prefix):].strip()}"
            break
    return escape(normalized)
