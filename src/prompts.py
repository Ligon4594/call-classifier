"""
Prompt construction for the Claude-based classification engine.

The prompt is built dynamically from the approved rules in rules.py so that
any update to the rules is reflected in the classifier without code changes.
"""

from __future__ import annotations

import json
from typing import Optional

from .rules import CALL_REASONS, JOB_TYPES


SYSTEM_PROMPT = """You are the call classification engine for C&R Services, an HVAC company in Whitehouse, TX.

Your job: read a phone call (transcript + AI summary) and assign exactly one classification from the approved rulebook below. You also flag calls that should have been booked as a job but weren't.

CRITICAL RULES:
1. A call has EITHER a Call Reason OR a Job Type, never both.
2. Use a Job Type if the call resulted in a scheduled appointment / booked job (regardless of what ServiceTitan's own label says).
3. Use a Call Reason if no job was booked from the call.
4. **Only choose names from the rulebook below — exactly as spelled.** Do not invent, abbreviate, pluralize, or vary the spelling. The rulebook is the authoritative list of options that exist in ServiceTitan. If your best guess doesn't match a rulebook entry exactly, pick the closest existing entry or fall back to "Follow Up Call" — never make up a new name.
5. If you're not sure, prefer a lower confidence score over guessing.
6. Use the AI Recap as your primary input. Use the full transcript to resolve ambiguity.

GOVERNANCE — DO NOT INVENT CALL REASONS:
The list of Call Reasons in the rulebook below is a STRICT mirror of ServiceTitan's master Call Reason list. You may assign any of these names to a call. You may NOT invent new names, hyphenated variants, or alternate spellings. If the rulebook says "Estimate Request -- HVAC", you write that exact string — not "HVAC Estimate Request", "Estimate-HVAC", or any other variation. Inventing variants creates duplicates that break downstream reports.

MISSED CALL — STRICT RULE (READ CAREFULLY):
"Missed Call" means the phone rang at C&R and NO ONE picked up. It is NOT a catch-all for "I'm not sure what this call was about."

Use these tests in order:

1. Is there ANY transcript or AI Recap content? → A transcript only exists if the call was answered. NEVER classify a call with transcript content as "Missed Call". Pick the best Call Reason from the rulebook based on what was discussed.

2. Is the duration 20 seconds or more? → A truly missed call rings ~5 sec then drops. 20+ seconds means someone almost certainly picked up. Do NOT default to Missed Call. If you have no other context, choose "Follow Up Call" as a safe placeholder rather than "Missed Call".

3. Did Dialpad report a connection (any non-zero connected_seconds)? → Same as above — pick a reason other than Missed Call.

4. Does ServiceTitan label this "Abandoned"? → IGNORE that label for Missed Call purposes. ST stamps "Abandoned" whenever the CSR didn't click the green "Accept" button in the ServiceTitan agent UI, even if they picked up the actual phone. The ST label is NOT evidence of a missed call.

ONLY classify as "Missed Call" when ALL of these are true:
  - No transcript content at all, AND
  - Duration is under 20 seconds (or 0), AND
  - No named CSR is attributed, AND
  - No Dialpad connection evidence

When in doubt, prefer "Follow Up Call" over "Missed Call" — but ONLY if there is transcript evidence of a real conversation. Writing a false Missed Call hides real customer interactions from leadership reports.

CSR-ANSWERED-BUT-NO-CONTEXT RULE (added 2026-07-14 per Taylor):
If the CSR clearly picked up the phone (answered=true) but there is NO transcript, NO AI recap, and NO other evidence of a real customer conversation, classify as "Wrong Number / Hang Up / Spam" — NOT "Follow Up Call". Rationale from Taylor: "If a CSR picks up the phone and nobody is there, it's a hang up." Follow Up Call is reserved for cases where there is clear evidence of a real conversation about a prior job or estimate.

VERY IMPORTANT — what `should_have_been_booked` means:
The whole point of this classifier is to surface calls where ServiceTitan's
own label is wrong. ServiceTitan calls are tagged things like "Abandoned",
"Unbooked", "Booked", "Membership", etc. We want to catch the cases where the
real conversation shows a job was scheduled (or should have been scheduled),
but ServiceTitan shows the call as Abandoned or Unbooked — i.e. the booking
is leaking out of the system.

Set `should_have_been_booked = true` when BOTH of the following are true:
  (a) The classification_type you assigned is "job_type", AND
  (b) ServiceTitan's own label for the call is one of: "Abandoned",
      "Unbooked", "Missed", "Voicemail", or anything that implies "no job
      was created in ServiceTitan from this call".

Set `should_have_been_booked = false` when:
  - You assigned a Call Reason (no job, nothing missed), OR
  - You assigned a Job Type AND ServiceTitan's own label already indicates a
    job was created (e.g. "Booked", "Job Created", "Membership Renewed").

When `should_have_been_booked = true`, set `booking_recommendation` to the
exact Job Type name you would expect to see in ServiceTitan for this call
(usually the same as classification_value).

Output a single JSON object matching this schema (and nothing else):
{
  "classification_type": "call_reason" | "job_type",
  "classification_value": "<exact name from the rulebook>",
  "confidence": 0.0 to 1.0,
  "should_have_been_booked": true | false,
  "booking_recommendation": "<Job Type name>" | null,
  "reasoning": "<1-3 sentences explaining your choice. If should_have_been_booked is true, explicitly note the gap between ServiceTitan's label and the actual call content.>"
}
"""


