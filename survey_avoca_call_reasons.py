#!/usr/bin/env python3
"""
Survey ALL distinct Avoca classification values across every historical call.

Before we can safely map Avoca's own call_reason/call_outcome/disposition
fields onto C&R's approved ServiceTitan rulebook (governance rule: never
invent a Call Reason that doesn't already exist in ST), we need to see the
COMPLETE vocabulary Avoca actually uses for this account -- not just the 3
example values we've spot-checked by hand.

This pulls every call Avoca has on record for the team (in monthly chunks,
since the API doesn't document a max lookback) and tallies distinct values
for:
  - call_reason
  - call_outcome
  - is_booked / is_bookable / booking_result
  - non_booked_disposition / non_booked_disposition_code
  - job_type

For each distinct call_reason value, it also prints one example call (id +
phone + duration) so we can spot-check a couple in the Avoca dashboard UI
to confirm what the value actually means in practice.

Usage:
  python3 survey_avoca_call_reasons.py                # last 12 months
  python3 survey_avoca_call_reasons.py --months 24     # last 24 months
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict
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


FIELDS_OF_INTEREST = [
    "call_reason",
    "call_outcome",
    "is_booked",
    "is_bookable",
    "booking_result",
    "non_booked_disposition",
    "non_booked_disposition_code",
    "job_type",
]


def fetch_raw_calls_in_window(client: AvocaClient, start: datetime, end: datetime) -> list[dict]:
    """Pull raw call JSON (not mapped to AvocaCall) so we see every configured field."""
    limit = 1000
    offset = 0
    results: list[dict] = []
    pages_fetched = 0
    while pages_fetched < 50:
        page = client._get(  # noqa: SLF001 -- intentional, survey needs the raw dicts
            "/api/calls",
            params={
                "start_date": start.astimezone(timezone.utc).isoformat(),
                "end_date": end.astimezone(timezone.utc).isoformat(),
                "limit": limit,
                "offset": offset,
            },
        )
        items = page.get("data") or []
        results.extend(items)
        pages_fetched += 1
        pagination = page.get("pagination") or {}
        if not pagination.get("has_more"):
            break
        offset += limit
    return results


def main():
    parser = argparse.ArgumentParser(description="Survey Avoca's call classification vocabulary")
    parser.add_argument("--months", type=int, default=12, help="How many months back to pull (default: 12)")
    args = parser.parse_args()

    if not os.environ.get("AVOCA_API_KEY"):
        print("ERROR: AVOCA_API_KEY is not set in .env")
        sys.exit(1)

    client = AvocaClient()

    end = datetime.now(tz=timezone.utc)
    overall_start = end - timedelta(days=30 * args.months)

    print(f"Pulling Avoca calls from {overall_start.date()} to {end.date()} "
          f"in monthly chunks (this may take a minute)...\n")

    all_calls: list[dict] = []
    chunk_end = end
    while chunk_end > overall_start:
        chunk_start = max(overall_start, chunk_end - timedelta(days=30))
        try:
            chunk = fetch_raw_calls_in_window(client, chunk_start, chunk_end)
        except AvocaAPIError as e:
            print(f"  ERROR pulling {chunk_start.date()} to {chunk_end.date()}: {e}")
            chunk = []
        print(f"  {chunk_start.date()} to {chunk_end.date()}: {len(chunk)} calls")
        all_calls.extend(chunk)
        chunk_end = chunk_start

    print(f"\n{'=' * 70}")
    print(f"TOTAL CALLS PULLED: {len(all_calls)}")
    print(f"{'=' * 70}\n")

    if not all_calls:
        print("No calls found. Nothing to survey.")
        return

    # Tally distinct values per field, with one example call_id per value.
    value_counts: dict[str, Counter] = defaultdict(Counter)
    value_example: dict[str, dict] = defaultdict(dict)

    for call in all_calls:
        for field in FIELDS_OF_INTEREST:
            val = call.get(field)
            key = "(null)" if val is None else str(val)
            value_counts[field][key] += 1
            if key not in value_example[field]:
                value_example[field][key] = call

    for field in FIELDS_OF_INTEREST:
        counts = value_counts[field]
        if not counts:
            continue
        print(f"\n--- {field} ---")
        for val, n in counts.most_common():
            example = value_example[field][val]
            ex_id = example.get("call_id", "?")
            ex_phone = example.get("phone_number", "?")
            ex_dur = example.get("duration_seconds", "?")
            print(f"  {val:40s}  count={n:5d}   e.g. {ex_id}  ({ex_phone}, {ex_dur}s)")

    # Cross-tab: for each call_reason, what job_type (if any) tends to go with it?
    # This is the key signal for building the mapping table.
    print(f"\n{'=' * 70}")
    print("CROSS-TAB: call_reason -> job_type (booked calls only)")
    print(f"{'=' * 70}")
    cross: dict[str, Counter] = defaultdict(Counter)
    for call in all_calls:
        if call.get("is_booked"):
            reason = str(call.get("call_reason") or "(null)")
            jt = str(call.get("job_type") or "(null)")
            cross[reason][jt] += 1
    for reason, jt_counts in sorted(cross.items()):
        print(f"\n  {reason}:")
        for jt, n in jt_counts.most_common():
            print(f"    -> {jt:40s}  count={n}")

    print(f"\n{'=' * 70}")
    print("Done. Paste this full output back so the mapping table can be built.")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
