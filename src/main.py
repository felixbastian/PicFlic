"""Command-line entrypoint for PictoAgent."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import create_default_agent, load_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze images and store records in the persistent PictoAgent database.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze_parser = subparsers.add_parser("analyze", help="Analyze an image and store the result.")
    analyze_parser.add_argument("image_path", help="Path to the image to analyze.")

    subparsers.add_parser("list", help="List stored records.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    config = load_config()
    agent = create_default_agent()

    if args.command == "analyze":
        result = agent.process_image(args.image_path)
        print(json.dumps(result, indent=2))
        print(f"Database: {config.database_path}")
        return 0

    if args.command == "list":
        records = agent.list_records()
        print(f"Database: {config.database_path}")
        print(f"Records: {len(records)}")
        for record in records:
            print(
                json.dumps(
                    {
                        "id": record.id,
                        "image_path": record.image_path,
                        "category": record.analysis.category,
                        "calories": record.analysis.calories,
                        "created_at": record.created_at,
                    }
                )
            )
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
