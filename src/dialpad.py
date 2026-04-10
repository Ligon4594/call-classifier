"""
Dialpad API client.

STATUS (2026-04-09): LIVE. Wired against the Dialpad public REST API v2.

Endpoints used (all under https://dialpad.com/api/v2):
  GET /call/{id}                 - base call metadata + transcription_text   (10/min)
  GET /call/{id}/ai_recap        - AI Recap (summary, action items, purpose) (12/min)
  GET /transcripts/{call_id}     - granular speaker-tagged transcript        (1200/min)
  GET /call                      - paginated call list with started_after/started_before
                                   (UTC ms-since-epoch)                      (1200/min)

Auth: Bearer token via the DIALPAD_API_KEY env var. Requires a Company Admin
key. The AI Recap endpoint requires the `ai_recap` scope on the key.

Reference: https://developers.dialpad.com/reference/calllist
           https://developers.dialpad.com/reference/callget_call_info
           https://developers.dialpad.com/reference/callai_recap
           https://developers.dialpad.com/reference/transcriptsget
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from .models import DialpadCall


def _normalize_phone(phone: str) -> str:
    """Strip everything except digits, then take the last 10 (US numbers).

    Inlined here (instead of imported from .linker) to avoid a circular
    import — linker.py depends on this module for DialpadClient.
    """
    digits = re.sub(r"\D", "", phone or "")
    return digits[-10:] if len(digits) >= 10 else digits


DIALPAD_API_BASE = "https://dialpad.com/api/v2"

# Dialpad enforces 10/min on /call/{id} which is the bottleneck for our pipeline.
# We'll let the caller handle rate limiting in the pipeline; this client just
# raises HTTPStatusError on 429 so callers can back off.
DEFAULT_TIMEOUT_SECONDS = 30.0


class DialpadAPIError(RuntimeError):
    """Raised when the Dialpad API returns an error response."""


class DialpadClient:
    """Client for the Dialpad public API.

    Designed for the call classifier pipeline. Methods return our internal
    `DialpadCall` dataclass, fully populated where possible. Callers that need
    the raw JSON can read it from `DialpadCall.raw`.
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: str = DIALPAD_API_BASE,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ):
        self.api_key = api_key or os.environ.get("DIALPAD_API_KEY")
        if not self.api_key:
            raise ValueError(
                "DIALPAD_API_KEY is not set. Either pass api_key=... or set "
                "the DIALPAD_API_KEY environment variable."
            )
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # ------------------------------------------------------------------
    # Lifecycle (stdlib urllib has no persistent client to close)
    # ------------------------------------------------------------------

    def close(self) -> None:
        pass

    def __enter__(self) -> "DialpadClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Low-level GET helper
    # ------------------------------------------------------------------

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        url = self.base_url + path
        if params:
            # Drop None values; urlencode doesn't like them.
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url = url + "?" + urllib.parse.urlencode(clean)
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
            },
            method="GET",
        )
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
            raise DialpadAPIError(
                f"Dialpad API error {exc.code} on GET {path}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise DialpadAPIError(f"Dialpad API connection error on GET {path}: {exc}") from exc

    # ------------------------------------------------------------------
    # PUBLIC INTERFACE — pipeline depends on these signatures.
    # ------------------------------------------------------------------

    def get_call(self, call_id: str) -> DialpadCall:
        """Fetch a single call by Dialpad call ID, including transcript + recap.

        Makes up to 3 API calls:
          1. /call/{id}                   -> base metadata + transcription_text
          2. /call/{id}/ai_recap          -> AI summary + action items (best-effort; may 404)
          3. /call/{operator_call_id}     -> operator-leg call (only if main `target`
                                             is an office queue, not a user)

        Why the operator follow-up: for inbound calls that hit a department/queue
        (e.g. C&R's mainline), Dialpad sets `target` to the QUEUE on the entry-point
        leg, not the human who actually answered. The real user (e.g. "Julie Templin")
        lives on the operator leg, accessible via `operator_call_id`.
        """
        base = self._get(f"/call/{call_id}")
        if not base:
            raise DialpadAPIError(f"Dialpad call {call_id} not found")

        recap = self._get_call_recap_safe(call_id)

        # If the main call's target is an office queue, follow operator_call_id
        # to find the human who actually answered.
        operator_leg: Optional[dict] = None
        target = base.get("target") or {}
        if isinstance(target, dict) and target.get("type") == "office":
            operator_id = base.get("operator_call_id")
            if operator_id:
                try:
                    operator_leg = self._get(f"/call/{operator_id}")
                except DialpadAPIError:
                    operator_leg = None  # Best-effort; fall back to queue name.

        return _build_dialpad_call(base, recap, operator_leg=operator_leg)

    def get_calls_in_window(
        self,
        *,
        start: datetime,
        end: datetime,
        target_id: Optional[int] = None,
        target_type: Optional[str] = None,
        include_anonymized: bool = False,
        max_pages: int = 50,
    ) -> list[DialpadCall]:
        """Pull all calls in a time window using the /call list endpoint.

        Note: the list endpoint returns base call data only — no recap. If you
        need recaps too, call `get_call(call_id)` for each result. The linker
        only needs phone+timestamp matching, so for speed we skip the recap
        fetch in the bulk path and let the pipeline lazily enrich matches.

        Pagination: walks the cursor up to `max_pages` (default 50) to avoid
        runaway pulls.
        """
        params: dict[str, Any] = {
            "started_after": _datetime_to_ms(start),
            "started_before": _datetime_to_ms(end),
            "include_anonymized": "true" if include_anonymized else "false",
        }
        if target_id is not None:
            params["target_id"] = target_id
        if target_type is not None:
            params["target_type"] = target_type

        results: list[DialpadCall] = []
        pages_fetched = 0
        cursor: Optional[str] = None
        while pages_fetched < max_pages:
            if cursor:
                params["cursor"] = cursor
            page = self._get("/call", params=params)
            items: Iterable[dict] = page.get("items") or []
            for item in items:
                results.append(_build_dialpad_call(item, recap=None))
            pages_fetched += 1
            cursor = page.get("cursor")
            if not cursor:
                break
        return results

    def find_call_by_phone_and_time(
        self,
        *,
        phone: str,
        approximate_time: datetime,
        window_seconds: int = 120,
    ) -> Optional[DialpadCall]:
        """Find a Dialpad call matching a phone number within a time window.

        This is the primary linkage method for joining ServiceTitan calls to
        Dialpad calls (since neither system stores the other's call ID).

        Strategy:
          1. Pull the /call list for [approximate_time-window, approximate_time+window]
          2. Filter by normalized last-10-digit phone
          3. Return the closest match by date_started; None if no candidates
          4. Enrich the chosen match with the AI Recap
        """
        from datetime import timedelta

        window = timedelta(seconds=window_seconds)
        candidates = self.get_calls_in_window(
            start=approximate_time - window,
            end=approximate_time + window,
        )
        target_phone = _normalize_phone(phone)
        matches = [
            c for c in candidates
            if _normalize_phone(c.external_number) == target_phone
        ]
        if not matches:
            return None

        # Pick the temporally closest match.
        approx_aware = _ensure_aware(approximate_time)
        best = min(
            matches,
            key=lambda c: abs((_ensure_aware(c.started_at) - approx_aware).total_seconds()),
        )

        # Enrich with the recap (and re-attach to the existing object so we
        # don't lose the base fields).
        recap = self._get_call_recap_safe(best.call_id)
        if recap:
            best.recap = _extract_recap_summary(recap)
            best.action_items = _extract_action_items(recap)
            best.raw["ai_recap"] = recap
        return best

    # ------------------------------------------------------------------
    # Optional convenience methods
    # ------------------------------------------------------------------

    def get_call_recap(self, call_id: str, *, summary_format: str = "medium") -> dict:
        """Fetch raw AI Recap JSON for a call. Returns {} if no recap exists."""
        return self._get_call_recap_safe(call_id, summary_format=summary_format)

    def get_call_transcript(self, call_id: str) -> dict:
        """Fetch raw granular transcript JSON (lines + moments). Returns {} on 404."""
        return self._get(f"/transcripts/{call_id}")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_call_recap_safe(
        self, call_id: str, *, summary_format: str = "medium"
    ) -> dict:
        """Fetch AI recap; tolerate 404 (no recap available) by returning {}."""
        try:
            return self._get(
                f"/call/{call_id}/ai_recap",
                params={"summary_format": summary_format},
            )
        except DialpadAPIError:
            # Some calls (e.g. very short or transferred) have no recap.
            return {}


# ----------------------------------------------------------------------
# Pure functions: JSON -> DialpadCall mapping
# ----------------------------------------------------------------------


def _datetime_to_ms(dt: datetime) -> int:
    """Convert a datetime to UTC milliseconds-since-epoch (Dialpad's format)."""
    return int(_ensure_aware(dt).timestamp() * 1000)


def _to_int(value: Any) -> Optional[int]:
    """Coerce a Dialpad numeric field to int, tolerating str/float/None.

    The Dialpad docs say fields like `date_started`, `duration`, `call_id`
    are int64, but the live API actually returns them as JSON strings (e.g.
    "1775535793000"). This helper accepts either form.
    """
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def _ms_to_datetime(ms: Any) -> Optional[datetime]:
    """Convert ms-since-epoch (int OR string) to a UTC datetime."""
    ms_int = _to_int(ms)
    if ms_int is None:
        return None
    return datetime.fromtimestamp(ms_int / 1000, tz=timezone.utc)


def _ensure_aware(dt: datetime) -> datetime:
    """Treat naive datetimes as UTC. Dialpad timestamps are always UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _extract_recap_summary(recap_json: dict) -> Optional[str]:
    """Pull the summary text from a /call/{id}/ai_recap response."""
    summary = recap_json.get("summary") if recap_json else None
    if isinstance(summary, dict):
        return summary.get("content")
    return None


def _extract_action_items(recap_json: dict) -> list[str]:
    items = recap_json.get("action_items") if recap_json else None
    if not items:
        return []
    out: list[str] = []
    for item in items:
        if isinstance(item, dict) and item.get("content"):
            out.append(item["content"])
    return out


def _extract_purposes(recap_json: dict) -> list[str]:
    purposes = recap_json.get("purposes") if recap_json else None
    if not purposes:
        return []
    return [p["content"] for p in purposes if isinstance(p, dict) and p.get("content")]


def _internal_user_name(call_json: dict, operator_leg: Optional[dict] = None) -> str:
    """The Dialpad user who handled the call.

    Resolution order:
      1. If `operator_leg` was passed AND its target is a `user`, use that name.
         (This is the case for inbound queue calls where the entry-point leg's
         `target` is the office/department, not the person who answered.)
      2. Inbound calls where the entry-point target IS already a user -> that name.
      3. Outbound calls -> proxy_target.
      4. Fallback to any non-customer-looking name we can find.
    """
    # 1) Operator leg, if provided and it points to an actual user.
    if operator_leg:
        op_target = operator_leg.get("target") or {}
        if isinstance(op_target, dict) and op_target.get("type") == "user" and op_target.get("name"):
            return op_target["name"]

    direction = (call_json.get("direction") or "").lower()

    # 2) Inbound: only trust target.name if it's actually a user (not an office queue).
    if direction == "inbound":
        target = call_json.get("target") or {}
        if isinstance(target, dict) and target.get("type") == "user" and target.get("name"):
            return target["name"]

    # 3) Outbound: proxy_target is the C&R user dialing out.
    if direction == "outbound":
        proxy = call_json.get("proxy_target") or {}
        if isinstance(proxy, dict) and proxy.get("name"):
            return proxy["name"]

    # 4) Last-resort fallback: any name we can find on user-typed nodes.
    for key in ("target", "proxy_target", "entry_point_target"):
        node = call_json.get(key) or {}
        if isinstance(node, dict) and node.get("type") == "user" and node.get("name"):
            return node["name"]
    # 4b) Truly desperate fallback: any name at all (will be queue/office for
    # inbound queue calls — better than empty string for logging).
    for key in ("target", "proxy_target", "entry_point_target"):
        node = call_json.get(key) or {}
        if isinstance(node, dict) and node.get("name"):
            return node["name"]
    return ""


def _build_dialpad_call(
    call_json: dict,
    recap: Optional[dict],
    operator_leg: Optional[dict] = None,
) -> DialpadCall:
    """Map a /call or /call/{id} JSON object into our DialpadCall dataclass.

    `operator_leg` is the optional /call/{operator_call_id} payload. When the
    main call's `target` is an office queue, the actual user who answered lives
    on the operator leg — pass it in so we can resolve the human's name.
    """
    started_at = _ms_to_datetime(call_json.get("date_started")) or datetime.now(tz=timezone.utc)
    date_connected = _to_int(call_json.get("date_connected"))
    date_ended = _to_int(call_json.get("date_ended"))

    duration_ms = _to_int(call_json.get("duration")) or 0
    duration_seconds = int(duration_ms / 1000)

    if date_connected and date_ended and date_ended > date_connected:
        connected_seconds = int((date_ended - date_connected) / 1000)
    else:
        connected_seconds = duration_seconds

    raw = {"call": call_json}
    if recap:
        raw["ai_recap"] = recap
    if operator_leg:
        raw["operator_leg"] = operator_leg

    return DialpadCall(
        call_id=str(call_json.get("call_id") or ""),
        external_number=call_json.get("external_number") or "",
        internal_user=_internal_user_name(call_json, operator_leg=operator_leg),
        started_at=started_at,
        duration_seconds=duration_seconds,
        connected_seconds=connected_seconds,
        transcript=call_json.get("transcription_text"),
        recap=_extract_recap_summary(recap) if recap else None,
        action_items=_extract_action_items(recap) if recap else [],
        moments={},  # Reserved for future enrichment from /transcripts/{id}
        raw=raw,
    )
