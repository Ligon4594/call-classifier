"""
Joins ServiceTitan call records to Dialpad call records.

Neither system stores the other system's call ID, so we match by:
  1. Phone number (normalized to digits-only, last 10)
  2. Timestamp (within a configurable window — usually within seconds)

Two modes:
  - link_call(): for single-call linking, fetches Dialpad candidates on-demand.
  - link_batch(): for pipeline runs, pre-fetches all Dialpad calls in the date
    range and matches locally. Much more efficient at scale.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from .dialpad import DialpadClient
from .models import DialpadCall, LinkedCall, ServiceTitanCall


def _ensure_aware(dt: datetime) -> datetime:
    """Ensure a datetime is timezone-aware (assume UTC if naive)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def normalize_phone(phone: str) -> str:
    """Strip everything except digits, then take the last 10 (US numbers)."""
    digits = re.sub(r"\D", "", phone or "")
    return digits[-10:] if len(digits) >= 10 else digits


def link_call(
    st_call: ServiceTitanCall,
    dp_client: DialpadClient,
    *,
    window_seconds: int = 120,
) -> LinkedCall:
    """Find the best Dialpad match for a single ServiceTitan call.

    Makes a Dialpad API call (get_calls_in_window) for each ST call.
    Use link_batch() instead for pipeline runs.
    """
    st_phone = normalize_phone(st_call.caller_phone)

    window = timedelta(seconds=window_seconds)
    candidates = dp_client.get_calls_in_window(
        start=st_call.received_at - window,
        end=st_call.received_at + window,
    )

    matches = [c for c in candidates if normalize_phone(c.external_number) == st_phone]
    return _pick_best_match(st_call, matches, window_seconds)


def link_batch(
    st_calls: list[ServiceTitanCall],
    dp_calls: list[DialpadCall],
    *,
    window_seconds: int = 120,
) -> list[LinkedCall]:
    """Match a batch of ServiceTitan calls to pre-fetched Dialpad calls.

    This avoids per-call Dialpad API lookups. The pipeline should pre-fetch
    all Dialpad calls in the date range via dp_client.get_calls_in_window()
    ONCE, then pass them here.
    """
    # Build a lookup: normalized phone -> list of DialpadCalls
    dp_by_phone: dict[str, list[DialpadCall]] = {}
    for dp in dp_calls:
        phone = normalize_phone(dp.external_number)
        if phone:
            dp_by_phone.setdefault(phone, []).append(dp)

    results: list[LinkedCall] = []
    for st_call in st_calls:
        st_phone = normalize_phone(st_call.caller_phone)
        candidates = dp_by_phone.get(st_phone, [])

        # Filter to time window (ensure tz-aware to avoid crash)
        st_time = _ensure_aware(st_call.received_at)
        matches = [
            c for c in candidates
            if abs((_ensure_aware(c.started_at) - st_time).total_seconds()) <= window_seconds
        ]

        results.append(_pick_best_match(st_call, matches, window_seconds))

    return results


def _pick_best_match(
    st_call: ServiceTitanCall,
    matches: list[DialpadCall],
    window_seconds: int,
) -> LinkedCall:
    """Pick the best Dialpad match from a list of phone+time candidates."""

    if not matches:
        return LinkedCall(
            servicetitan=st_call,
            dialpad=None,
            match_confidence=0.0,
            match_method="no_match",
        )

    def time_delta(dp: DialpadCall) -> float:
        return abs((_ensure_aware(dp.started_at) - _ensure_aware(st_call.received_at)).total_seconds())

    best = min(matches, key=time_delta)
    delta = time_delta(best)

    # Confidence: 1.0 if within 5 seconds, decays linearly to 0.5 at the window edge
    if delta <= 5:
        confidence = 1.0
        method = "phone+timestamp_exact"
    else:
        confidence = max(0.5, 1.0 - (delta / window_seconds) * 0.5)
        method = "phone+timestamp_window"

    return LinkedCall(
        servicetitan=st_call,
        dialpad=best,
        match_confidence=confidence,
        match_method=method,
    )
