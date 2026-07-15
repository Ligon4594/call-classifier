"""
The end-to-end pipeline orchestrator.

For a given date range:
  1. Pull all calls from ServiceTitan (paginated)
  2. Pull all Dialpad calls in the same window (for batch linking)
  3. Link each ST call to its Dialpad match (phone + timestamp)
  4. Classify each linked call via Claude Haiku
  5. Optionally write classifications back to ServiceTitan
  6. Return results for the reporter

This is the main entry point for the daily / weekly cron job that will run
on Railway.
"""

from __future__ import annotations

import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional

from .classifier import Classifier
from .dialpad import DialpadClient
from .linker import link_batch
from .models import Classification, JobTypeMismatch, LinkedCall, ServiceTitanCall
from .servicetitan import ServiceTitanClient


def run_pipeline(
    *,
    start_date: date,
    end_date: date,
    st_client: ServiceTitanClient,
    dp_client: DialpadClient,
    classifier: Classifier,
    write_back: bool = False,
    enrich_recaps: bool = True,
    skip_already_classified: bool = True,
    verbose: bool = True,
) -> tuple[list[Classification], dict]:
    """Run the full classify pipeline for a date range.

    Args:
        start_date: First day to pull calls from (inclusive).
        end_date: Last day (exclusive).
        st_client: Authenticated ServiceTitan client.
        dp_client: Authenticated Dialpad client.
        classifier: Claude-backed classifier (or dry_run mode).
        write_back: If True, write classifications back to ServiceTitan.
        enrich_recaps: If True, fetch full recaps for matched Dialpad calls
                       (costs 1 extra API call per match but gives the classifier
                       much better context).
        skip_already_classified: If True, skip calls that already have a reason
                                 set in ServiceTitan (saves API calls + tokens).
        verbose: If True, print progress to stderr.

    Returns:
        Tuple of (classifications, stats) where stats is a dict with keys:
        total_st_calls, matched_dialpad, written_back, reason_field_updated.
    """
    log = _make_logger(verbose)
    stats: dict = {
        "total_st_calls": 0,
        "matched_dialpad": 0,
        "written_back": 0,
        "reason_field_updated": 0,
    }

    # ---- Step 1: Pull all ServiceTitan calls in the date range ----
    log(f"Step 1: Pulling ServiceTitan calls ({start_date} to {end_date})...")
    # ServiceTitan uses `createdBefore` (exclusive), so add 1 day to make the
    # end_date inclusive (e.g. --end 2026-03-28 should include all of March 28).
    st_calls = st_client.get_all_calls(start_date=start_date, end_date=end_date + timedelta(days=1))
    stats["total_st_calls"] = len(st_calls)
    log(f"  Got {len(st_calls)} calls from ServiceTitan.")

    # Filter out Avoca-handled calls — Avoca transcripts are not accessible via
    # Dialpad, so the classifier has no content to work with. Leave these calls
    # unclassified in ServiceTitan until Avoca API access is available.
    avoca_before = len(st_calls)
    st_calls = [
        c for c in st_calls
        if (c.agent_name or "").strip().lower() != "avoca"
    ]
    avoca_skipped = avoca_before - len(st_calls)
    if avoca_skipped:
        log(f"  Skipped {avoca_skipped} Avoca-handled call(s) (no transcript access).")

    # Optionally filter out calls that already have a classification.
    # Exception: always re-process calls labeled "Missed Call" — ServiceTitan
    # stamps this label natively on any "Abandoned" call, even if someone
    # actually answered. The classifier will correct these when it runs.
    if skip_already_classified:
        before = len(st_calls)
        st_calls = [
            c for c in st_calls
            if not c.reason_name                                      # unclassified
            or (c.reason_name or "").strip().lower() == "missed call" # or wrongly labeled
        ]
        skipped = before - len(st_calls)
        if skipped:
            log(f"  Skipped {skipped} already-classified calls. {len(st_calls)} remaining.")

    if not st_calls:
        log("  No calls to process. Done.")
        return [], stats

    # ---- Step 2: Pull Dialpad calls in the same window (for batch linking) ----
    log("Step 2: Pulling Dialpad calls for linking...")
    # Expand the window by 5 minutes on each side to catch edge cases.
    dp_start = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc) - timedelta(minutes=5)
    dp_end = datetime.combine(end_date, datetime.min.time(), tzinfo=timezone.utc) + timedelta(minutes=5)
    dp_calls = dp_client.get_calls_in_window(start=dp_start, end=dp_end)
    log(f"  Got {len(dp_calls)} calls from Dialpad.")

    # ---- Step 3: Link ST ↔ Dialpad by phone + timestamp ----
    log("Step 3: Linking ServiceTitan calls to Dialpad calls...")
    linked = link_batch(st_calls, dp_calls, window_seconds=120)
    matched = sum(1 for lc in linked if lc.dialpad is not None)
    stats["matched_dialpad"] = matched
    log(f"  {matched}/{len(linked)} calls matched to Dialpad records.")

    # ---- Step 3b: Enrich matched calls with full Dialpad recaps ----
    if enrich_recaps and matched > 0:
        log("Step 3b: Fetching AI Recaps for matched calls...")
        enriched = 0
        for lc in linked:
            if lc.dialpad and not lc.dialpad.recap:
                try:
                    full_call = dp_client.get_call(lc.dialpad.call_id)
                    lc.dialpad.recap = full_call.recap
                    lc.dialpad.action_items = full_call.action_items
                    lc.dialpad.transcript = full_call.transcript
                    enriched += 1
                    # Dialpad rate limit on /call/{id} is 10/min.
                    if enriched % 9 == 0:
                        log(f"    Enriched {enriched} calls, pausing for rate limit...")
                        time.sleep(6)
                except Exception as e:
                    log(f"    Warning: couldn't enrich call {lc.dialpad.call_id}: {e}")
        log(f"  Enriched {enriched} calls with recaps.")

    # ---- Step 4: Classify each call ----
    log(f"Step 4: Classifying {len(linked)} calls via Claude...")
    classifications: list[Classification] = []
    for i, lc in enumerate(linked, 1):
        try:
            result = classifier.classify(lc.servicetitan, lc.dialpad)
            classifications.append(result)
            if verbose and i % 10 == 0:
                log(f"  Classified {i}/{len(linked)}...")
        except Exception as e:
            log(f"  Error classifying call {lc.servicetitan.call_id}: {e}")
            continue

    log(f"  Classified {len(classifications)}/{len(linked)} calls.")

    # Build call lookup map — used in Steps 5 and 6.
    st_call_map = {lc.servicetitan.call_id: lc.servicetitan for lc in linked}

    # ---- Step 5: Write back to ServiceTitan (if enabled) ----
    if write_back:
        log("Step 5: Writing classifications back to ServiceTitan...")

        # Fetch ServiceTitan's call reason list so we can set the actual
        # Call Reason field (not just the memo) when the name matches.
        reason_name_to_id: dict[str, int] = {}
        try:
            st_reasons = st_client.get_call_reasons()
            reason_name_to_id = {
                _normalize_reason_name(r.get("name", "")): r["id"]
                for r in st_reasons
                if r.get("name") and r.get("id")
            }
            log(f"  Loaded {len(reason_name_to_id)} call reasons from ServiceTitan for ID mapping.")
        except Exception as e:
            log(f"  Warning: couldn't load call reasons from ServiceTitan — will use memo only. ({e})")

        # Build lookup maps for write-back decisions.
        call_type_map: dict[str, str] = {
            lc.servicetitan.call_id: (lc.servicetitan.call_type or "")
            for lc in linked
        }
        # job_id is None for unbooked calls — used to detect missed service calls.
        call_job_map: dict[str, Optional[int]] = {
            lc.servicetitan.call_id: lc.servicetitan.job_id
            for lc in linked
        }

        written = 0
        skipped_no_id = 0
        for result in classifications:
            current_call_type = call_type_map.get(result.call_id, "")

            # Never touch already-Booked calls — a job exists and changing
            # callType to Excused would corrupt the booking record.
            if current_call_type == "Booked":
                continue

            if result.classification_type == "call_reason":
                # Threshold: 0.5 (lower than job_type writes) because call_reason
                # calls are never booked so the risk of a wrong write is low.
                # This catches edge cases — Avoca calls, short abandoned calls —
                # that have less context and legitimately score 0.5–0.69.
                if result.confidence < 0.5:
                    continue

                # ----- ANSWERED-CALL GUARD on the call_reason write path -----
                # If Claude returned "Missed Call" but the CSR actually answered,
                # do NOT write Missed Call. Re-classify with force_call_reason and
                # fall back to "Follow Up Call" — never persist Missed Call on a
                # call we have positive evidence was answered.
                if _normalize_reason_name(result.classification_value) == _normalize_reason_name("Missed Call"):
                    st_call = st_call_map.get(result.call_id)
                    lc = next((lc for lc in linked if lc.servicetitan.call_id == result.call_id), None)
                    dp_call = lc.dialpad if lc else None
                    if _call_was_answered(st_call, dp_call):
                        _handle_answered_missed_call(
                            st_call=st_call,
                            dp_call=dp_call,
                            result=result,
                            classifier=classifier,
                            st_client=st_client,
                            reason_name_to_id=reason_name_to_id,
                            log=log,
                        )
                        # _handle_answered_missed_call does its own writes/logging.
                        # Bump written if it succeeded (it logs [ok] either way).
                        written += 1
                        continue

                norm = _normalize_reason_name(result.classification_value)
                call_reason_id: Optional[int] = reason_name_to_id.get(norm)

                if call_reason_id is None:
                    log(f"  [skip] call {result.call_id}: '{result.classification_value}' has no matching ST reason ID")
                    skipped_no_id += 1
                    continue

                try:
                    st_client.write_classification(
                        call_id=result.call_id,
                        call_reason_id=call_reason_id,
                        call_reason_name=result.classification_value,
                    )
                    written += 1
                    log(f"  [ok] call {result.call_id}: set Call Reason → '{result.classification_value}' (id={call_reason_id})")
                except Exception as e:
                    log(f"  [error] call {result.call_id}: {e}")

            elif result.classification_type == "job_type":
                # If the AI sees an HVAC service call but no booking exists in ST,
                # that's a missed service opportunity. We can't write a job type
                # to the call record directly, so we stamp "Missed Call" as the
                # call reason — BUT only if the call was truly abandoned (no real
                # CSR answered). If a CSR was on the line and just didn't book the
                # job, "Missed Call" is semantically wrong (it implies no one
                # picked up). Those calls are flagged in the weekly report for
                # manual follow-up instead.
                if result.confidence < 0.5:
                    continue

                job_id = call_job_map.get(result.call_id)
                if job_id:  # 0 and None both mean no booking in ServiceTitan
                    continue  # Has a booking — job type is already on the job record.

                # Check whether the call was actually answered. Uses multi-signal
                # detection (CSR named, duration, Dialpad evidence) — see
                # _call_was_answered docstring. If answered, we MUST NOT stamp
                # "Missed Call". Either short-circuit to Wrong Number/Hang Up
                # (clear hang-up case) or re-classify via transcript.
                st_call = st_call_map.get(result.call_id)
                lc = next((lc for lc in linked if lc.servicetitan.call_id == result.call_id), None)
                dp_call = lc.dialpad if lc else None

                if _call_was_answered(st_call, dp_call):
                    has_content = dp_call and bool(dp_call.recap or dp_call.transcript)
                    duration = st_call.duration_seconds if st_call else 0

                    # Short-circuit: CSR answered, no transcript, very short call →
                    # caller hung up immediately. No need to call Claude.
                    # (Only short-circuit when ST reports a real but short duration.
                    # duration=0 from ST is unreliable on Abandoned calls — we let
                    # those fall through to the re-classify path.)
                    if not has_content and 0 < duration <= 30:
                        wn_id = reason_name_to_id.get(_normalize_reason_name("Wrong Number / Hang Up / Spam"))
                        if wn_id:
                            try:
                                st_client.write_classification(
                                    call_id=result.call_id,
                                    call_reason_id=wn_id,
                                    call_reason_name="Wrong Number / Hang Up / Spam",
                                )
                                written += 1
                                log(f"  [ok] call {result.call_id}: answered+short+no transcript → 'Wrong Number / Hang Up / Spam'")
                            except Exception as e:
                                log(f"  [error] call {result.call_id}: {e}")
                        else:
                            log(f"  [skip] call {result.call_id}: no ST reason ID for 'Wrong Number / Hang Up / Spam'")
                        continue

                    # Otherwise: CSR answered but no job was booked. Delegate to
                    # the shared answered-Missed-Call handler — re-classify via
                    # transcript and ALWAYS fall back to Follow Up Call rather
                    # than leaving a false "Missed Call" stamp.
                    _handle_answered_missed_call(
                        st_call=st_call,
                        dp_call=dp_call,
                        result=result,
                        classifier=classifier,
                        st_client=st_client,
                        reason_name_to_id=reason_name_to_id,
                        log=log,
                    )
                    written += 1
                    continue

                # Truly abandoned call (short duration, no CSR) that looked like
                # a service inquiry. Write "Missed Call" so it surfaces in ST reports.
                missed_call_id = reason_name_to_id.get(_normalize_reason_name("Missed Call"))
                if not missed_call_id:
                    log(f"  [skip] call {result.call_id}: 'Missed Call' reason not in ST reason map")
                    continue

                try:
                    st_client.write_classification(
                        call_id=result.call_id,
                        call_reason_id=missed_call_id,
                        call_reason_name="Missed Call",
                    )
                    written += 1
                    log(f"  [ok] call {result.call_id}: unbooked {result.classification_value} → set 'Missed Call' (abandoned, potential missed booking)")
                except Exception as e:
                    log(f"  [error] call {result.call_id}: {e}")

        stats["written_back"] = written
        stats["reason_field_updated"] = written  # Every successful write updates the reason field
        log(f"  Wrote {written} Call Reason fields back to ServiceTitan.")
        if skipped_no_id:
            log(f"  Skipped {skipped_no_id} calls with no matching ST reason ID (check your Call Reasons in ST match the rulebook).")
    else:
        log("Step 5: Write-back disabled (dry run). No changes made to ServiceTitan.")

    # ---- Step 6: Audit Job Types on Booked calls ----
    # For every booked call the classifier assigned a job_type to, compare the
    # prediction against the job type already on the ST booking.  Mismatches at
    # ≥70% confidence are flagged for Taylor to review in the weekly report.
    log("Step 6: Auditing job type accuracy on booked calls...")
    mismatches: list[JobTypeMismatch] = []
    for result in classifications:
        if result.classification_type != "job_type":
            continue
        if result.confidence < 0.7:
            continue
        st_call = st_call_map.get(result.call_id)
        if not st_call or not st_call.job_id:  # 0 and None both mean no booking
            continue  # Unbooked calls are handled by the missed-booking logic above
        actual_raw = st_call.job_type_name or ""
        if not actual_raw or actual_raw == "Imported Default JobType":
            continue
        actual_norm = _normalize_job_type(actual_raw)
        predicted_norm = _normalize_job_type(result.classification_value)
        if actual_norm == predicted_norm:
            continue  # Match — all good
        mismatches.append(JobTypeMismatch(
            call_id=result.call_id,
            job_number=st_call.job_number,
            caller_phone=st_call.caller_phone,
            customer_name=st_call.customer_name,
            received_at=st_call.received_at,
            actual_job_type=actual_raw,
            predicted_job_type=result.classification_value,
            confidence=result.confidence,
            reasoning=result.reasoning,
        ))

    stats["job_type_mismatches"] = mismatches
    if mismatches:
        log(f"  Found {len(mismatches)} job type mismatch(es) to review.")
    else:
        log("  No job type mismatches found.")

    # ---- Done ----
    summary = summarize(classifications)
    log(f"\nDone! {summary['total']} calls processed.")
    log(f"  Missed bookings flagged: {summary['missed_bookings']}")
    log(f"  Low-confidence (needs review): {summary['low_confidence']}")
    log(f"  Top classifications: {list(summary['by_value'].items())[:5]}")

    return classifications, stats


