"""
Approved Call Classification Definitions for C&R Services.

Source of truth: Call_Classification_Definitions_APPROVED.docx
Approved by: Taylor Ligon on 2026-04-09
DO NOT modify these definitions without Taylor's explicit approval.

Structure:
- A call has EITHER a Call Reason OR a Job Type, never both.
- Call Reason: used when the call did NOT result in a booked job.
- Job Type: used when the call DID result in a booked job.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ClassificationOption:
    name: str
    definition: str
    use_when: str
    notes: str
    confirmed: bool


# ---------------------------------------------------------------------------
# SECTION 1: CALL REASONS (No Job Booked)
# Use these when the call does not result in a booked job in ServiceTitan.
# ---------------------------------------------------------------------------

CALL_REASONS: list[ClassificationOption] = [
    ClassificationOption(
        name="Billing Question",
        definition="Customer is calling to make payment, inquire about or dispute a charge, invoice, or payment.",
        use_when="Caller references an invoice, payment, balance, or billing concern.",
        notes="Do not book a job. Route to office staff.",
        confirmed=True,
    ),
    ClassificationOption(
        name="Callback / Recall",
        definition="Return visit requested due to a prior repair or installation issue that may be tech-related.",
        use_when="Within ~45 days of the original job and the customer believes the issue is related to prior work.",
        notes=(
            "This should only be used if the customer does not want us to come back out to fix the issue "
            "because they had another company come out instead and the issue was resolved by that company."
        ),
        confirmed=False,
    ),
    ClassificationOption(
        name="Cancellation",
        definition="Customer is calling to cancel an existing scheduled appointment or job.",
        use_when="Caller explicitly states they want to cancel or reschedule a booked appointment.",
        notes="Update job status in ServiceTitan accordingly.",
        confirmed=True,
    ),
    ClassificationOption(
        name="Contractor Contact",
        definition="Call is from a contractor, builder, or trade professional rather than a residential customer.",
        use_when=(
            "Caller identifies themselves as a contractor, builder, or is inquiring about commercial/construction work. "
            "Or about available job openings."
        ),
        notes=(
            "Route to appropriate team member for commercial or RNC jobs. "
            "Those seeking employment should email resumes to office@crhvacpro.com."
        ),
        confirmed=False,
    ),
    ClassificationOption(
        name="Demand",
        definition="Customer is calling to request an immediate or urgent service visit.",
        use_when="Caller needs same-day or next-day service and the call does not result in a booked job.",
        notes="May include price shoppers and those who want over-the-phone pricing.",
        confirmed=False,
    ),
    ClassificationOption(
        name="Estimate Request -- Duct Cleaning",
        definition="Caller is requesting a quote or estimate specifically for duct cleaning services.",
        use_when="Caller asks about pricing, scheduling, or availability for duct cleaning.",
        notes="May include price shoppers.",
        confirmed=False,
    ),
    ClassificationOption(
        name="Estimate Request -- HVAC",
        definition="Caller is requesting a quote or estimate for HVAC installation, replacement, or repair.",
        use_when="Caller asks about HVAC pricing, system replacement, or new installation.",
        notes="May include price shoppers.",
        confirmed=False,
    ),
    ClassificationOption(
        name="Follow Up Call",
        definition="Outbound or inbound call to follow up on a previous estimate, proposal, or completed job.",
        use_when="Call is a check-in after a visit, estimate, or to confirm customer satisfaction. Or appointment verification.",
        notes="Should reference a prior job or estimate in ServiceTitan.",
        confirmed=False,
    ),
    ClassificationOption(
        name="Maintenance",
        definition="Customer is calling to schedule routine HVAC maintenance or a tune-up.",
        use_when="Caller references a maintenance plan, annual tune-up, or seasonal checkup.",
        notes=(
            "If an appointment IS booked from this call, classify as the HVAC Maintenance Job Type instead. "
            "Use this Call Reason only when the call does not result in a booked job."
        ),
        confirmed=False,
    ),
    ClassificationOption(
        name="Missed Call",
        definition="Call was received but not answered; no voicemail or sufficient information was left.",
        use_when="Call was abandoned or dropped with no customer interaction or message.",
        notes="Attempt callback. Log outcome.",
        confirmed=True,
    ),
    ClassificationOption(
        name="Outside Service Area",
        definition="Caller is located outside C&R's 50-mile service radius around Whitehouse, TX.",
        use_when="Address or location provided is beyond the service boundary.",
        notes="Politely decline and refer to another provider if possible.",
        confirmed=True,
    ),
    ClassificationOption(
        name="Requires Different Service",
        definition="Caller needs a service that C&R does not offer.",
        use_when="Request is outside C&R's service offerings (e.g., plumbing, electrical, roofing).",
        notes="Refer out if possible. Do not book a job.",
        confirmed=True,
    ),
    ClassificationOption(
        name="Supply House Order",
        definition="Call is from or related to a parts or supply order from a vendor or supply house.",
        use_when="Caller is a vendor, supplier, or the call relates to a parts order.",
        notes="Route to operations or purchasing.",
        confirmed=False,
    ),
    ClassificationOption(
        name="Vendor / Marketing",
        definition="Call is from a vendor, salesperson, or marketing representative.",
        use_when="Caller is selling a product or service, or is a business development contact.",
        notes="Route to appropriate leadership if needed.",
        confirmed=True,
    ),
    ClassificationOption(
        name="Wrong Number / Hang Up / Spam",
        definition="Call was a wrong number, immediate hang-up, or identified spam/robocall.",
        use_when="No meaningful customer interaction occurred and call is unrelated to C&R services.",
        notes="No action required.",
        confirmed=True,
    ),
]


# ---------------------------------------------------------------------------
# SECTION 2: JOB TYPES (Job Was Booked)
# Use these when the call results in a scheduled job. The job type serves
# as the classification.
# ---------------------------------------------------------------------------

JOB_TYPES: list[ClassificationOption] = [
    ClassificationOption(
        name="Bathroom Estimate",
        definition="On-site estimate appointment for a bathroom remodel project using BCI Acrylics.",
        use_when="Customer requested a quote for bathroom renovation and an appointment was scheduled.",
        notes="2-hour duration. Requires gate code if applicable.",
        confirmed=True,
    ),
    ClassificationOption(
        name="Bathroom Install",
        definition="Full bathroom remodel installation using BCI Acrylics products.",
        use_when="Estimate was accepted and customer approved the bathroom remodel project.",
        notes=(
            "9-hour duration. High priority. This usually happens from an estimate that sells first. "
            "Process flows through Project Manager first to get equipment requisitioned and team assignments, "
            "then we reach out to customer with 'soonest available' and 'next available' options."
        ),
        confirmed=True,
    ),
    ClassificationOption(
        name="Callback / Recall",
        definition="Return visit due to a prior repair or installation issue that may be tech-related.",
        use_when="Within ~45 days of original job. Customer believes issue is related to prior work.",
        notes="2-hour duration. Distinguish from Warranty Work — this is potential tech error.",
        confirmed=True,
    ),
    ClassificationOption(
        name="Duct Cleaning Estimate",
        definition="On-site estimate appointment for duct cleaning services.",
        use_when="Customer requested pricing for duct cleaning and an appointment was scheduled.",
        notes="Requires 'Job Estimate Status' custom field to be completed.",
        confirmed=False,
    ),
    ClassificationOption(
        name="HVAC Estimate",
        definition="On-site estimate for HVAC repair, replacement, or new system installation.",
        use_when="Customer requested a quote for HVAC work and an appointment was scheduled.",
        notes="2-hour duration. Gate code required if applicable.",
        confirmed=True,
    ),
    ClassificationOption(
        name="HVAC Maintenance",
        definition="Routine preventive maintenance or seasonal tune-up for an HVAC system.",
        use_when="Customer is on a maintenance plan or requesting annual service AND an appointment was scheduled.",
        notes="2-hour duration. Gate code required if applicable.",
        confirmed=False,
    ),
    ClassificationOption(
        name="HVAC No Cool",
        definition="Service call for a system that is running but not producing cool air, or not running at all in cooling mode.",
        use_when="Customer reports no cooling, warm air, or system not functioning in summer/cooling season.",
        notes="2-hour duration. High priority. Gate code required if applicable.",
        confirmed=False,
    ),
    ClassificationOption(
        name="HVAC No Heat",
        definition="Service call for a system that is running but not producing heat, or not running at all in heating mode.",
        use_when="Customer reports no heat, cold air, or system not functioning in winter/heating season.",
        notes="2-hour duration. High priority. Gate code required if applicable.",
        confirmed=False,
    ),
    ClassificationOption(
        name="HVAC Other Issue",
        definition="Service call for an HVAC concern that does not fit No Cool, No Heat, or Maintenance categories.",
        use_when="Customer reports unusual noise, odor, leaking, or another HVAC concern not covered by other types.",
        notes="2-hour duration. Gate code required if applicable.",
        confirmed=False,
    ),
    ClassificationOption(
        name="HVAC Replacement / System Install",
        definition="Full HVAC system replacement or new equipment installation (Daikin or other brands).",
        use_when="Estimate was approved and customer is moving forward with full system replacement or new install.",
        notes="5-hour duration. Requires permit field. Optional field for gate code.",
        confirmed=False,
    ),
    ClassificationOption(
        name="RNC Rough In",
        definition="Initial HVAC installation phase during new construction — ductwork and rough mechanical install.",
        use_when="Builder or contractor has scheduled the first HVAC phase of a new construction project.",
        notes="8-hour duration. Permit status required. Gate code if applicable.",
        confirmed=True,
    ),
    ClassificationOption(
        name="RNC Trim Out",
        definition="Final HVAC installation phase during new construction — equipment, registers, and finish work.",
        use_when="Rough-in is complete and project is ready for final HVAC installation and startup.",
        notes="8-hour duration. Permit status required. Gate code if applicable.",
        confirmed=True,
    ),
    ClassificationOption(
        name="Walk-Through",
        definition="Pre-job or post-job walkthrough to assess scope, review work, or confirm project completion.",
        use_when="Project requires an on-site review before scheduling or after completion.",
        notes="1-hour duration. Permit fields may apply.",
        confirmed=True,
    ),
    ClassificationOption(
        name="Warranty Work",
        definition=(
            "Service visit covered under labor warranty — 2-year labor warranty on new system installs "
            "or lifetime labor warranty program. Not for tech error."
        ),
        use_when="Customer has an active labor warranty and the issue is covered under warranty terms.",
        notes="2-hour duration. Distinguish from Callback/Recall — this is warranty coverage, not a recall for tech error.",
        confirmed=False,
    ),
    ClassificationOption(
        name="Water Filtration Estimate",
        definition="On-site estimate for Halo water purification system installation.",
        use_when="Customer inquired about water filtration and an estimate appointment was scheduled.",
        notes="1-hour duration.",
        confirmed=True,
    ),
]


# Quick lookups by name
CALL_REASONS_BY_NAME = {opt.name: opt for opt in CALL_REASONS}
JOB_TYPES_BY_NAME = {opt.name: opt for opt in JOB_TYPES}

# All valid classification names (for validation of classifier output)
ALL_CALL_REASON_NAMES = [opt.name for opt in CALL_REASONS]
ALL_JOB_TYPE_NAMES = [opt.name for opt in JOB_TYPES]
