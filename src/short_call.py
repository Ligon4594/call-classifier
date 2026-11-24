"""
Short-call rule (Taylor, 2026-11-24, verbatim excerpts):

  "we have a bunch of abandoned calls that have been classified by the call
   classifier and are shown as missed call, but the call length was 0
   seconds. This means it never rang on our side, so it would fall into
   wrong number, hang-up, or spam call ... it probably doesn't need to fall
   into the call booking rate ... If there's any call that's less than about
   10 seconds in call length, it could be classified as wrong number,
   hang-up, or spam."

Rule: an INBOUND, UNBOOKED call whose length is under SHORT_CALL_SECONDS gets
the EXISTING ServiceTitan Call Reason "Wrong Number / Hang Up / Spam" (a
Disqualified reason, Is Lead = false), which keeps it out of every
booking-rate denominator. Deterministic, no Claude call.

Guard rails (why it is not just `st.duration < 10`):
  * ST's duration is NOT trustworthy on its own (handoff trap #1): ST stamps
    00:00:00 on any call where the CSR didn't click Accept, including real
    booked conversations. So when a Dialpad or Avoca record of the same call
    exists, that record must ALSO be short and carry no transcript/recap.
    A call ST shows as 0s but Dialpad shows as a 3-minute conversation is
    left to the normal classifier path.
  * A call Dialpad shows ringing >= 10s unanswered is a real missed call
    (governance #4) and stays on the normal path.
  * Never on a booked call (job_id or callType Booked) - governance #6.
  * Never over a human's manual fix: only calls whose current reason is
    blank, "Missed Call", or the "Avoca" placeholder are eligible (the same
    set the skip filter re-processes). Enforced here too so --no-skip can't
    bypass it.
"""

from __future__ import annotations

from typing import Iterable, Optional

SHORT_CALL_SECONDS = 10
SHORT_CALL_REASON = "Wrong Number / Hang Up / Spam"
SHORT_CALL_VERSION = "short-call-rule-v1"

# Current reasons the rule may overwrite. Anything else may be a CSR's or
# Julie Templin's manual fix (ST exposes no modifiedBy on calls), so it is
# protected.
OVERWRITABLE_REASONS = {"", "missed call", "avoca"}


def _has_content(rec) -> bool:
    return bool(getattr(rec, "transcript", None) or getattr(rec, "recap", None))


def is_overwritable(st_call) -> bool:
    return (getattr(st_call, "reason_name", None) or "").strip().lower() in OVERWRITABLE_REASONS


def short_call_verdict(st_call, evidence: Iterable[Optional[object]] = ()) -> tuple[bool, str]:
    """Return (is_short, why). `evidence` = linked Dialpad/Avoca records (None ok)."""
    if st_call is None:
        return False, "no call"
    if (st_call.direction or "").strip().lower() != "inbound":
        return False, "not inbound"
    if st_call.job_id or (st_call.call_type or "").strip().lower() == "booked":
        return False, "booked"
    if st_call.duration_seconds >= SHORT_CALL_SECONDS:
        return False, f"ST duration {st_call.duration_seconds}s"
    for rec in evidence:
        if rec is None:
            continue
        if _has_content(rec):
            return False, "linked record has transcript/recap"
        if int(getattr(rec, "duration_seconds", 0) or 0) >= SHORT_CALL_SECONDS:
            return False, f"linked record duration {getattr(rec, 'duration_seconds', 0)}s"
        if int(getattr(rec, "connected_seconds", 0) or 0) >= SHORT_CALL_SECONDS:
            return False, f"linked record connected {getattr(rec, 'connected_seconds', 0)}s"
    return True, f"inbound, unbooked, ST {st_call.duration_seconds}s, no conversation on any linked record"