def summarize(classifications: Iterable[Classification]) -> dict:
    """Quick summary stats for a batch of classifications. Used by the reporter."""
    classifications = list(classifications)
    total = len(classifications)
    if total == 0:
        return {"total": 0, "by_value": {}, "missed_bookings": 0, "low_confidence": 0}

    by_value: dict[str, int] = {}
    missed = 0
    low_conf = 0
    for c in classifications:
        by_value[c.classification_value] = by_value.get(c.classification_value, 0) + 1
        if c.should_have_been_booked:
            missed += 1
        if c.confidence < 0.6:
            low_conf += 1

    return {
        "total": total,
        "by_value": dict(sorted(by_value.items(), key=lambda kv: -kv[1])),
        "missed_bookings": missed,
        "low_confidence": low_conf,
    }


def _call_was_answered(st_call: Optional[ServiceTitanCall], dp_call) -> bool:
    """Determine whether a real human at C&R answered this call.

    Why this is hard: ServiceTitan stamps "Abandoned" + "Missed Call" + duration=0
    on ANY call where the CSR didn't click the green "Accept" button in the
    ServiceTitan agent UI — even if the CSR picked up the actual phone. So we
    can't trust ST's `agent` field, ST's `duration_seconds`, or ST's `callType`
    individually. We need positive evidence from any of several signals.

    A call is considered ANSWERED if ANY of these are true:
      (a) A named CSR is attributed (agent ≠ "Abandoned"/"Avoca"/empty)
      (b) ServiceTitan duration > 20 seconds (real conversation length)
      (c) Dialpad shows connected_seconds > 0 (Dialpad's authoritative
          answered-vs-not signal — Dialpad knows whether the call was actually
          picked up regardless of how ST tagged the agent)
      (d) Dialpad has any transcript or AI recap content (transcripts only
          exist for answered calls — Dialpad doesn't transcribe ringing)

    Returns False ONLY when we have no positive evidence of an answer. In any
    ambiguous case the safe default is to treat the call as answered, because
    writing a false "Missed Call" is worse than over-classifying an actually-
    missed call as "Follow Up Call".
    """
    # Signal (a): named CSR
    agent_raw = (st_call.agent_name or "").strip().lower() if st_call else ""
    if agent_raw and agent_raw not in ("abandoned", "avoca"):
        return True

    # Signal (b): meaningful ST duration
    if st_call and st_call.duration_seconds > 20:
        return True

    # Signal (c): Dialpad confirms call was connected
    if dp_call and getattr(dp_call, "connected_seconds", 0) > 0:
        return True

    # Signal (d): Dialpad has transcript content (only generated for answered calls)
    if dp_call and (getattr(dp_call, "transcript", None) or getattr(dp_call, "recap", None)):
        return True

    return False


