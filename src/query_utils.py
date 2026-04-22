"""Text planning helpers for PictoAgent."""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from difflib import SequenceMatcher
from typing import Any
import unicodedata

from openai import OpenAI

from .config import load_config
from .models import (
    EXPENSE_CATEGORIES,
    RecipeCollectionResult,
    SQLQueryPlan,
    TextRoutingDecision,
    VocabularyDescriptionRefinement,
    VocabularyWorkflowResult,
)
from .openai_schema import build_strict_openai_schema

logger = logging.getLogger(__name__)
_VOCAB_DESCRIPTION_STOPWORDS = {"a", "an", "the", "to"}


def route_text_workflow(text: str, metadata: dict[str, Any] | None = None) -> TextRoutingDecision:
    """Route a text message to the appropriate tracking or assistant workflow."""
    today = date.today().isoformat()
    prompt = (
        "You are an orchestrator for a personal tracking assistant. "
        "Metadata may include recent_history containing the last 5 chat messages, latest_nutrition_result "
        "containing the most recent tracked nutrition entry, latest_expense_result containing the most recent "
        "tracked expense entry, and latest_tracking_result containing the single most recent tracked entry of any "
        "type. Use that context only to decide which workflow should handle the current user message. "
        "Choose workflow_type='expense_correction' when the user is clearly revising the most recent tracked "
        "expense entry, for example changing its amount, category, merchant, or short description. Only choose this "
        "when latest_expense_result is available in metadata, or when latest_tracking_result is available and refers "
        "to an expense entry, and the current message is clearly about that same previous expense. "
        "Choose workflow_type='expense_tracking' when the user is logging a new expense to track right now from text, "
        "for example messages like '12.50 EUR at Rewe', 'I spent 8 euros on coffee', '25 EUR for the train', or "
        "'30 Euro im Restaurant ausgegeben'. Use this when the message describes a fresh purchase, payment, or "
        "spending event with an amount, merchant, or purpose that should be saved as a new expense entry. "
        "Do not choose workflow_type='expense_correction' for a fresh expense that merely happens to come after an "
        "earlier expense entry. "
        "Choose workflow_type='expense_query' when the user asks about expenses, receipts, spending, money, "
        "monthly totals, categories, groceries, or similar historical spending questions. "
        "Choose workflow_type='nutrition_query' when the user asks about previously tracked calories, meals, "
        "drinks, alcohol, or historical nutrition tracking data. "
        "Choose workflow_type='nutrition_correction' when the user is clearly revising the most recent tracked "
        "nutrition entry, for example changing the amount, ingredients, toppings, preparation, or drink size of "
        "that same dish or drink. Only choose this when latest_nutrition_result is available in metadata and the "
        "current message is clearly about that same previous entry. "
        "Choose workflow_type='delete_latest_entry' when the user clearly means that the most recent tracked entry "
        "should no longer count, for example because it was a mistake, should be undone, should not count, should be "
        "ignored, should be taken back, or should be deleted. Infer this from the meaning of the message, not from "
        "one specific keyword. Only choose this when latest_tracking_result is available in metadata. This workflow "
        "must only ever target one entry: the single most recent tracked entry from metadata. Never use it for "
        "requests to delete multiple entries, clear history, or remove a broad set of data. "
        "Choose workflow_type='nutrition_tracking' when the user is logging or describing a food or drink to "
        "estimate and track right now, for example messages like '2 croissants', 'beer 500 ml', 'I had a chicken "
        "salad', or '1 glass of wine'. Use this for standalone food or drink descriptions even when they mention "
        "amounts. Also choose this when the user mentions a different dish, a second item, dessert, or any new "
        "food or drink entry instead of revising the previous tracked one. Do not use workflow_type='nutrition_query' "
        "for a new food or drink entry that should be logged. "
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
        "english_description as one concise plain-English description that includes the meaning plus a short explanation. "
        "3a. If the most direct English equivalent is identical to the French word or looks almost the same, do not "
        "use that near-identical cognate as the main gloss. Instead use a short plain-English paraphrase. "
        "4. When store_vocabulary=false, set french_word and english_description to null. "
        "5. assistant_reply should always be a concise helpful answer in English. "
        "6. If it is a new vocabulary item, assistant_reply should include the English meaning and a short "
        "description. "
        "6a. When the direct English equivalent is too close to the French word, assistant_reply should also avoid "
        "using that near-identical cognate as the main gloss and should use a simple paraphrase instead. "
        "7. If it is a follow-up, answer the follow-up directly without pretending to store a new word. "
        "8. Keep explanations simple and short. Do not overcomplicate them. "
        "Return only the structured result."
    )
    user_text = _build_text_user_text(text, metadata, today)
    result = _call_text_with_schema(prompt, user_text, VocabularyWorkflowResult, "vocabulary_response")
    if result.store_vocabulary and (not result.french_word or not result.english_description):
        raise ValueError("Vocabulary workflow must return french_word and english_description when storing.")
    if result.store_vocabulary and _is_description_too_close_to_french_word(
        result.french_word,
        result.english_description,
    ):
        refined = _refine_vocabulary_description(
            result.french_word,
            result.english_description,
            result.assistant_reply,
        )
        result = result.model_copy(
            update={
                "assistant_reply": refined.assistant_reply,
                "english_description": refined.english_description,
            }
        )
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


