"""
ServiceTitan API client.

STATUS (2026-04-10): LIVE. Wired against the ServiceTitan v2 REST API.

Auth: OAuth2 client credentials flow.
  1. POST https://auth.servicetitan.io/connect/token
     Body: grant_type=client_credentials&client_id=...&client_secret=...
     → access_token (15-minute TTL, no refresh token)
  2. All API calls include:
     - Authorization: Bearer {access_token}
     - ST-App-Key: {app_key}
  3. Tenant ID goes in the URL path: /telecom/v2/tenant/{tenant_id}/...

Endpoints used:
  GET  /telecom/v2/tenant/{tid}/calls           — paginated call list  (rate: 60/sec)
  GET  /telecom/v2/tenant/{tid}/calls/{id}      — single call detail
  PATCH/PUT /telecom/v2/tenant/{tid}/calls/{id}  — update call reason  (TBD exact method)
  GET  /crm/v2/tenant/{tid}/customers/{id}      — customer lookup
  GET  /jpm/v2/tenant/{tid}/jobs                — jobs list

Reference: https://developer.servicetitan.io/
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from typing import Any, Optional

from .models import ServiceTitanCall


SERVICETITAN_API_BASE = "https://api.servicetitan.io"
SERVICETITAN_AUTH_URL = "https://auth.servicetitan.io/connect/token"


class ServiceTitanAPIError(Exception):
    """Raised when a ServiceTitan API call fails."""

    def __init__(self, message: str, status_code: Optional[int] = None, response_body: Optional[str] = None):
        self.status_code = status_code
        self.response_body = response_body
        super().__init__(message)


class ServiceTitanClient:
    """Client for the ServiceTitan v2 API.

    Auth is OAuth2 client credentials. The token lasts 15 minutes and is
    cached/reused automatically to avoid throttling.
    """

    def __init__(
        self,
        *,
        tenant_id: Optional[str] = None,
        app_key: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
    ):
        self.tenant_id = tenant_id or os.environ.get("SERVICETITAN_TENANT_ID", "")
        self.app_key = app_key or os.environ.get("SERVICETITAN_APP_KEY", "")
        self.client_id = client_id or os.environ.get("SERVICETITAN_CLIENT_ID", "")
        self.client_secret = client_secret or os.environ.get("SERVICETITAN_CLIENT_SECRET", "")
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0  # epoch seconds

        missing = []
        if not self.tenant_id:
            missing.append("SERVICETITAN_TENANT_ID")
        if not self.app_key:
            missing.append("SERVICETITAN_APP_KEY")
        if not self.client_id:
            missing.append("SERVICETITAN_CLIENT_ID")
        if not self.client_secret:
            missing.append("SERVICETITAN_CLIENT_SECRET")
        if missing:
            # Don't blow up on construction — let individual API calls fail
            # so dry-run / test paths still work.
            import sys
            print(f"[ServiceTitanClient] WARNING: missing env vars: {', '.join(missing)}", file=sys.stderr)

    # ------------------------------------------------------------------
    # AUTH
    # ------------------------------------------------------------------

    def _ensure_token(self) -> str:
        """Get a valid access token, refreshing if expired or missing.

        ServiceTitan tokens last 900 seconds (15 min). We refresh with
        a 60-second safety margin to avoid mid-request expiry.
        """
        if self._access_token and time.time() < (self._token_expires_at - 60):
            return self._access_token

        body = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }).encode("utf-8")

        req = urllib.request.Request(
            SERVICETITAN_AUTH_URL,
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "CRCallClassifier/1.0",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise ServiceTitanAPIError(
                f"ServiceTitan auth failed (HTTP {exc.code}): {error_body}",
                status_code=exc.code,
                response_body=error_body,
            ) from exc
        except urllib.error.URLError as exc:
            raise ServiceTitanAPIError(f"ServiceTitan auth connection error: {exc}") from exc

        self._access_token = data["access_token"]
        expires_in = int(data.get("expires_in", 900))
        self._token_expires_at = time.time() + expires_in
        return self._access_token

    # ------------------------------------------------------------------
    # LOW-LEVEL HTTP
    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json_body: Optional[dict] = None,
    ) -> dict:
        """Make an authenticated API request.

        Returns the parsed JSON response as a dict.
        """
        token = self._ensure_token()

        url = f"{SERVICETITAN_API_BASE}{path}"
        if params:
            qs = urllib.parse.urlencode(
                {k: v for k, v in params.items() if v is not None},
                doseq=True,
            )
            url = f"{url}?{qs}"

        headers = {
            "Authorization": f"Bearer {token}",
            "ST-App-Key": self.app_key,
            "Accept": "application/json",
            "User-Agent": "CRCallClassifier/1.0",
        }

        body_bytes: Optional[bytes] = None
        if json_body is not None:
            body_bytes = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                if not raw:
                    return {}
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise ServiceTitanAPIError(
                f"ServiceTitan API error on {method} {path} (HTTP {exc.code}): {error_body}",
                status_code=exc.code,
                response_body=error_body,
            ) from exc
        except urllib.error.URLError as exc:
            raise ServiceTitanAPIError(
                f"ServiceTitan API connection error on {method} {path}: {exc}"
            ) from exc

    def _get(self, path: str, *, params: Optional[dict[str, Any]] = None) -> dict:
        return self._request("GET", path, params=params)

    def _patch(self, path: str, *, json_body: dict) -> dict:
        return self._request("PATCH", path, json_body=json_body)

    def _put(self, path: str, *, json_body: dict) -> dict:
        return self._request("PUT", path, json_body=json_body)

    # ------------------------------------------------------------------
    # PUBLIC INTERFACE — pipeline depends on these signatures.
    # ------------------------------------------------------------------

    def get_calls(
        self,
        *,
        start_date: date,
        end_date: date,
        page: int = 1,
        page_size: int = 200,
    ) -> tuple[list[ServiceTitanCall], bool]:
        """Pull a page of calls from the ServiceTitan Telecom API.

        Returns (calls, has_more). Caller should loop incrementing `page`
        until has_more is False.

        Date params use ISO format: "2026-04-01".
        """
        path = f"/telecom/v2/tenant/{self.tenant_id}/calls"
        params: dict[str, Any] = {
            "page": page,
            "pageSize": page_size,
            "createdOnOrAfter": start_date.isoformat(),
            "createdBefore": end_date.isoformat(),
        }

        data = self._get(path, params=params)
        items = data.get("data") or []
        has_more = data.get("hasMore", False)

        calls = [_build_st_call(item) for item in items]
        return calls, has_more

    def get_all_calls(
        self,
        *,
        start_date: date,
        end_date: date,
        max_pages: int = 50,
    ) -> list[ServiceTitanCall]:
        """Pull ALL calls in a date range, handling pagination automatically.

        Walks up to `max_pages` to avoid runaway pulls.
        """
        all_calls: list[ServiceTitanCall] = []
        page = 1
        while page <= max_pages:
            calls, has_more = self.get_calls(
                start_date=start_date,
                end_date=end_date,
                page=page,
            )
            all_calls.extend(calls)
            if not has_more:
                break
            page += 1
        return all_calls

    def get_call(self, call_id: str) -> ServiceTitanCall:
        """Fetch a single call by its ServiceTitan call ID."""
        path = f"/telecom/v2/tenant/{self.tenant_id}/calls/{call_id}"
        data = self._get(path)
        return _build_st_call(data)

    def write_classification(
        self,
        *,
        call_id: str,
        call_reason_id: Optional[int] = None,
        memo: Optional[str] = None,
    ) -> dict:
        """Write the classification back to the ServiceTitan call record.

        Two strategies (we'll use whichever works against the live API):
          1. Update the call's `reason` field (requires a call_reason_id lookup)
          2. Add a note/memo to the call record with the classification details

        For now this sends a PATCH to the call endpoint. If the API uses PUT
        instead, we'll switch on first live test.
        """
        path = f"/telecom/v2/tenant/{self.tenant_id}/calls/{call_id}"
        body: dict[str, Any] = {}
        if call_reason_id is not None:
            body["reasonId"] = call_reason_id
        if memo is not None:
            body["memo"] = memo

        if not body:
            raise ValueError("write_classification requires at least call_reason_id or memo")

        return self._patch(path, json_body=body)

    def get_call_reasons(self) -> list[dict]:
        """Fetch available call reasons/types configured in ServiceTitan.

        This lets us map our classification_value (e.g. "HVAC Maintenance")
        to the correct ServiceTitan reason ID for the write-back.
        """
        path = f"/telecom/v2/tenant/{self.tenant_id}/calls/reasons"
        data = self._get(path)
        return data.get("data") or (data if isinstance(data, list) else [])

    def get_customer(self, customer_id: str) -> dict:
        """Fetch customer details for context enrichment."""
        path = f"/crm/v2/tenant/{self.tenant_id}/customers/{customer_id}"
        return self._get(path)

    # ------------------------------------------------------------------
    # DIAGNOSTIC / EXPLORATION
    # ------------------------------------------------------------------

    def test_connection(self) -> dict:
        """Quick connectivity test: authenticate and pull page 1 of calls.

        Returns a summary dict with token info + first page stats.
        """
        token = self._ensure_token()

        # Try pulling 1 call just to prove the full round-trip works.
        path = f"/telecom/v2/tenant/{self.tenant_id}/calls"
        data = self._get(path, params={"page": 1, "pageSize": 1})

        return {
            "auth": "OK",
            "token_prefix": token[:20] + "...",
            "tenant_id": self.tenant_id,
            "total_calls": data.get("totalCount"),
            "has_data": bool(data.get("data")),
            "first_call_id": data["data"][0].get("id") if data.get("data") else None,
            "raw_keys": list(data.keys()),
        }


# ------------------------------------------------------------------
# HELPERS — pure functions, no API calls
# ------------------------------------------------------------------

def _normalize_phone(phone: str) -> str:
    """Strip to digits, keep last 10 (US numbers)."""
    digits = re.sub(r"\D", "", phone or "")
    return digits[-10:] if len(digits) >= 10 else digits


def _parse_st_datetime(value: Any) -> Optional[datetime]:
    """Parse a ServiceTitan ISO 8601 datetime string to a tz-aware datetime.

    ServiceTitan returns dates like "2025-05-17T13:33:12.2548779Z" — note
    the 7-digit fractional seconds, which Python's %f (max 6 digits) can't
    handle. We truncate to 6 digits before parsing.
    """
    if not value:
        return None
    if isinstance(value, datetime):
        return value

    s = str(value).strip()

    # Truncate fractional seconds to 6 digits (Python %f limit).
    # Match: "2025-05-17T13:33:12.2548779Z" -> "2025-05-17T13:33:12.254877Z"
    s = re.sub(r"(\.\d{6})\d+(Z|[+-])", r"\1\2", s)

    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def _parse_duration_hms(value: Any) -> int:
    """Parse a ServiceTitan duration string like "00:01:06" into seconds.

    ServiceTitan returns duration as "HH:MM:SS", NOT as an integer.
    Falls back to 0 if unparseable.
    """
    if not value:
        return 0
    s = str(value).strip()
    # Try HH:MM:SS format
    parts = s.split(":")
    if len(parts) == 3:
        try:
            h, m, sec = int(parts[0]), int(parts[1]), int(parts[2])
            return h * 3600 + m * 60 + sec
        except ValueError:
            pass
    # Maybe it's already an int (future API versions?)
    try:
        return int(s)
    except (ValueError, TypeError):
        return 0


def _build_st_call(item_json: dict) -> ServiceTitanCall:
    """Map a ServiceTitan /telecom/v2/calls item into our ServiceTitanCall dataclass.

    The API returns a JOB-level wrapper with the actual call nested inside
    as `leadCall`. Structure:
      {
        "id": 34585884,           # Job ID (0 if unbooked)
        "jobNumber": "009287",    # null if unbooked
        "businessUnit": {...},
        "type": {"name": "HVAC Maintenance", ...},  # Job type
        "leadCall": {
          "id": 27439621,         # Call ID
          "from": "9032161247",   # Caller phone (10-digit)
          "direction": "Inbound",
          "receivedOn": "2025-05-17T13:33:12Z",
          "duration": "00:01:06", # HH:MM:SS string
          "callType": "Excused",  # "Booked", "Excused", "Abandoned", etc.
          "reason": {"id": 70, "name": "Wrong Number/Hang Up/Spam"},
          "customer": {"name": "Eva Dacus", ...},
          "agent": {"name": "Templin, Julie"},
          "recordingUrl": "https://...",
          "sid": "CA9af50e41...",  # Twilio SID
          ...
        }
      }
    """
    # The call data lives inside leadCall
    call = item_json.get("leadCall") or {}

    # Call ID
    call_id = str(call.get("id") or "")

    # Phone (10-digit string like "9032161247")
    caller_phone = call.get("from") or ""

    # Direction
    direction = call.get("direction") or ""

    # Timestamp
    received_at = (
        _parse_st_datetime(call.get("receivedOn"))
        or _parse_st_datetime(call.get("createdOn"))
        or datetime.now(tz=timezone.utc)
    )

    # Duration — "HH:MM:SS" string
    duration_seconds = _parse_duration_hms(call.get("duration"))

    # Call type label
    call_type = call.get("callType") or ""

    # Customer name (nested object, can be null)
    customer = call.get("customer") or {}
    customer_name = customer.get("name") if isinstance(customer, dict) else None

    # Recording URL
    recording_url = call.get("recordingUrl") or None

    # Agent / CSR — "Last, First" format
    agent = call.get("agent") or {}
    agent_name = agent.get("name") if isinstance(agent, dict) else None

    # Existing reason/classification (can be null)
    reason = call.get("reason") or {}
    if isinstance(reason, dict):
        reason_name = reason.get("name") or None
        reason_id = reason.get("id")
    else:
        reason_name = None
        reason_id = None

    # Campaign
    campaign = call.get("campaign") or {}
    campaign_name = campaign.get("name") if isinstance(campaign, dict) else None

    # Twilio SID
    twilio_sid = call.get("sid") or None

    # Job-level fields (outer wrapper)
    job_id_raw = item_json.get("id") or 0
    job_id = int(job_id_raw) if job_id_raw else None
    if job_id == 0:
        job_id = None  # 0 means unbooked

    job_number = item_json.get("jobNumber") or None

    job_type = item_json.get("type") or {}
    if isinstance(job_type, dict):
        job_type_name = job_type.get("name") or None
        job_type_id = job_type.get("id")
    else:
        job_type_name = None
        job_type_id = None

    bu = item_json.get("businessUnit") or {}
    business_unit = bu.get("name") if isinstance(bu, dict) else None

    return ServiceTitanCall(
        call_id=call_id,
        caller_phone=caller_phone,
        direction=direction,
        received_at=received_at,
        duration_seconds=duration_seconds,
        call_type=call_type,
        customer_name=customer_name,
        recording_url=recording_url,
        agent_name=agent_name,
        reason_name=reason_name,
        reason_id=reason_id,
        job_id=job_id,
        job_number=job_number,
        job_type_name=job_type_name,
        job_type_id=job_type_id,
        business_unit=business_unit,
        campaign_name=campaign_name,
        twilio_sid=twilio_sid,
        raw=item_json,
    )
