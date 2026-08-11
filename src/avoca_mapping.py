"""
Maps Avoca's own call_reason enum to C&R's approved ServiceTitan Call Reasons.

GOVERNANCE (see rules.py header, locked 2026-06-05): the classifier may edit
which Call Reason is written on a call record, but it may NEVER invent, add,
or activate a new Call Reason option in ServiceTitan. Every value on the
right-hand side of AVOCA_CALL_REASON_MAP below is one of the 14 Call Reasons
that already exist in ST's master list (see CALL_REASONS in rules.py).

WHY THIS FILE EXISTS: Avoca's /transcript API endpoint returns 404
"Transcript not available for this call" for the large majority of calls,
even ones with a full transcript visible in Avoca's own dashboard (confirmed
2026-08-10, reported to Avoca support as a platform bug — ticket pending).
But Avoca's call_reason enum field (e.g. "BOOKED_REPAIR",
"UNBOOKED_TIME_CONCERN", "EXCUSED_TELEMARKETING") populates reliably and
already IS Avoca's own classification of the call. So Avoca-handled calls
skip Claude/transcript classification entirely and get mapped directly here
instead — deterministic, no LLM cost, no dependency on the broken endpoint.

Built from an empirical survey of all 563 Avoca calls on record as of
2026-08-10 (see survey_avoca_call_reasons.py / survey_avoca_call_reasons.log).
Bucket decisions confirmed with Taylor 2026-08-10:

  - Real-but-unbooked "demand-shaped" objections (customer wanted service,
    didn't book over timing/price/coordination/trip-charge concerns) ->
    "Demand". Matches rules.py's own Demand definition verbatim: "Customer
    wanted urgent service or repair pricing but didn't book." Avoca already
    flags these is_bookable=True, so this also keeps them correctly counted
    as bookable opportunities in the EGIA booking rate.
  - Ambiguous "customer wants a human, no discernible reason why" calls ->
    "Avoca" (the existing ST placeholder reason). Left for Julie to review
    manually rather than guess — same as the original pre-mapping design.
  - Non-customer noise (test calls, internal calls, job applicants, DNC
    compliance) -> "Wrong Number / Hang Up / Spam". Not a real opportunity.
  - Membership / maintenance-plan inquiries -> "Maintenance".

Any Avoca call_reason NOT in this map (a brand-new enum value Avoca
introduces later) falls back to "Avoca" — never guess, never invent.

If Taylor needs to change any of these mappings, or Avoca introduces a new
call_reason value that shows up as "(unmapped -> Avoca)" in a run's log,
that's a signal to come back and extend this table — always ask before
guessing at a new bucket, per Taylor's instruction 2026-08-10.
"""

from __future__ import annotations

# Booked calls (BOOKED_REPAIR, BOOKED_INSTALL_SALES, BOOKED_MAINTENANCE,
# BOOKED_SERVICE) are intentionally NOT in this map. They never need a Call
# Reason because ServiceTitan already has the real Job Type from the booking
# itself — the pipeline skips classification entirely for booked Avoca calls
# (see pipeline.py Step 3c: `if st_call.job_id: continue`).

AVOCA_CALL_REASON_MAP: dict[str, str] = {
    # --- Not a real opportunity / hang-up-equivalent ---
    "EXCUSED_SILENT_CALL": "Wrong Number / Hang Up / Spam",
    "EXCUSED_NO_REASON": "Wrong Number / Hang Up / Spam",
    "EXCUSED_SPAM": "Wrong Number / Hang Up / Spam",
    "EXCUSED_TEST_CALL": "Wrong Number / Hang Up / Spam",
    "EXCUSED_INTERNAL_CALL": "Wrong Number / Hang Up / Spam",
    "EXCUSED_EMPLOYMENT": "Wrong Number / Hang Up / Spam",
    "EXCUSED_NOT_QUALIFIED_CALLER": "Wrong Number / Hang Up / Spam",
    "OUTBOUND_DO_NOT_CALL": "Wrong Number / Hang Up / Spam",

    # --- Marketing / solicitation ---
    "EXCUSED_TELEMARKETING": "Vendor / Marketing",

    # --- Existing customer, real conversation, no new opportunity ---
    "EXCUSED_RETURNING_CALL": "Follow Up Call",
    "FOLLOW_UP_ETA": "Follow Up Call",
    "FOLLOW_UP_OTHER_UPDATE": "Follow Up Call",
    "FOLLOW_UP_CONFIRMING_TIME": "Follow Up Call",
    "FOLLOW_UP_RESCHEDULE": "Follow Up Call",
    "FOLLOW_UP_COMPLIMENT": "Follow Up Call",
    "FOLLOW_UP_COMPLAINT": "Follow Up Call",  # confirmed with Taylor 2026-08-11
    "EXCUSED_UPDATE_PROFILE": "Follow Up Call",
    "UNBOOKED_CALLBACK_PREVIOUS_JOB": "Follow Up Call",
    "OUTBOUND_NOT_AVAILABLE": "Follow Up Call",  # Avoca's own outbound reminder call, not answered

    # --- Specific existing-customer admin requests (a more precise reason exists) ---
    "FOLLOW_UP_CANCEL": "Cancellation",
    "FOLLOW_UP_INVOICE": "Billing Question",

    # --- Out of scope for C&R ---
    "EXCUSED_OUTSIDE_OF_SERVICES": "Requires Different Service",
    "EXCUSED_OUTSIDE_OF_AREA": "Outside Service Area",
    "UNBOOKED_COMMERCIAL": "Requires Different Service",

    # --- Real bookable opportunity, didn't book (Taylor confirmed 2026-08-10) ---
    "UNBOOKED_TIME_CONCERN": "Demand",
    "UNBOOKED_PRICE_CONCERN": "Demand",
    "UNBOOKED_CALL_BACK_LATER": "Demand",
    "UNBOOKED_PENDING_COORDINATION": "Demand",
    "UNBOOKED_TRIP_CHARGE": "Demand",

    # --- Maintenance-plan inquiry (Taylor confirmed 2026-08-10) ---
    "EXCUSED_MEMBERSHIP_INQUIRY": "Maintenance",

    # --- Ambiguous "wants a human", real conversation, no clear reason.
    # Taylor confirmed 2026-08-10: leave for Julie to review manually. ---
    "EXCUSED_TRANSFER_TO_SPECIFIC_PERSON": "Avoca",
    "EXCUSED_OTHER_QUESTIONS": "Avoca",
    "EXCUSED_LIVE_REPRESENTATIVE_TRANSFER": "Avoca",
    "UNBOOKED_REJECT_AGENT": "Avoca",
    "EXCUSED_INSTALLATION_CALL": "Avoca",
}

# Fallback for any Avoca call_reason value not in the map above — a brand
# new enum value Avoca introduces later, or a call with no Avoca match at
# all. Always "Avoca" (the existing ST placeholder), never a guess.
AVOCA_UNMAPPED_FALLBACK = "Avoca"


def map_avoca_call_reason(avoca_reason: str | None) -> str:
    """Translate an Avoca call_reason enum value to an approved ST Call Reason.

    Never returns anything outside ST's existing approved rulebook — unmapped
    or missing values fall back to "Avoca" (Julie's manual-review placeholder),
    consistent with the 2026-06-05 governance rule (classifier may edit, never
    invent, Call Reasons).
    """
    if not avoca_reason:
        return AVOCA_UNMAPPED_FALLBACK
    return AVOCA_CALL_REASON_MAP.get(avoca_reason.strip().upper(), AVOCA_UNMAPPED_FALLBACK)