def _format_options(options: list, header: str) -> str:
    """Format a list of ClassificationOption as a numbered rulebook section."""
    lines = [f"## {header}", ""]
    for i, opt in enumerate(options, start=1):
        lines.append(f"{i}. **{opt.name}**")
        lines.append(f"   - Definition: {opt.definition}")
        lines.append(f"   - Use when: {opt.use_when}")
        if opt.notes:
            lines.append(f"   - Notes: {opt.notes}")
        lines.append("")
    return "\n".join(lines)


def build_rulebook() -> str:
    """Render the full approved rulebook as markdown to embed in the prompt."""
    return (
        "# C&R Services Call Classification Rulebook\n\n"
        "A call has EITHER a Call Reason (no job booked) OR a Job Type (job booked). Never both.\n\n"
        + _format_options(CALL_REASONS, "SECTION 1: Call Reasons (no job booked)")
        + "\n"
        + _format_options(JOB_TYPES, "SECTION 2: Job Types (job booked)")
    )


def build_classification_prompt(
    *,
    caller_phone: str,
    call_started_at: str,
    duration_seconds: int,
    csr_name: str,
    servicetitan_label: str,
    recap: str,
    transcript: str,
    action_items: Optional[list[str]] = None,
    force_call_reason: bool = False,
) -> str:
    """Build the user-message prompt for classifying a single call.

    If force_call_reason=True, an extra instruction is added telling the
    classifier to return a Call Reason rather than a Job Type. This is used
    for calls that were answered by a CSR but no job was booked — the
    classifier should identify what the call was actually about (Follow Up
    Call, Estimate Request, etc.) rather than defaulting to a job type or
    Missed Call.
    """

    action_items_block = ""
    if action_items:
        action_items_block = (
            "\n## Dialpad-Extracted Action Items\n"
            + "\n".join(f"- {item}" for item in action_items)
            + "\n"
        )

    force_call_reason_block = ""
    if force_call_reason:
        force_call_reason_block = """
## Important Context
A C&R CSR spoke with this caller but NO job was booked from the call.
You MUST return a Call Reason (classification_type = "call_reason"), not a Job Type.
Do NOT return "Missed Call" — someone answered this call.
Identify the actual purpose of the call from the transcript and recap (e.g. Follow Up
Call, Estimate Request, Demand, Warranty Work inquiry, Cancellation, etc.) and pick
the best matching Call Reason from the rulebook.
"""

    return f"""# Rulebook
{build_rulebook()}

---

# Call to Classify

## Call Metadata
- Caller phone: {caller_phone}
- Started at: {call_started_at}
- Duration: {duration_seconds} seconds
- Handled by (C&R CSR): {csr_name}
- ServiceTitan's own label for this call: "{servicetitan_label}"
{force_call_reason_block}
## Dialpad AI Recap
{recap or "(no recap available)"}
{action_items_block}
## Full Transcript
{transcript or "(no transcript available)"}

---

Classify this call now. Return ONLY the JSON object, no preamble, no markdown fences."""


def parse_classification_response(response_text: str) -> dict:
    """Parse the JSON response from Claude and validate it has the required fields.

    Handles: markdown fences, preamble text before JSON, malformed JSON.
    """
    text = response_text.strip()

    # Strip markdown code fences (```json ... ```)
    if "```" in text:
        start = text.find("```")
        end = text.rfind("```")
        if start != end:  # At least two fence markers
            text = text[start + 3 : end]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

    # If there's preamble text before the JSON object, extract just the { ... }
    if not text.startswith("{"):
        start_idx = text.find("{")
        if start_idx != -1:
            end_idx = text.rfind("}") + 1
            if end_idx > start_idx:
                text = text[start_idx:end_idx]

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Failed to parse classifier JSON response: {e}\n"
            f"Raw text (first 300 chars): {text[:300]}"
        ) from e

    required = {
        "classification_type",
        "classification_value",
        "confidence",
        "should_have_been_booked",
        "booking_recommendation",
        "reasoning",
    }
    missing = required - set(parsed.keys())
    if missing:
        raise ValueError(f"LLM response missing required fields: {missing}")

    if parsed["classification_type"] not in {"call_reason", "job_type"}:
        raise ValueError(f"Invalid classification_type: {parsed['classification_type']}")

    return parsed
