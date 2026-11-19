#!/usr/bin/env python3
"""Rebuild the normalized layer from a previously extracted InfoMentor JSON file.

Useful while iterating on classification/resource extraction without logging in again.
Raw source data is preserved unchanged.
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

from infomentor_extract import build_normalized


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", help="Existing infomentor.json with raw data")
    parser.add_argument("--output", default="infomentor.v3.json")
    args = parser.parse_args()

    source = json.loads(Path(args.input).read_text(encoding="utf-8"))
    reference = None
    extracted_at = source.get("extracted_at")
    if extracted_at:
        try:
            reference = datetime.fromisoformat(str(extracted_at).replace("Z", "+00:00")).date()
        except ValueError:
            reference = None

    for child in source.get("children", []):
        raw = child.get("raw", {})
        child["normalized"] = build_normalized(
            raw.get("timetable", []),
            raw.get("calendar", []),
            raw.get("tasks", {}),
            raw.get("notifications", {}),
            raw.get("planning", {}),
            reference=reference,
        )
    source["schema_version"] = 3
    Path(args.output).write_text(json.dumps(source, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