def _handle_answered_missed_call(
    *,
    st_call,
    dp_call,
    result,
    classifier,
    st_client,
    reason_name_to_id: dict,
    log,
) -> None:
    """Handle a call that was answered but Claude wanted to label as Missed Call.

    Flow:
      1. Re-classify with force_call_reason=True to find the actual reason from
         the transcript (Follow Up Call, Demand, Estimate Request, etc.).
      2. If re-classification produces a confident, mappable Call Reason → write it.
      3. Otherwise → write "Follow Up Call" as the guaranteed safe default.

    A call we have positive evidence was answered must NEVER be left labeled
    "Missed Call" after this function returns. The fallback always fires.
    """
    log(f"  [re-classify] call {result.call_id}: answered "
        f"(agent='{(st_call.agent_name if st_call and st_call.agent_name else 'unknown')}', "
        f"duration={st_call.duration_seconds if st_call else '?'}s, "
        f"dp_connected={getattr(dp_call, 'connected_seconds', 0) if dp_call else 0}s) "
        f"— pulling transcript to find actual call reason...")

    # Fallback selection (updated 2026-07-14 per Taylor):
    #   - If Dialpad has ANY transcript/recap content → Follow Up Call (real conversation)
    #   - If NO transcript content → Wrong Number / Hang Up / Spam (CSR picked up but
    #     nobody was really there). Taylor's rule: "If a CSR picks up and nobody is
    #     there, it counts as a hang up." Follow Up Call is reserved for calls with
    #     evidence of a real conversation about a prior job/estimate.
    has_content = dp_call and bool(
        getattr(dp_call, "recap", None) or getattr(dp_call, "transcript", None)
    )
    follow_up_id = reason_name_to_id.get(_normalize_reason_name("Follow Up Call"))
    hangup_id    = reason_name_to_id.get(_normalize_reason_name("Wrong Number / Hang Up / Spam"))
    fallback_id, fallback_name = (
        (follow_up_id, "Follow Up Call") if has_content else (hangup_id, "Wrong Number / Hang Up / Spam")
    )
    written = False

    # Step 1: try a confident re-classification.
    try:
        reason_result = classifier.classify(st_call, dp_call, force_call_reason=True)
        if reason_result.confidence >= 0.5:
            norm = _normalize_reason_name(reason_result.classification_value)
            cr_id = reason_name_to_id.get(norm)
            if cr_id:
                st_client.write_classification(
                    call_id=result.call_id,
                    call_reason_id=cr_id,
                    call_reason_name=reason_result.classification_value,
                )
                log(f"  [ok] call {result.call_id}: answered → '{reason_result.classification_value}' "
                    f"({reason_result.confidence:.0%})")
                written = True
            else:
                log(f"  [info] call {result.call_id}: re-classified as "
                    f"'{reason_result.classification_value}' but no ST reason ID — "
                    f"applying '{fallback_name}' fallback")
        else:
            log(f"  [info] call {result.call_id}: re-classification confidence "
                f"{reason_result.confidence:.0%} — applying '{fallback_name}' fallback")
    except Exception as e:
        log(f"  [error] call {result.call_id}: re-classification failed: {e} — "
            f"applying '{fallback_name}' fallback")

    # Step 2: guaranteed fallback. Answered calls NEVER stay as Missed Call.
    # Fallback is Wrong Number / Hang Up / Spam by default (no transcript = no
    # real conversation), or Follow Up Call only when transcript evidence exists.
    if not written:
        if not fallback_id:
            log(f"  [error] call {result.call_id}: no ST reason ID for '{fallback_name}' — "
                f"cannot apply fallback. Check FALLBACK_REASON_IDS in servicetitan.py")
            return
        try:
            st_client.write_classification(
                call_id=result.call_id,
                call_reason_id=fallback_id,
                call_reason_name=fallback_name,
            )
            log(f"  [ok] call {result.call_id}: answered+no-confident-reason → "
                f"'{fallback_name}' (guaranteed fallback; cleared false Missed Call)")
        except Exception as e:
            log(f"  [error] call {result.call_id}: '{fallback_name}' fallback write failed: {e}")