def _normalize_vocab_text(value: str) -> str:
    lowered = value.strip().lower()
    decomposed = unicodedata.normalize("NFKD", lowered)
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    cleaned = re.sub(r"[^a-z0-9\s]", " ", without_accents)
    return re.sub(r"\s+", " ", cleaned).strip()


def _primary_gloss_candidates(english_description: str) -> list[str]:
    cleaned_description = re.split(r"[;:,.()\n]", english_description, maxsplit=1)[0]
    tokens = [
        token
        for token in _normalize_vocab_text(cleaned_description).split()
        if token not in _VOCAB_DESCRIPTION_STOPWORDS
    ]
    if not tokens:
        return []

    candidates = [tokens[0]]
    candidates.append(" ".join(tokens[: min(2, len(tokens))]))
    candidates.append(" ".join(tokens[: min(3, len(tokens))]))
    return [candidate for candidate in candidates if candidate]


def _is_description_too_close_to_french_word(french_word: str, english_description: str) -> bool:
    normalized_word = _normalize_vocab_text(french_word)
    if not normalized_word:
        return False

    for candidate in _primary_gloss_candidates(english_description):
        if candidate == normalized_word:
            return True
        if len(candidate) >= 4 and SequenceMatcher(None, normalized_word, candidate).ratio() >= 0.88:
            return True

    return False


def _refine_vocabulary_description(
    french_word: str,
    english_description: str,
    assistant_reply: str,
) -> VocabularyDescriptionRefinement:
    prompt = (
        "You are refining a French vocabulary card. "
        "The current English gloss is too close to the French word, so it is not helpful for learning. "
        "Rewrite both fields in short plain English. "
        "Do not use the same-looking English cognate as the main gloss when it is identical or almost identical to "
        "the French word. "
        "Use a brief paraphrase instead. "
        "Keep the explanation simple, natural, and not overcomplicated. "
        "assistant_reply should stay concise and helpful. "
        "english_description should remain a compact stored description. "
        "Return only the structured result."
    )
    user_text = (
        f"French word: {french_word}\n"
        f"Current english_description: {english_description}\n"
        f"Current assistant_reply: {assistant_reply}"
    )
    return _call_text_with_schema(
        prompt,
        user_text,
        VocabularyDescriptionRefinement,
        "vocabulary_description_refinement",
    )


def call_text_with_schema(prompt: str, user_text: str, response_model: type, response_name: str):
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


def _call_text_with_schema(prompt: str, user_text: str, response_model: type, response_name: str):
    return call_text_with_schema(prompt, user_text, response_model, response_name)
