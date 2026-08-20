#!/usr/bin/env python3
"""Diagnose WHY Avoca-handled ST calls aren't matching Avoca API records.

Prints both sides (ST Avoca calls + Avoca API calls) with normalized phone
and timestamp, then shows for each unmatched ST call what the nearest Avoca
record by phone is (and the time delta), so we can tell whether the failure
is a phone mismatch, a timestamp-window problem, or missing data on Avoca's side.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() and v.strip():
            os.environ.setdefault(k.strip(), v.strip())

from src.avoca import AvocaClient
from src.linker import normalize_phone, _customer_phone
from src.servicetitan import ServiceTitanClient

days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
end_date = date.today()
start_date = end_date - timedelta(days=days)

st = ServiceTitanClient()
st_calls = st.get_all_calls(start_date=start_date, end_date=end_date + timedelta(days=1))
avoca_st = [c for c in st_calls if (c.agent_name or "").strip().lower() == "avoca"]

av_client = AvocaClient()
av_start = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc) - timedelta(minutes=5)
# end_date is inclusive — same +1 day as pipeline.py (Avoca tolerates a future end).
av_end = datetime.combine(end_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc) + timedelta(minutes=5)
avoca_calls = av_client.get_calls_in_window(start=av_start, end=av_end)

print(f"ST Avoca-handled calls: {len(avoca_st)}")
print(f"Avoca API calls in window ({av_start} -> {av_end}): {len(avoca_calls)}")
print()

print("=== AVOCA API RECORDS ===")
for av in avoca_calls:
    raw = (av.raw or {}).get("call", {})
    print(f"  {av.call_id[:12]}  phone={normalize_phone(av.external_number):<12} "
          f"start={av.started_at}  dur={av.duration_seconds:<5} "
          f"st_job={av.service_titan_job_id}  reason={raw.get('call_reason')!r}")
print()

print("=== ST AVOCA CALLS ===")
for c in avoca_st:
    print(f"  call={c.call_id}  phone={_customer_phone(c):<12} dir={c.direction:<9} "
          f"recv={c.received_at}  job={c.job_id}  reason={c.reason_name!r}")
print()

print("=== PER-ST-CALL NEAREST AVOCA RECORD (by phone) ===")
for c in avoca_st:
    p = _customer_phone(c)
    same_phone = [av for av in avoca_calls if normalize_phone(av.external_number) == p]
    st_time = c.received_at if c.received_at.tzinfo else c.received_at.replace(tzinfo=timezone.utc)
    if not same_phone:
        print(f"  call={c.call_id} phone={p} -> NO Avoca record with this phone at all")
        continue
    best = min(same_phone, key=lambda av: abs((av.started_at - st_time).total_seconds()))
    delta = (best.started_at - st_time).total_seconds()
    raw = (best.raw or {}).get("call", {})
    print(f"  call={c.call_id} phone={p} -> nearest avoca {best.call_id[:12]} "
          f"delta={delta:+.0f}s  (window=180s)  reason={raw.get('call_reason')!r}")

print()
print("=== RAW FIELD KEYS ON FIRST AVOCA RECORD ===")
if avoca_calls:
    print(sorted((avoca_calls[0].raw or {}).get("call", {}).keys()))
