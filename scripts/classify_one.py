#!/usr/bin/env python3
"""
CLI to classify a single call from a JSON fixture file.

USAGE:
    # Dry-run mode (no API key needed — prints the prompt to stdout):
    python scripts/classify_one.py tests/fixtures/sample_call_gita_ranum.json --dry-run

    # Live mode (requires ANTHROPIC_API_KEY in env or .env):
    python scripts/classify_one.py tests/fixtures/sample_call_gita_ranum.json

The dry-run path is useful for verifying the rules + prompt structure
without burning API credit.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# Allow running this script directly without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.classifier import Classifier
from src.models import DialpadCall, ServiceTitanCall


def load_fixture(path: Path) -> tuple[ServiceTitanCall, DialpadCall]:
    raw = json.loads(path.read_text())

    st = raw["servicetitan"]
    dp = raw["dialpad"]

    st_call = ServiceTitanCall(
        call_id=st["call_id"],
        caller_phone=st["caller_phone"],
        direction=st["direction"],
        received_at=datetime.fromisoformat(st["received_at"]),
        duration_seconds=st["duration_seconds"],
        call_type=st["call_type"],
        customer_name=st.get("customer_name"),
        recording_url=st.get("recording_url"),
        raw=st,
    )

    dp_call = DialpadCall(
        call_id=dp["call_id"],
        external_number=dp["external_number"],
        internal_user=dp["internal_user"],
        started_at=datetime.fromisoformat(dp["started_at"]),
        duration_seconds=dp["duration_seconds"],
        connected_seconds=dp["connected_seconds"],
        transcript=dp.get("transcript"),
        recap=dp.get("recap"),
        action_items=dp.get("action_items", []),
        moments=dp.get("moments", {}),
        raw=dp,
    )

    return st_call, dp_call


def main():
    parser = argparse.ArgumentParser(description="Classify a single call from a JSON fixture.")
    parser.add_argument("fixture", type=Path, help="Path to fixture JSON file")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the constructed prompt instead of calling the API. No API key required.",
    )
    args = parser.parse_args()

    if not args.fixture.exists():
        print(f"ERROR: fixture file not found: {args.fixture}", file=sys.stderr)
        sys.exit(1)

    st_call, dp_call = load_fixture(args.fixture)

    mode = "dry_run" if args.dry_run else "live"
    classifier = Classifier(mode=mode)
    result = classifier.classify(st_call, dp_call)

    print()
    print("=" * 80)
    print("CLASSIFICATION RESULT")
    print("=" * 80)
    print(f"Call ID:                {result.call_id}")
    print(f"Type:                   {result.classification_type}")
    print(f"Value:                  {result.classification_value}")
    print(f"Confidence:             {result.confidence:.2f}")
    print(f"Should have booked:     {result.should_have_been_booked}")
    if result.booking_recommendation:
        print(f"Booking recommendation: {result.booking_recommendation}")
    print(f"Reasoning:              {result.reasoning}")
    print(f"Classifier version:     {result.classifier_version}")


if __name__ == "__main__":
    main()
