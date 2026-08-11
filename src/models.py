"""Data models for the call classification pipeline."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class ServiceTitanCall:
    """A call record pulled from the ServiceTitan Telecom v2 API.

    The API returns a job-level wrapper with the actual call nested inside
    as `leadCall`. This dataclass flattens both levels into a single record.
    """

    call_id: str                    # leadCall.id — ServiceTitan call ID
    caller_phone: str               # leadCall.from — for inbound: customer; for outbound: agent's line
    callee_phone: str               # leadCall.to   — for outbound: customer; for inbound: agent's line
    direction: str                  # leadCall.direction — "Inbound" / "Outbound"
    received_at: datetime           # leadCall.receivedOn — ISO timestamp
    duration_seconds: int           # leadCall.duration — parsed from "HH:MM:SS" string
    call_type: str                  # leadCall.callType — "Booked", "Excused", "Abandoned", etc.
    customer_name: Optional[str]    # leadCall.customer.name, else None
    recording_url: Optional[str]    # leadCall.recordingUrl
    agent_name: Optional[str]       # leadCall.agent.name — CSR, e.g. "Templin, Julie"
    reason_name: Optional[str]      # leadCall.reason.name — existing classification if any
    reason_id: Optional[int]        # leadCall.reason.id — for write-back reference
    job_id: Optional[int]           # top-level id — 0 or null means unbooked
    job_number: Optional[str]       # top-level jobNumber — null if unbooked
    job_type_name: Optional[str]    # top-level type.name — e.g. "HVAC Maintenance"
    job_type_id: Optional[int]      # top-level type.id
    business_unit: Optional[str]    # top-level businessUnit.name
    campaign_name: Optional[str]    # leadCall.campaign.name — e.g. "Existing Customer"
    twilio_sid: Optional[str]       # leadCall.sid — Twilio call SID (potential linking aid)
    raw: dict = field(default_factory=dict)  # Original JSON for debugging


@dataclass
class DialpadCall:
    """A call record from Dialpad with transcript and AI features."""

    call_id: str                    # Dialpad internal call ID
    external_number: str            # The customer's number
    internal_user: str              # The C&R employee who handled it (e.g., "Julie Templin")
    started_at: datetime
    duration_seconds: int           # Total call duration
    connected_seconds: int          # Time actually connected (excludes ring time)
    transcript: Optional[str]       # Full text transcript with speaker labels
    recap: Optional[str]            # AI-generated summary paragraph
    action_items: list[str]         # AI-extracted action items
    moments: dict                   # AI-tagged moments (Action Item, Call Purpose, Time, etc.)
    raw: dict = field(default_factory=dict)


@dataclass
class AvocaCall:
    """A call record from Avoca (AI virtual receptionist) with transcript.

    Deliberately mirrors DialpadCall's field shape (call_id, external_number,
    internal_user, started_at, duration_seconds, connected_seconds, transcript,
    recap, action_items, moments, raw) so it's a drop-in for linker.link_batch()
    and Classifier.classify(), both of which duck-type on these attributes
    rather than checking isinstance. See src/avoca.py.
    """

    call_id: str                    # Avoca call UUID
    external_number: str            # The customer's number
    internal_user: str              # "Avoca AI" — Avoca doesn't expose which human (if any) it warm-transferred to
    started_at: datetime
    duration_seconds: int
    connected_seconds: int          # Avoca's AI engages the instant it picks up, so this equals duration_seconds
    transcript: Optional[str]       # Flattened "Avoca AI: ... / Customer: ..." text (fetched separately per call)
    recap: Optional[str]            # ai_summary if the team's column allowlist exposes it, else synthesized from call_reason + call_outcome + booking_result
    action_items: list[str]         # Always [] — not exposed by the Avoca API
    moments: dict                   # Always {} — kept for interface parity with DialpadCall
    service_titan_job_id: Optional[int] = None  # If the team exposes service_titan_job_id, this is an exact-match linking key — see linker.link_avoca_by_job_id
    raw: dict = field(default_factory=dict)


@dataclass
class LinkedCall:
    """A ServiceTitan call joined to its corresponding Dialpad (or Avoca) call."""

    servicetitan: ServiceTitanCall
    dialpad: Optional[DialpadCall]   # None if no match found. May hold an AvocaCall for Avoca-handled ST calls — same duck-typed interface.
    match_confidence: float          # 0.0 to 1.0 — how confident the linker is
    match_method: str                # "phone+timestamp_exact", "phone+timestamp_window", "no_match", etc.


@dataclass
class Classification:
    """The output of the classifier for a single call."""

    call_id: str                              # Matches ServiceTitanCall.call_id
    classification_type: str                  # "call_reason" or "job_type"
    classification_value: str                 # e.g. "HVAC Maintenance" or "Maintenance"
    confidence: float                         # 0.0 to 1.0
    should_have_been_booked: bool             # True if this looks like a missed booking opportunity
    booking_recommendation: Optional[str]     # If should_have_been_booked, the suggested Job Type
    reasoning: str                            # 1-3 sentence explanation
    classified_at: datetime
    classifier_version: str                   # e.g. "v1.0-claude-haiku-4.5"
    raw_llm_response: Optional[str] = None    # Full response for debugging


@dataclass
class JobTypeMismatch:
    """A booked call where the classifier disagrees with the assigned Job Type.

    Collected during each pipeline run and included in the weekly report so
    Taylor can spot-check and correct wrong job types in ServiceTitan.
    """

    call_id: str                  # ServiceTitan lead call ID
    job_number: Optional[str]     # ST job number — easy to look up in ST
    caller_phone: str             # Customer phone number
    customer_name: Optional[str]  # Customer name from ST (if known)
    received_at: datetime         # When the call came in
    actual_job_type: str          # Job type currently set on the ST booking
    predicted_job_type: str       # What the classifier thinks it should be
    confidence: float             # Classifier confidence (only flagged at ≥ 0.7)
    reasoning: str                # Classifier's explanation
