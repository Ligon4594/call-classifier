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
    caller_phone: str               # leadCall.from — 10-digit string, e.g. "9032451470"
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
class LinkedCall:
    """A ServiceTitan call joined to its corresponding Dialpad call."""

    servicetitan: ServiceTitanCall
    dialpad: Optional[DialpadCall]   # None if no match found
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
