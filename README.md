# C&R Services — Call Classification App

Automatically classifies inbound phone calls from ServiceTitan using transcripts and AI summaries from Dialpad, and flags calls that should have been booked as a job but weren't.

**Status as of 2026-04-09:** Scaffolding complete. **Live Anthropic classification verified end-to-end on the Gita Ranum fixture** — Claude Haiku 4.5 returns `job_type / HVAC Maintenance / should_have_been_booked: true` with 0.95 confidence and explicitly explains the ServiceTitan/Dialpad mismatch in its reasoning. Cost: ~$0.004/call. Dialpad client is implemented (stdlib `urllib`, no external deps) and ready to test against a live call once Taylor runs it from his own machine. Still blocked on ServiceTitan integration env access.

---

## What this app does

For every call in a date range:

1. Pulls the call list from a ServiceTitan report (Unbooked & Abandoned Calls, or Calls Received).
2. Finds the matching call in Dialpad by phone number + timestamp.
3. Sends the Dialpad transcript and AI Recap to Claude with the approved C&R rulebook.
4. Claude returns: a Call Reason or Job Type, a confidence score, and a flag for whether the call looks like a missed booking opportunity.
5. Writes the classification back to ServiceTitan and emails Taylor a weekly summary.

The killer feature is step 4's missed-booking flag. The 2026-04-09 cross-reference test caught a real example: a 3-minute call from "Gita Ranum" was flagged as Abandoned in ServiceTitan, but the Dialpad transcript shows the customer successfully scheduled a maintenance appointment with Julie. That call should have been booked as an HVAC Maintenance job and wasn't. The whole point of this app is to surface those calls.

---

## What's built

| Module | Purpose | Status |
|---|---|---|
| `src/rules.py` | Approved Call Reasons + Job Types embedded verbatim from the approved rulebook | Done |
| `src/prompts.py` | Builds the Claude classification prompt from the rules | Done |
| `src/models.py` | Dataclasses for ServiceTitan calls, Dialpad calls, classifications | Done |
| `src/classifier.py` | Claude API client (live + dry-run modes) | Done |
| `src/linker.py` | Joins ServiceTitan calls to Dialpad calls by phone + timestamp | Done |
| `src/pipeline.py` | End-to-end orchestrator | Done |
| `src/reporter.py` | Weekly email report generator | Done |
| `src/servicetitan.py` | ServiceTitan API client | **Stubbed** — interface defined, real calls TBD |
| `src/dialpad.py` | Dialpad API client (stdlib urllib, no deps) | **Done** — `get_call`, `get_calls_in_window` (paginated), `find_call_by_phone_and_time`, `get_call_recap`, `get_call_transcript`. Pure helpers unit-tested. Live test pending Taylor's local run. |
| `tests/fixtures/sample_call_gita_ranum.json` | Real call captured manually for regression testing | Done |
| `scripts/classify_one.py` | CLI to classify a single call from a fixture file | Done |

## What's blocked

| Block | Owner | Status |
|---|---|---|
| ServiceTitan API credentials (App Key, Client ID, Client Secret) | Waiting on integration env access from ServiceTitan support | ETA ~Tue 2026-04-14 |
| Dialpad API key | Taylor | **Unblocked 2026-04-09** — key in `.env`, rotate after testing |
| Anthropic API key | Taylor | **Unblocked 2026-04-09** — key in `.env`, classifier verified live, rotate after testing |
| SMTP provider for weekly email | Pick one of SendGrid / Resend / Postmark | Pick after Railway is up |
| Railway hosting | Sign up + connect repo | After ServiceTitan creds are in |

---

## Trying it without any credentials (dry-run mode)

The classifier has a "dry run" mode that builds the full prompt (rulebook + call data) and prints it to stdout instead of calling the API. This lets you verify the prompt structure and rules without an API key, and you can paste the printed prompt into Claude.ai manually to test classification end-to-end.

