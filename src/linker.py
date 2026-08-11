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


def _customer_phone(st_call: ServiceTitanCall) -> str:
    """Return the customer-side phone number for a ServiceTitan call.

    For inbound calls the customer is the caller (leadCall.from).
    For outbound calls the customer is the callee (leadCall.to) — the agent's
    line is stored in 'from', which is the same number on every outbound call
    and would never match against Dialpad's external_number index.
    """
    if st_call.direction.lower() == "outbound":
        return normalize_phone(st_call.callee_phone or st_call.caller_phone)
    return normalize_phone(st_call.caller_phone)


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
    st_phone = _customer_phone(st_call)

    window = timedelta(seconds=window_seconds)
    candidates = dp_client.get_calls_in_window(
        start=st_call.received_at - window,
        end=st_call.received_at + window,
    )

    matches = [c for c in candidates if normalize_phone(c.external_number) == st_phone]
    return _pick_best_match(st_call, matches, window_seconds)


def link_avoca_batch(
    st_calls: list[ServiceTitanCall],
    avoca_calls: list,
    *,
    window_seconds: int = 180,
) -> list[LinkedCall]:
    """Match Avoca-handled ST calls to Avoca API calls, preferring an exact
    ServiceTitan Job ID match over phone+timestamp fuzzy matching.

    Avoca can optionally expose `service_titan_job_id` on each call record
    (team column-allowlist dependent — see AvocaCall.service_titan_job_id).
    When both sides have a job_id and they match, that's a certain match —
    no ambiguity possible, unlike phone+time windows where two calls from
    the same number close together could be confused. ST calls that don't
    have a job_id yet (not booked at call time) or whose Avoca record lacks
    service_titan_job_id fall through to the existing phone+timestamp logic.
    """
    # Index Avoca calls by ST job_id, for the ones that have it.
    avoca_by_job_id: dict[int, object] = {
        av.service_titan_job_id: av
        for av in avoca_calls
        if getattr(av, "service_titan_job_id", None)
    }

    exact_matched: list[LinkedCall] = []
    remaining_st_calls: list[ServiceTitanCall] = []
    matched_avoca_ids: set[str] = set()

    for st_call in st_calls:
        av = avoca_by_job_id.get(st_call.job_id) if st_call.job_id else None
        if av is not None:
            exact_matched.append(LinkedCall(
                servicetitan=st_call,
                dialpad=av,
                match_confidence=1.0,
                match_method="avoca_job_id_exact",
            ))
            matched_avoca_ids.add(av.call_id)
        else:
            remaining_st_calls.append(st_call)

    # Fuzzy-match everything else by phone+timestamp, same as Dialpad.
    # Exclude Avoca calls already claimed by an exact job_id match so they
    # can't also be picked up as a fuzzy match for a different ST call.
    remaining_avoca_calls = [av for av in avoca_calls if av.call_id not in matched_avoca_ids]
    fuzzy_matched = link_batch(remaining_st_calls, remaining_avoca_calls, window_seconds=window_seconds)

    return exact_matched + fuzzy_matched


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
        # Use the customer-side phone for matching — for outbound calls this is
        # callee_phone (leadCall.to), not caller_phone (the agent's own line).
        st_phone = _customer_phone(st_call)
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
