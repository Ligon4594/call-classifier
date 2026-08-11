"""
Avoca API client.

STATUS (2026-08-04): LIVE. Wired against the Avoca Enterprise API.

Avoca is C&R's AI virtual receptionist — it answers overflow / after-hours
inbound calls when no CSR is available. ServiceTitan shows these calls with
agent name = "Avoca". The /transcript endpoint is unreliable (404 "Transcript
not available" for most calls — reported to Avoca 2026-08-10), so the
pipeline does NOT classify these via transcripts: it maps Avoca's own
call_reason enum directly to an approved ST Call Reason (see avoca_mapping.py
and pipeline.py Step 3c). This client just pulls the call records.

Docs: https://docs.avoca.ai/
OpenAPI spec: https://docs.avoca.ai/api-reference/openapi.json

Endpoints used (all under https://enterprise-api.avoca.ai):
  GET /api/calls                  - list calls, paginated (start_date/end_date, limit/offset)
  GET /api/calls/{id}             - single call by UUID
  GET /api/calls/{id}/transcript  - ordered assistant/user turns (requires read:transcripts)
  GET /api/calls/latest-by-phone  - most recent call for a phone number

Auth: Bearer token via the AVOCA_API_KEY env var (format: avoca_<64 hex chars>).

To generate a key: Avoca dashboard -> Settings -> API Keys (team-level, under
/team/[slug]/settings/api-keys). Grant it the `read:calls` and
`read:transcripts` permission scopes — that's all this client needs.

A team-level key is locked to one team and doesn't need AVOCA_TEAM_ID. If
C&R ever uses an enterprise/portfolio multi-team key instead, set
AVOCA_TEAM_ID and it will be sent as the `x-team-id` header.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .models import AvocaCall

AVOCA_API_BASE = "https://enterprise-api.avoca.ai"
DEFAULT_TIMEOUT_SECONDS = 30.0


def _normalize_phone(phone: str) -> str:
    """Strip everything except digits, then take the last 10 (US numbers).

    Inlined (rather than imported from .linker) to avoid a circular import,
    matching the pattern already used in dialpad.py.
    """
    digits = re.sub(r"\D", "", phone or "")
    return digits[-10:] if len(digits) >= 10 else digits


class AvocaAPIError(RuntimeError):
    """Raised when the Avoca API returns an error response."""


class AvocaClient:
    """Client for the Avoca Enterprise API (read-only).

    Designed for the call classifier pipeline. Methods return our internal
    `AvocaCall` dataclass, shaped to match `DialpadCall` so downstream code
    (linker.link_batch, Classifier.classify, pipeline._call_was_answered)
    works against it without any special-casing.
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        team_id: Optional[str] = None,
        base_url: str = AVOCA_API_BASE,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ):
        self.api_key = api_key or os.environ.get("AVOCA_API_KEY")
        if not self.api_key:
            raise ValueError(
                "AVOCA_API_KEY is not set. Either pass api_key=... or set "
                "the AVOCA_API_KEY environment variable. Generate one in the "
                "Avoca dashboard: Settings -> API Keys, with the read:calls "
                "and read:transcripts permissions."
            )
        self.team_id = team_id or os.environ.get("AVOCA_TEAM_ID")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # ------------------------------------------------------------------
    # Lifecycle (stdlib urllib has no persistent client to close)
    # ------------------------------------------------------------------

    def close(self) -> None:
        pass

    def __enter__(self) -> "AvocaClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Low-level GET helper
    # ------------------------------------------------------------------

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        url = self.base_url + path
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url = url + "?" + urllib.parse.urlencode(clean)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }
        if self.team_id:
            headers["x-team-id"] = str(self.team_id)
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read()
                if not body:
                    return {}
                return json.loads(body.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return {}
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                detail = ""
            raise AvocaAPIError(
                f"Avoca API error {exc.code} on GET {path}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise AvocaAPIError(f"Avoca API connection error on GET {path}: {exc}") from exc

    # ------------------------------------------------------------------
    # PUBLIC INTERFACE — pipeline depends on these signatures.
    # ------------------------------------------------------------------

    def get_calls_in_window(
        self,
        *,
        start: datetime,
        end: datetime,
        max_pages: int = 50,
    ) -> list[AvocaCall]:
        """Pull all Avoca calls in a time window.

        Note: like Dialpad's list endpoint, this returns base call data only
        — no transcript. Call get_call_transcript(call_id) separately for
        each match the linker finds (the pipeline does this lazily, same
        pattern as Dialpad recap enrichment).

        Pagination: walks `offset` using the `pagination.has_more` flag, up
        to `max_pages` (default 50, at limit=1000/page = 50,000 calls) to
        avoid runaway pulls.
        """
        limit = 1000
        offset = 0
        results: list[AvocaCall] = []
        pages_fetched = 0
        while pages_fetched < max_pages:
            page = self._get(
                "/api/calls",
                params={
                    "start_date": _iso(start),
                    "end_date": _iso(end),
                    "limit": limit,
                    "offset": offset,
                },
            )
            items: list[dict] = page.get("data") or []
            for item in items:
                results.append(_build_avoca_call(item))
            pages_fetched += 1
            pagination = page.get("pagination") or {}
            if not pagination.get("has_more"):
                break
            offset += limit
        return results

    def get_call(self, call_id: str) -> Optional[AvocaCall]:
        """Fetch a single call by Avoca call ID (no transcript — see get_call_transcript)."""
        page = self._get(f"/api/calls/{call_id}")
        data = page.get("data")
        if not data:
            return None
        return _build_avoca_call(data)

    def get_call_transcript(self, call_id: str) -> Optional[str]:
        """Fetch a call's transcript and flatten it into readable speaker-tagged text.

        Returns None if the call has no transcript (e.g. still processing, or
        a pre-call transfer where the AI never engaged).
        """
        page = self._get(f"/api/calls/{call_id}/transcript")
        data = page.get("data") or {}
        turns = data.get("transcript") or []
        if not turns:
            return None
        lines = []
        for turn in turns:
            role = "Avoca AI" if turn.get("role") == "assistant" else "Customer"
            content = (turn.get("content") or "").strip()
            if content:
                lines.append(f"{role}: {content}")
        return "\n".join(lines) if lines else None

    def find_latest_call_by_phone(self, phone: str) -> Optional[AvocaCall]:
        """Get the most recent Avoca call for a phone number. Useful for spot-checks."""
        page = self._get("/api/calls/latest-by-phone", params={"phone": phone})
        data = page.get("data")
        if not data:
            return None
        return _build_avoca_call(data)


# ----------------------------------------------------------------------
# Pure functions: JSON -> AvocaCall mapping
# ----------------------------------------------------------------------


def _iso(dt: datetime) -> str:
    """Format a datetime as ISO 8601 UTC, matching Avoca's start_date/end_date params."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


# Sentinel for records whose timestamp couldn't be parsed. Far in the past so
# the record can never fuzzy-match anything by time — a call with an unknown
# time must FAIL to match, never match something at random. (Records with a
# service_titan_job_id are still rescued by link_avoca_batch's exact-ID path.)
_UNPARSEABLE_TIMESTAMP = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _parse_avoca_timestamp(value: Any) -> datetime:
    """Parse Avoca's created_at (ISO 8601, e.g. '2026-08-04T13:05:00.42763Z').

    Avoca emits VARIABLE-precision fractional seconds (trailing zeros are
    stripped, so '.42763' with 5 digits is common). Python 3.9's
    datetime.fromisoformat only accepts exactly 3 or 6 digits and raises
    ValueError on anything else — Python 3.11 (the Railway image) is lenient,
    so this failed locally but not in prod. Normalize to 6 digits first.

    Failures return _UNPARSEABLE_TIMESTAMP, never now() — returning now()
    silently planted today's timestamp on ~20% of records (found 2026-08-10),
    making them match the wrong window or nothing at all.
    """
    if not value:
        return _UNPARSEABLE_TIMESTAMP
    s = str(value).replace("Z", "+00:00")
    # Pad/truncate fractional seconds to exactly 6 digits for Python 3.9.
    m = re.match(r"^(.*?\.)(\d+)([+-].*|)$", s)
    if m:
        s = m.group(1) + (m.group(2) + "000000")[:6] + m.group(3)
    try:
        parsed = datetime.fromisoformat(s)
    except (TypeError, ValueError):
        print(f"  Warning: unparseable Avoca timestamp {value!r} — record will not time-match.",
              file=sys.stderr)
        return _UNPARSEABLE_TIMESTAMP
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _build_avoca_call(call_json: dict) -> AvocaCall:
    """Map an /api/calls or /api/calls/{id} JSON object into our AvocaCall dataclass.

    Transcript is intentionally left as None here — the list/get endpoints
    don't return it. Callers enrich matched calls via get_call_transcript().
    """
    ended_at = _parse_avoca_timestamp(call_json.get("created_at"))
    duration_seconds = int(call_json.get("duration_seconds") or 0)
    # Avoca's `created_at` is stamped when the call ENDS, so the start is
    # created_at - duration. ServiceTitan's received_at is stamped when the
    # call arrives, ~36-120s earlier (ring/queue before Avoca answers) — well
    # inside link_avoca_batch's 180s window. Matching raw created_at instead
    # fails for any call over ~60s, which is most of them (measured
    # 2026-08-10: match rate 7/23 raw vs 20/23 with this correction).
    started_at = ended_at - timedelta(seconds=duration_seconds)

    # Avoca's AI engages the moment it picks up (unlike a phone ringing before
    # a human answers), so any nonzero duration means real engagement. The one
    # exception — pre-call transfers where the AI never engaged, duration=0 —
    # lives on a separate endpoint (list-pre-call-transfers) that this client
    # doesn't pull, so every call returned here can be treated as "connected".
    connected_seconds = duration_seconds

    # Prefer the AI-generated natural-language summary (ai_summary) when the
    # team's column allowlist exposes it — it's far more useful classifier
    # context than the raw enum-style call_reason/call_outcome values (e.g.
    # "EXCUSED_RETURNING_CALL"). Fall back to synthesizing one from whatever
    # enum fields ARE present if ai_summary isn't in the allowlist.
    ai_summary = (call_json.get("ai_summary") or "").strip()
    if ai_summary:
        recap = ai_summary
    else:
        call_reason = call_json.get("call_reason") or ""
        call_outcome = call_json.get("call_outcome") or ""
        booking_result = call_json.get("booking_result") or ""
        recap_parts = [
            p for p in (
                f"Avoca call reason: {call_reason}." if call_reason else "",
                f"Avoca outcome: {call_outcome}." if call_outcome else "",
                f"Avoca booking result: {booking_result}." if booking_result else "",
            ) if p
        ]
        recap = " ".join(recap_parts) if recap_parts else None

    # ServiceTitan Job ID — when the team's column allowlist exposes this,
    # it's an exact match key back to ServiceTitanCall.job_id, more reliable
    # than phone+timestamp fuzzy matching. See linker.link_avoca_by_job_id.
    st_job_id_raw = call_json.get("service_titan_job_id")
    try:
        st_job_id = int(st_job_id_raw) if st_job_id_raw not in (None, "") else None
    except (TypeError, ValueError):
        st_job_id = None

    return AvocaCall(
        call_id=str(call_json.get("call_id") or ""),
        external_number=call_json.get("phone_number") or "",
        internal_user="Avoca AI",
        started_at=started_at,
        duration_seconds=duration_seconds,
        connected_seconds=connected_seconds,
        transcript=None,
        recap=recap,
        action_items=[],
        moments={},
        service_titan_job_id=st_job_id,
        raw={"call": call_json},
    )