```bash
cd call-classifier
python scripts/classify_one.py tests/fixtures/sample_call_gita_ranum.json --dry-run
```

The expected output is:
- A classification of `job_type` / `HVAC Maintenance`
- `should_have_been_booked: true`
- Reasoning that mentions the Thursday appointment

---

## Architecture

```
ServiceTitan API ──┐
                   ├──> linker.py ──> classifier.py (Claude) ──> ServiceTitan write-back
Dialpad API ───────┘                                          └─> reporter.py (weekly email)
```

**Key architectural decisions** (recorded in project memory `project_call_classification_app.md`):

1. **Dialpad replaces Deepgram.** Manual testing on 2026-04-09 confirmed Dialpad provides full text transcripts and AI Recaps for every call automatically — no separate transcription provider needed. Saves ~$10–15/month and removes a moving part.
2. **The Recap is the primary input.** It's already a 5-sentence summary written by an LLM. Most calls can be classified from the Recap alone, with the full transcript reserved as a fallback when confidence is low. This minimizes Claude API token use.
3. **The classifier can recommend missed bookings.** The output schema includes `should_have_been_booked` and `booking_recommendation`. This is the highest-ROI feature — it surfaces revenue that's leaking out of the abandoned bucket.
4. **Calls match by phone number + timestamp window.** Neither system stores the other's call ID, so the linker normalizes phone numbers (digits-only, last 10) and looks for Dialpad calls within a 2-minute window of the ServiceTitan timestamp.

---

## Cost estimate (monthly)

| Item | Cost |
|---|---|
| Anthropic API (Claude Haiku 4.5, ~5k calls/mo) | ~$5–15 |
| Railway hosting | ~$5 |
| SMTP (SendGrid free tier or Resend) | $0 |
| Dialpad transcripts | $0 (already paid for) |
| **Total** | **~$10–20/mo** |

Original plan with Deepgram was ~$30–55/mo. Eliminating the audio transcription step roughly halved the cost.

---

## Next steps

1. **Taylor: rotate the Dialpad and Anthropic API keys** that were shared in chat on 2026-04-09. Both are in `.env` for now; replace them with freshly minted keys before this app sees production traffic.
2. **Run a live Dialpad fetch from Taylor's machine.** From `call-classifier/`, run `pip install -r requirements.txt` then a one-liner like `python -c "from src.dialpad import DialpadClient; from dotenv import load_dotenv; load_dotenv(); c = DialpadClient(); print(c.get_call('5177714974531584'))"` to confirm the live Dialpad client can fetch the Gita Ranum call and that `recap` is populated. (The sandbox can't do this — Dialpad's API is not on the workspace egress allowlist.)
3. Once ServiceTitan integration env is granted (~Tue 2026-04-14): implement `servicetitan.py` (auth + report fetch + write-back).
4. Backfill the past 30 days of calls and email Taylor the report so we can validate the classifications and tune the prompt further.
5. Set up Railway with a daily cron and the weekly email schedule.

---

## File map

```
call-classifier/
├── README.md                       (you are here)
├── .env.example                    (copy to .env, fill in secrets, never commit)
├── .gitignore
├── requirements.txt
├── src/
│   ├── __init__.py
│   ├── rules.py                    (approved Call Reasons + Job Types)
│   ├── prompts.py                  (Claude prompt construction)
│   ├── models.py                   (dataclasses)
│   ├── classifier.py               (Claude API client + dry-run mode)
│   ├── servicetitan.py             (STUBBED — needs creds)
│   ├── dialpad.py                  (STUBBED — needs creds)
│   ├── linker.py                   (phone+time matching)
│   ├── pipeline.py                 (orchestrator)
│   └── reporter.py                 (weekly email)
├── scripts/
│   └── classify_one.py             (CLI: classify one call from a fixture)
└── tests/
    └── fixtures/
        └── sample_call_gita_ranum.json  (real call captured 2026-04-09)
```
