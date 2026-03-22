"""Text-query planning helpers for PictoAgent."""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from openai import OpenAI

from .config import load_config
from .models import EXPENSE_CATEGORIES, SQLQueryPlan, TextRoutingDecision

logger = logging.getLogger(__name__)


def route_text_workflow(text: str, metadata: dict[str, Any] | None = None) -> TextRoutingDecision:
    """Route a text message to echo, expense query, or nutrition query."""
    today = date.today().isoformat()
    prompt = (
        "You are an orchestrator for a personal tracking assistant. "
        "Choose workflow_type='expense_query' when the user asks about expenses, receipts, spending, money, "
        "monthly totals, categories, groceries, or similar historical spending questions. "
        "Choose workflow_type='nutrition_query' when the user asks about calories, meals, drinks, alcohol, or "
        "historical nutrition tracking data. "
        "Choose workflow_type='echo' for casual conversation or anything that is not a database lookup request. "
        "Return only the structured result."
    )
    user_text = _build_query_user_text(text, metadata, today)
    return _call_text_with_schema(prompt, user_text, TextRoutingDecision, "text_routing_decision")


def build_expense_query_plan(text: str, metadata: dict[str, Any] | None = None) -> SQLQueryPlan:
    """Build a read-only SQL plan for expense questions."""
    today = date.today().isoformat()
    categories = ", ".join(EXPENSE_CATEGORIES)
    prompt = (
        "You generate safe PostgreSQL read-only queries for a personal expense warehouse. "
        f"Today's date is {today}. "
        "The only table you may query is fact_expenses(expense_id, user_id, created_at, description, "
        "expense_total_amount_in_euros, category). "
        f"The valid categories are: {categories}. "
        "Requirements: "
        "1. Output exactly one SELECT statement and nothing destructive. "
        "2. Always filter to the current user with `user_id = $1`. "
        "3. Query only fact_expenses. "
        "3a. Do not use CTEs, comments, DDL, DML, or multiple statements. "
        "4. Build efficient queries. For breakdown questions, return a compact grouped result and avoid huge result sets. "
        "5. Prefer the aliases result_value, result_unit, result_label, period_label. "
        "6. Use COALESCE for numeric aggregates when aggregating. "
        "7. Use explicit date ranges when the user names a month. "
        "8. Map groceries or lebensmittel requests to the category 'Lebensmitteleinkäufe'. "
        "9. explanation must be a short sentence like: I am looking for all expenses in the category "
        "\"Lebensmitteleinkäufe\" for January 2026. "
        "10. response_template must be a short sentence using only {result_value}, {result_unit}, "
        "{result_label}, and {period_label}. "
        "11. For grouped breakdowns, keep the result compact, for example with GROUP BY and LIMIT when appropriate. "
        "Return only the structured result."
    )
    user_text = _build_query_user_text(text, metadata, today)
    return _call_text_with_schema(prompt, user_text, SQLQueryPlan, "expense_query_plan")


def build_nutrition_query_plan(text: str, metadata: dict[str, Any] | None = None) -> SQLQueryPlan:
    """Build a read-only SQL plan for nutrition questions."""
    today = date.today().isoformat()
    prompt = (
        "You generate safe PostgreSQL read-only queries for a personal nutrition warehouse. "
        f"Today's date is {today}. "
        "The only table you may query is fact_consumption(meal_id, user_id, created_at, category, calories, tags, alcohol_units). "
        "Requirements: "
        "1. Output exactly one SELECT statement and nothing destructive. "
        "2. Always filter to the current user with `user_id = $1`. "
        "3. Query only fact_consumption. "
        "3a. Do not use CTEs, comments, DDL, DML, or multiple statements. "
        "4. Build efficient queries. For breakdown questions, return a compact grouped result and avoid huge result sets. "
        "5. Prefer the aliases result_value, result_unit, result_label, period_label. "
        "6. Use COALESCE for numeric aggregates when aggregating. "
        "7. Use explicit date ranges when the user names a month. "
        "8. explanation must be a short sentence like: I am looking for all tracked calories in March 2026. "
        "9. response_template must be a short sentence using only {result_value}, {result_unit}, "
        "{result_label}, and {period_label}. "
        "10. For grouped breakdowns, keep the result compact, for example with GROUP BY and LIMIT when appropriate. "
        "Return only the structured result."
    )
    user_text = _build_query_user_text(text, metadata, today)
    return _call_text_with_schema(prompt, user_text, SQLQueryPlan, "nutrition_query_plan")


def _build_query_user_text(text: str, metadata: dict[str, Any] | None, today: str) -> str:
    metadata = metadata or {}
    return (
        f"Today's date: {today}\n"
        f"User message: {text}\n"
        f"Metadata: {json.dumps(metadata)}"
    )


def _call_text_with_schema(prompt: str, user_text: str, response_model: type, response_name: str):
    config = load_config()
    if not config.openai_api_key:
        raise ValueError(
            "Missing OpenAI API key. Set OPENAI_API_KEY in the environment or in a local .env file."
        )

    client = OpenAI(api_key=config.openai_api_key)
    logger.info(
        "Sending text LLM request",
        extra={
            "event": "llm_text_request",
            "response_name": response_name,
            "system_prompt": prompt,
            "user_prompt_text": user_text,
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
                "content": [{"type": "input_text", "text": user_text}],
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
        "Received text LLM response",
        extra={
            "event": "llm_text_response",
            "response_name": response_name,
            "model": config.openai_model,
            "response_text": response.output_text,
        },
    )
    return response_model.model_validate_json(response.output_text)
