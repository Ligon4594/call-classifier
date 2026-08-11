#!/usr/bin/env python3
"""
Quick sanity check for the Avoca API connection.

Run this once after adding AVOCA_API_KEY to .env, before trusting Avoca
data in the full pipeline. Pulls the last 7 days of Avoca calls, prints a
count, and shows the transcript for the most recent one so you can eyeball
that it looks right.

Usage:
  python3 test_avoca_connection.py
  python3 test_avoca_connection.py --days 1
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Load .env (same pattern as run.py)
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip()
            if key and val:
                os.environ.setdefault(key, val)

from src.avoca import AvocaClient, AvocaAPIError  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Test the Avoca API connection")
    parser.add_argument("--days", type=int, default=7, help="How many days back to pull (default: 7)")
    parser.add_argument("--call-id", type=str, default=None,
                         help="Test a specific call ID instead of pulling a window and using the most recent")
    args = parser.parse_args()

    if not os.environ.get("AVOCA_API_KEY"):
        print("ERROR: AVOCA_API_KEY is not set in .env")
        print("Generate one in the Avoca dashboard: Settings -> API Keys")
        print("(team-level key, with read:calls + read:transcripts permissions)")
        sys.exit(1)

    print(f"Connecting to Avoca API (key: {os.environ['AVOCA_API_KEY'][:12]}...)")
    try:
        client = AvocaClient()
    except ValueError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    if args.call_id:
        print(f"Fetching specific call {args.call_id}...\n")
        try:
            latest = client.get_call(args.call_id)
        except AvocaAPIError as e:
            print(f"ERROR calling Avoca API: {e}")
            sys.exit(1)
        if not latest:
            print(f"No call found with ID {args.call_id}")
            sys.exit(1)
    else:
        end = datetime.now(tz=timezone.utc)
        start = end - timedelta(days=args.days)
        print(f"Pulling Avoca calls from {start.date()} to {end.date()}...\n")

        try:
            calls = client.get_calls_in_window(start=start, end=end)
        except AvocaAPIError as e:
            print(f"ERROR calling Avoca API: {e}")
            print("\nCommon causes:")
            print("  - API key is wrong or was revoked")
            print("  - Key is missing the read:calls permission")
            print("  - Using an enterprise/portfolio key without AVOCA_TEAM_ID set")
            sys.exit(1)

        print(f"Got {len(calls)} call(s) in the last {args.days} day(s).\n")

        if not calls:
            print("No calls found in this window — try a wider --days range, or "
                  "confirm calls were actually routed to Avoca recently.")
            return

        # Show the most recent call + its transcript
        latest = max(calls, key=lambda c: c.started_at)
    print("Call detail:" if args.call_id else "Most recent Avoca call:")
    print(f"  Call ID:  {latest.call_id}")
    print(f"  Phone:    {latest.external_number}")
    print(f"  Started:  {latest.started_at}")
    print(f"  Duration: {latest.duration_seconds}s")
    print(f"  ST Job ID:{latest.service_titan_job_id}")
    print(f"  Recap:    {latest.recap or '(none)'}")
    print()

    try:
        transcript = client.get_call_transcript(latest.call_id)
    except AvocaAPIError as e:
        print(f"ERROR fetching transcript: {e}")
        print("Common cause: key is missing the read:transcripts permission.")
        sys.exit(1)

    if transcript:
        print("Transcript:")
        print("-" * 60)
        print(transcript)
        print("-" * 60)
        print("\n✓ Avoca connection is working — transcripts are readable.")
    else:
        print("⚠ No transcript available for this call (may still be processing,")
        print("  or this was a pre-call transfer where the AI never engaged).")
        print("  Try running again in a few minutes, or with a call you know")
        print("  had a real conversation.")


if __name__ == "__main__":
    main()
