#!/usr/bin/env python3
"""Backfill stored example sentences for existing vocabulary rows."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_config
from src.db import PostgresDatabase
from src.vocabulary_review import (
    append_vocabulary_examples_to_description,
    generate_stored_vocabulary_examples,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate and store 3 French example sentences for existing vocabulary rows."
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum number of rows to process. Defaults to no limit.",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Skip this many matching rows after ordering by created_at and vocabulary_id.",
    )
    parser.add_argument("--user-id", help="Restrict the backfill to one user_id.")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Regenerate rows even if example_sentences is already populated.",
    )
    parser.add_argument(
        "--append-to-description",
        action="store_true",
        help="Also append a numbered Examples block into english_description.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview generated examples without updating the database.",
    )
    return parser.parse_args()


async def _load_candidate_rows(
    db: PostgresDatabase,
    *,
    limit: int | None,
    offset: int,
    user_id: str | None,
    overwrite: bool,
) -> list[dict[str, Any]]:
    if not db._pool:
        raise RuntimeError("Database not connected. Call connect() first.")

    conditions = [
        "french_word IS NOT NULL",
        "english_description IS NOT NULL",
    ]
    params: list[Any] = []

    if not overwrite:
        conditions.append("COALESCE(array_length(example_sentences, 1), 0) = 0")
    if user_id:
        params.append(user_id)
        conditions.append(f"user_id::TEXT = ${len(params)}")

    order_and_paging = [
        "ORDER BY created_at ASC, vocabulary_id ASC",
    ]
    if limit is not None:
        params.append(limit)
        order_and_paging.append(f"LIMIT ${len(params)}")
    if offset > 0:
        params.append(offset)
        order_and_paging.append(f"OFFSET ${len(params)}")

    query = f"""
        SELECT
            vocabulary_id::TEXT AS vocabulary_id,
            user_id::TEXT AS user_id,
            french_word,
            english_description,
            COALESCE(example_sentences, '{{}}'::TEXT[]) AS example_sentences
        FROM fact_vocabulary
        WHERE {' AND '.join(conditions)}
        {' '.join(order_and_paging)}
    """

    async with db._pool.acquire() as conn:
        rows = await conn.fetch(query, *params)
    return [dict(row) for row in rows]


async def _update_row(
    db: PostgresDatabase,
    *,
    vocabulary_id: str,
    english_description: str,
    example_sentences: list[str],
    append_to_description: bool,
) -> None:
    if not db._pool:
        raise RuntimeError("Database not connected. Call connect() first.")

    async with db._pool.acquire() as conn:
        if append_to_description:
            merged_description = append_vocabulary_examples_to_description(
                english_description,
                example_sentences,
            )
            await conn.execute(
                """
                UPDATE fact_vocabulary
                SET english_description = $2,
                    example_sentences = $3
                WHERE vocabulary_id::TEXT = $1
                """,
                vocabulary_id,
                merged_description,
                example_sentences,
            )
            return

        await conn.execute(
            """
            UPDATE fact_vocabulary
            SET example_sentences = $2
            WHERE vocabulary_id::TEXT = $1
            """,
            vocabulary_id,
            example_sentences,
        )


async def _run(args: argparse.Namespace) -> int:
    config = load_config()
    if not config.postgres_enabled:
        raise SystemExit("Postgres is not configured. Set DB_* env vars or the matching .env values first.")
    if not config.openai_api_key:
        raise SystemExit("OPENAI_API_KEY is required to generate vocabulary example sentences.")

    db = PostgresDatabase.from_config(config)
    await db.connect()
    try:
        rows = await _load_candidate_rows(
            db,
            limit=args.limit,
            offset=args.offset,
            user_id=args.user_id,
            overwrite=args.overwrite,
        )
        if not rows:
            print("No vocabulary rows matched the backfill criteria.")
            return 0

        updated_count = 0
        skipped_count = 0
        for row in rows:
            example_sentences = generate_stored_vocabulary_examples(
                row["french_word"],
                row["english_description"],
            )
            if not example_sentences:
                skipped_count += 1
                print(f'Skipped {row["vocabulary_id"]} ({row["french_word"]}): no examples generated.')
                continue

            if args.dry_run:
                print(f'{row["vocabulary_id"]} | {row["french_word"]} | {example_sentences}')
                updated_count += 1
                continue

            await _update_row(
                db,
                vocabulary_id=row["vocabulary_id"],
                english_description=row["english_description"],
                example_sentences=example_sentences,
                append_to_description=args.append_to_description,
            )
            updated_count += 1
            print(f'Updated {row["vocabulary_id"]} ({row["french_word"]}).')

        print(
            f"Processed {len(rows)} rows. Updated {updated_count}. Skipped {skipped_count}."
        )
        return 0
    finally:
        await db.disconnect()


def main() -> int:
    return asyncio.run(_run(_parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