def _normalize_job_type(name: str) -> str:
    """Normalize a job type name for comparison.

    Strips case, whitespace, hyphens, and common prefixes so that small
    formatting differences between the classifier's vocabulary and ServiceTitan's
    stored job type names don't produce false positives.

    Examples:
      "HVAC No Cool"  → "hvacnocool"
      "HVAC - No Cool" → "hvacnocool"
      "No Cool"        → "nocool"   (different from above — intentional flag)
    """
    import re
    return re.sub(r"[\s\-/]", "", name.lower()).strip()


def _normalize_reason_name(name: str) -> str:
    """Normalize a call reason name for fuzzy matching.

    Handles known formatting differences between our rulebook and what
    ServiceTitan stores, e.g.:
      "Wrong Number / Hang Up / Spam"  ↔  "Wrong Number/Hang Up/Spam"
      "Estimate Request -- HVAC"       ↔  "Estimate Request-HVAC"
      "Vendor / Marketing"             ↔  "Vendor/marketing"
    """
    import re
    n = name.lower().strip()
    n = n.replace(" / ", "/")   # "Wrong Number / Hang Up" → "wrong number/hang up"
    n = re.sub(r"\s*--\s*", "-", n)   # "Estimate Request -- HVAC" → "estimate request-hvac"
    n = re.sub(r"\s+-\s+", "-", n)   # "Estimate Request - HVAC" → "estimate request-hvac"
    n = re.sub(r"\s+", " ", n)  # collapse double spaces
    return n


def _make_logger(verbose: bool):
    """Return a log function that prints to stderr if verbose, else no-ops."""
    if verbose:
        def log(msg: str) -> None:
            print(msg, file=sys.stderr)
        return log
    return lambda msg: None
