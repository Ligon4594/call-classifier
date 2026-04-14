"""
The classification engine: takes a call (transcript + metadata) and asks
Claude to assign a Call Reason or Job Type per the approved rulebook.

Supports two modes:
- "live": calls the Anthropic API (requires ANTHROPIC_API_KEY).
- "dry_run": prints the constructed prompt and returns a stub Classification.
  Useful for testing the rules + prompt structure WITHOUT API access — you
  can copy the printed prompt into Claude.ai manually to verify it works.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

from .models import Classification, DialpadCall, ServiceTitanCall
from .prompts import (
    SYSTEM_PROMPT,
    build_classification_prompt,
    parse_classification_response,
)

CLASSIFIER_VERSION = "v0.1-claude-haiku-4.5"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"


class Classifier:
    """Wrapper around the Anthropic API for call classification.

    The Anthropic SDK is imported lazily so that dry-run mode works even
    when the SDK isn't installed yet.
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        mode: str = "live",
    ):
        self.model = model
        self.mode = mode
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._client = None

        if self.mode == "live":
            if not self._api_key:
                raise RuntimeError(
                    "ANTHROPIC_API_KEY is not set. Either set it in the environment, "
                    "pass api_key=..., or use mode='dry_run' to print the prompt instead."
                )
            try:
                from anthropic import Anthropic  # type: ignore
            except ImportError as e:
                raise RuntimeError(
                    "anthropic SDK not installed. Run: pip install anthropic"
                ) from e
            self._client = Anthropic(api_key=self._api_key)

    def classify(
        self,
        st_call: ServiceTitanCall,
        dp_call: Optional[DialpadCall],
    ) -> Classification:
        """Classify a single call. dp_call may be None if no Dialpad match was found."""

        # Prefer Dialpad's resolved CSR name (follows operator_call_id), fall
        # back to ServiceTitan's agent name ("Last, First" format), then "unknown".
        csr_name = "unknown"
        if dp_call and dp_call.internal_user:
            csr_name = dp_call.internal_user
        elif st_call.agent_name:
            csr_name = st_call.agent_name

        # Build the ServiceTitan label string — include call type, existing
        # reason, and job type for maximum classifier context.
        st_label_parts = [st_call.call_type or ""]
        if st_call.reason_name:
            st_label_parts.append(f"reason={st_call.reason_name}")
        if st_call.job_type_name and st_call.job_type_name != "Imported Default JobType":
            st_label_parts.append(f"job_type={st_call.job_type_name}")
        if st_call.job_number:
            st_label_parts.append(f"job={st_call.job_number}")
        servicetitan_label = " | ".join(p for p in st_label_parts if p)

        prompt = build_classification_prompt(
            caller_phone=st_call.caller_phone,
            call_started_at=st_call.received_at.isoformat(),
            duration_seconds=st_call.duration_seconds,
            csr_name=csr_name,
            servicetitan_label=servicetitan_label,
            recap=(dp_call.recap if dp_call else "(no Dialpad match — no recap available)"),
            transcript=(dp_call.transcript if dp_call else "(no Dialpad match — no transcript available)"),
            action_items=(dp_call.action_items if dp_call else None),
        )

        if self.mode == "dry_run":
            return self._dry_run(st_call, prompt)

        return self._live_call(st_call, prompt)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _live_call(self, st_call: ServiceTitanCall, prompt: str) -> Classification:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text
        parsed = parse_classification_response(text)

        return Classification(
            call_id=st_call.call_id,
            classification_type=parsed["classification_type"],
            classification_value=parsed["classification_value"],
            confidence=float(parsed["confidence"]),
            should_have_been_booked=bool(parsed["should_have_been_booked"]),
            booking_recommendation=parsed["booking_recommendation"],
            reasoning=parsed["reasoning"],
            classified_at=datetime.now(timezone.utc),
            classifier_version=CLASSIFIER_VERSION,
            raw_llm_response=text,
        )

    def _dry_run(self, st_call: ServiceTitanCall, prompt: str) -> Classification:
        """Print the prompt and return a stub Classification.

        This lets you copy/paste the prompt into Claude.ai to verify the
        rules logic without needing an API key.
        """
        print("=" * 80)
        print("DRY RUN — copy the prompt below into Claude.ai to test the classifier")
        print("=" * 80)
        print()
        print("SYSTEM PROMPT:")
        print("-" * 80)
        print(SYSTEM_PROMPT)
        print()
        print("USER MESSAGE:")
        print("-" * 80)
        print(prompt)
        print("=" * 80)

        return Classification(
            call_id=st_call.call_id,
            classification_type="call_reason",
            classification_value="(dry-run — no actual classification)",
            confidence=0.0,
            should_have_been_booked=False,
            booking_recommendation=None,
            reasoning="Dry run mode — see printed prompt above. Paste into Claude.ai to test.",
            classified_at=datetime.now(timezone.utc),
            classifier_version=f"{CLASSIFIER_VERSION}-dry-run",
        )
