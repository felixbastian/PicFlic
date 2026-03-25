"""Text planning helpers for PictoAgent."""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from openai import OpenAI

from .config import load_config
from .models import (
    EXPENSE_CATEGORIES,
    RecipeCollectionResult,
    SQLQueryPlan,
    TextRoutingDecision,
    VocabularyWorkflowResult,
)
from .openai_schema import build_strict_openai_schema

logger = logging.getLogger(__name__)


def route_text_workflow(text: str, metadata: dict[str, Any] | None = None) -> TextRoutingDecision:
    """Route a text message to echo, query, or vocabulary workflows."""
    today = date.today().isoformat()
    prompt = (
        "You are an orchestrator for a personal tracking assistant. "
        "Choose workflow_type='expense_query' when the user asks about expenses, receipts, spending, money, "
        "monthly totals, categories, groceries, or similar historical spending questions. "
        "Choose workflow_type='nutrition_query' when the user asks about calories, meals, drinks, alcohol, or "
        "historical nutrition tracking data. "
        "Choose workflow_type='vocabulary' when the user gives a French word or short phrase and wants its English "
        "meaning, or when the user asks a follow-up question about vocabulary that was discussed in the recent "
        "conversation history. A standalone French word or short French phrase like 'bonjour' should be treated as "
        "a vocabulary request even if the user does not explicitly ask for a translation. "
        "Choose workflow_type='recipe_collection' when the user wants to save a dish, recipe, meal idea, or cooking "
        "instructions into their recipe collection, for example by saying 'add this to the recipes' or "
        "'add this to the collection'. "
        "Choose workflow_type='echo' for casual conversation or anything that is not a database lookup request. "
        "Return only the structured result."
    )
    user_text = _build_text_user_text(text, metadata, today)
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
    user_text = _build_text_user_text(text, metadata, today)
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
    user_text = _build_text_user_text(text, metadata, today)
    return _call_text_with_schema(prompt, user_text, SQLQueryPlan, "nutrition_query_plan")


def build_vocabulary_response(
    text: str,
    metadata: dict[str, Any] | None = None,
) -> VocabularyWorkflowResult:
    """Build a structured vocabulary response, including whether it should be stored."""
    today = date.today().isoformat()
    prompt = (
        "You are a French vocabulary trainer inside a personal assistant app. "
        f"Today's date is {today}. "
        "You will receive the current user message plus recent conversation history. "
        "Decide whether this message introduces a new French vocabulary item that should be stored, or whether it is "
        "just a follow-up question about vocabulary that was already discussed. "
        "Rules: "
        "1. If the current message is a French word or short French phrase to translate or explain, set "
        "store_vocabulary=true. "
        "2. If the user is asking a follow-up question about a word that was already explained in the recent history, "
        "set store_vocabulary=false. "
        "3. When store_vocabulary=true, return french_word as the normalized French word or short phrase, and "
        "english_description as one concise English description that includes the meaning plus a short explanation. "
        "4. When store_vocabulary=false, set french_word and english_description to null. "
        "5. assistant_reply should always be a concise helpful answer in English. "
        "6. If it is a new vocabulary item, assistant_reply should include the English meaning and a short "
        "description. "
        "7. If it is a follow-up, answer the follow-up directly without pretending to store a new word. "
        "Return only the structured result."
    )
    user_text = _build_text_user_text(text, metadata, today)
    result = _call_text_with_schema(prompt, user_text, VocabularyWorkflowResult, "vocabulary_response")
    if result.store_vocabulary and (not result.french_word or not result.english_description):
        raise ValueError("Vocabulary workflow must return french_word and english_description when storing.")
    return result


def build_recipe_collection_response(
    text: str,
    metadata: dict[str, Any] | None = None,
) -> RecipeCollectionResult:
    """Build a structured recipe collection entry from text."""
    today = date.today().isoformat()
    prompt = (
        "You are a recipe collection assistant. "
        f"Today's date is {today}. "
        "The user wants to save a dish, recipe, or meal idea into their collection. "
        "Extract a short dish name, a concise description, carb_source, vegetarian, meat, and frequency_rotation. "
        "Use null for optional fields when not known. "
        "If vegetarian is true, meat must be null. "
        "Return only the structured result."
    )
    user_text = _build_text_user_text(text, metadata, today)
    return _call_text_with_schema(prompt, user_text, RecipeCollectionResult, "recipe_collection_result")


def _build_text_user_text(text: str, metadata: dict[str, Any] | None, today: str) -> str:
    metadata = metadata or {}
    return (
        f"Today's date: {today}\n"
        f"User message: {text}\n"
        f"Metadata: {json.dumps(metadata, ensure_ascii=False)}"
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
                "schema": build_strict_openai_schema(response_model),
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
