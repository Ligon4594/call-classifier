# C&R Call Classifier — Handoff

**Updated:** 2026-08-19
**For:** picking this work up in any Claude session (Cowork, Claude Code, VS Code, etc.)
**Owner:** Taylor Ligon (tligon@crhvacpro.com), C&R Services / Trinity Climate Solutions LLC, Whitehouse TX

**To start:** point the session at this directory (`call-classifier/`) and read this file top to bottom before touching code. §4 (Governance) is non-negotiable. §7 (Known traps) will save you hours. In Cowork, note the local `.env` is the secret source for local runs — never commit or display its values.

> **⚡ WHERE THINGS STAND (2026-08-20):** Avoca key blocker is **RESOLVED** — local `.env`
> now holds the same working key as Railway, verified against the live API. The 08-17 Railway
> cron was confirmed healthy (Step 3c wrote 21 real Call Reasons). Remaining: **(a)** push the
> local unpushed change (skip filter now re-processes `"Avoca"`-placeholder calls), **(b)** run
> the write-back backfill, **(c)** resolve a NEW issue the cron log exposed — the `"Avoca"`
> Call Reason appears to be missing/inactive in ServiceTitan, so ~10 calls per run can't be
> written at all. See §6.

---

## 1. What this system does

Every inbound phone call to C&R lands in **ServiceTitan** as a call record. ServiceTitan's own labels are unreliable (it stamps "Abandoned" whenever a CSR didn't click the green Accept button, even if they picked up the actual phone). So this pipeline:

1. Pulls ServiceTitan calls for a date range.
2. Links each one to its **Dialpad** record (by phone + timestamp) to get the AI recap / transcript.
3. Sends recap + transcript to **Claude** to assign exactly one **Call Reason** (no job booked) or **Job Type** (job booked), chosen strictly from C&R's approved ServiceTitan rulebook.
4. Writes the Call Reason back to ServiceTitan (only with `--write-back`).
5. Flags calls that should have been booked but weren't, and job-type mismatches.
6. Emails a weekly report.

Downstream, these Call Reasons feed C&R's **EGIA KPI reporting** (booking rate, lead counts). That's why the reason values must stay clean — see Governance below.

**Avoca** is C&R's AI virtual receptionist. It answers overflow / after-hours calls. ServiceTitan shows those calls with `agent_name = "Avoca"`. They have no Dialpad record, and Avoca's transcript API is broken, so they bypass Claude entirely: Avoca's own `call_reason` enum is mapped deterministically to an approved ST Call Reason (`src/avoca_mapping.py`, pipeline Step 3c).

---

## 2. Where everything lives

| Thing | Location |
|---|---|
| Local working copy | `~/Documents/Claude/Projects/CFO C&R/06-Call-Classification/call-classifier/` |
| GitHub repo | `https://github.com/Ligon4594/call-classifier` (branch `main`) |
| Production host | Railway — auto-deploys from GitHub `main`; cron `0 12 * * 1` runs `python run.py --days 7 --write-back --send-email` |
| Secrets (local) | `.env` in the repo root — **gitignored, never commit** |
| Secrets (prod) | Railway dashboard → project → Variables |

### Push flow
`git push origin main` (or double-click `push_to_github.command`, which stages the source files, prompts for a commit message, and pushes).

Auth (fixed 2026-08-11): credentials live in the macOS keychain via `git credential-osxkeychain` — no token in any file. If auth ever fails: generate a new fine-grained PAT (repo `call-classifier`, Contents: Read+write), run `git push origin main` in Terminal once, enter username `Ligon4594` + the token — the keychain stores it.

### Environment variables required
Present in `.env` locally; must also exist in Railway:

```
ANTHROPIC_API_KEY
SERVICETITAN_TENANT_ID / _APP_KEY / _CLIENT_ID / _CLIENT_SECRET
DIALPAD_API_KEY
RESEND_API_KEY / RESEND_FROM / REPORT_RECIPIENT
AVOCA_API_KEY          # format: avoca_<64 hex>
AVOCA_TEAM_ID          # blank for a team-level key
```

**✅ RESOLVED 2026-08-20: `AVOCA_API_KEY` now works in both Railway and local `.env`.** History, because the failure mode is worth knowing: the local key worked through the morning of 2026-08-11, then started returning `401 Unauthorized` right when a key was created for Railway — Avoca **appears to allow only one active key per team**, so generating the new one revoked the old. Fix was simply copying Railway's value into local `.env`.

`fix_avoca_key.command` automates that copy safely (updates only the `AVOCA_API_KEY=` line, backs up `.env`, runs the connection test, auto-restores on failure, never prints the secret). Use it if the key ever needs re-syncing.

> **Terminal paste gotcha:** the prompt hides input, so it looks like nothing happened and it's natural to hit Cmd-V twice. That produces a 140-character doubled key and a confusing 401. The script now extracts the first well-formed `avoca_` + 64-hex key out of whatever is pasted, so doubled pastes and `name=value` pastes both self-correct. A correct key is **exactly 70 characters**.

Verify any time with `python3 test_avoca_connection.py`.

> ⚠️ **Because of the single-active-key behavior: NEVER regenerate the Avoca key as a casual debugging step.** Generating a new key silently kills whichever copy is live — regenerating "to be safe" is exactly how prod breaks. Only regenerate when you intend to replace it everywhere at once.

**Other open security items (don't let these disappear):**
- The old GitHub PAT that used to sit in plaintext in `push_to_github.command` was removed from the file, but there is **no record it was revoked** on GitHub. Revoke it at github.com → Settings → Developer settings → Personal access tokens (the keychain-stored fine-grained PAT is the only one that should remain).
- Per `.env`'s own comments, the **Anthropic and Dialpad keys were each pasted into a chat once** (2026-04-09) and are still awaiting a final quiet rotation done directly in the provider dashboards + Railway, without the values ever appearing in chat again.

**Python version gotcha:** local Mac runs Python **3.9.6**; the Railway image is **python:3.11-slim**. They disagree on things like `fromisoformat` strictness — one such disagreement already hid a prod-only-works bug (§7). When touching date/string parsing, test against 3.9 semantics.

---

## 3. Code map

```
run.py                      CLI entry point + .env loader
src/
  pipeline.py               Orchestrator. Steps 1–6. Most of the logic lives here.
  servicetitan.py           ST API client (OAuth, get_all_calls, write_classification, get_call_reasons)
  dialpad.py                Dialpad API client (calls + recaps)
  avoca.py                  Avoca Enterprise API client (read-only)
  avoca_mapping.py          Avoca call_reason enum → approved ST Call Reason lookup table
  linker.py                 ST↔Dialpad and ST↔Avoca matching (phone+timestamp; job_id for Avoca)
  classifier.py             Claude call. mode="live" | "dry_run"
  prompts.py                System prompt + user prompt construction
  rules.py                  THE RULEBOOK — 14 Call Reasons + 15 Job Types (mirrors ServiceTitan)
  models.py                 Dataclasses: ServiceTitanCall, DialpadCall, AvocaCall, LinkedCall, Classification
  reporter.py               Text/HTML report + Resend email
diagnose_avoca_match.py     Prints both sides of ST↔Avoca matching with deltas — first tool to reach for if match rates drop
```

### CLI flags (`run.py`)

| Flag | Effect |
|---|---|
| `--days N` | Look back N days (default 7) |
| `--start / --end` | Explicit range. **`--end` is INCLUSIVE.** |
| `--write-back` | Actually write to ServiceTitan. **Off by default.** |
| `--send-email` | Send report via Resend |
| `--dry-run` | Print Claude prompts instead of calling the API (no cost). Does **not** affect Step 3c — Avoca mapping is deterministic and runs for real (but still won't write without `--write-back`). |
| `--no-skip` | Don't skip already-classified calls |
| `--quiet` | Suppress progress |

Safe verification run (no ST writes, no Claude cost, ~10 min due to Dialpad rate limits):

```bash
python3 run.py --days 7 --no-skip --dry-run
```

---

## 4. Governance — non-negotiable rules

These came directly from Taylor. Violating them corrupts downstream KPI reports.

1. **Never add, remove, rename, or activate a ServiceTitan Call Reason.** (Locked 2026-06-05.) The classifier may *change which* reason is on a call, never *invent* one. `rules.py` is a strict mirror of ST's master list. If a new reason is genuinely needed, Taylor adds it in ServiceTitan first, then it goes in `rules.py`.
2. **Never invent spelling variants.** "Estimate Request-HVAC" is the exact string — not "HVAC Estimate Request", not "Estimate-HVAC". Variants create duplicates that break reports.
3. **Ask before mapping a new Avoca enum value.** If a run logs `⚠ UNMAPPED Avoca call_reason values`, don't guess a bucket — surface it to Taylor. (Instruction 2026-08-10. The pipeline logs these distinctly for exactly this reason.)
4. **"Missed Call" means nobody picked up.** Not a catch-all for uncertainty. ServiceTitan's "Abandoned" label is NOT evidence of a missed call.
5. **CSR answered but no transcript/recap → "Wrong Number / Hang Up / Spam"**, not "Follow Up Call". (Locked 2026-07-14. Taylor: *"If a CSR picks up the phone and nobody is there, it's a hang up."*) "Follow Up Call" requires actual evidence of a conversation about a prior job/estimate.
6. **Never write to a call that already has a job.** Booked Avoca calls are skipped — ST already has the correct Job Type from the booking.

---

## 5. The Avoca direct-mapping design (current, shipped)

### Why it exists
Avoca calls have no Dialpad record. The original plan — pull Avoca transcripts via `GET /api/calls/{id}/transcript` and classify normally — died because **that endpoint is broken**: it returns `404 "Transcript not available for this call"` for the large majority of calls, including ones with a full transcript visible in Avoca's own dashboard. Confirmed 2026-08-10, reported to Avoca support (ticket pending).

### How it works
Avoca's `call_reason` **enum** field populates reliably and already *is* Avoca's own classification. So Avoca calls **bypass Claude entirely**:

- `src/avoca_mapping.py` — lookup table built from an empirical survey of all 563 Avoca calls on record (`survey_avoca_call_reasons.py` → `.log`). Bucket decisions confirmed with Taylor 2026-08-10.
- Unmapped / missing values fall back to `"Avoca"` — the existing ST placeholder Julie reviews manually. Never a guess. New enum values are logged with a `⚠ UNMAPPED` line (governance #3).
- `pipeline.py` **Step 3c** — splits Avoca calls out, links them (exact ST Job ID preferred over phone+timestamp), skips booked ones, maps the rest, optionally writes back.

Key mapping decisions:
- `UNBOOKED_TIME_CONCERN` / `PRICE_CONCERN` / `CALL_BACK_LATER` / `PENDING_COORDINATION` / `TRIP_CHARGE` → **"Demand"** (real bookable opportunities that didn't book; keeps the EGIA booking rate honest).
- `EXCUSED_TRANSFER_TO_SPECIFIC_PERSON`, `EXCUSED_OTHER_QUESTIONS`, `EXCUSED_LIVE_REPRESENTATIVE_TRANSFER`, `UNBOOKED_REJECT_AGENT`, `EXCUSED_INSTALLATION_CALL` → **"Avoca"** (ambiguous, left for Julie).
- `FOLLOW_UP_COMPLAINT` → **"Follow Up Call"** (Taylor, 2026-08-11).
- `BOOKED_*` values intentionally absent — those calls are skipped (governance #6).

### The 2026-08-11 fix round (all shipped in `542a8aa`)
The first version matched only 4/14 Avoca calls, and the initial "end-date window" fix would have crashed production. Four real defects were found and fixed:

1. **Dialpad rejects future query windows** (400 "Timestamp range cannot be in the future") — and `end_date + 1 day` is in the future on every `--days N` / cron run. Step 2's window is now clamped to `now() - 1min`.
2. **Avoca's `created_at` is the call END, not the start** — the actual cause of the low match rate. `started_at` is now computed as `created_at - duration_seconds`.
3. **`_parse_avoca_timestamp` fabricated `now()`** on Avoca's variable-precision fractional seconds (Python 3.9 only). Now normalized to 6 digits; genuine failures return a 1970 sentinel, never `now()`.
4. **Dialpad list pull was silently capped at exactly 1250 calls** (no `limit` param × 25/page default × max_pages=50), dropping the tail of every 7-day window. Now 50/page × 400 pages with a loud truncation warning.

Verified 2026-08-11 (`--days 7 --no-skip --dry-run`):
- Avoca match **22/23** (was 4/14). Mapped: `{'Follow Up Call': 8, 'Avoca': 7, 'Wrong Number / Hang Up / Spam': 3, 'Demand': 1}` — the remaining `'Avoca'`s are the intentionally-ambiguous enums left for Julie.
- Dialpad: 1565 calls pulled (was 1250 capped), match **301/537** (was 243/505).

---

## 6. Current state / next steps

**Status (2026-08-19): pushed (`c3c3c23`), deployed, key in Railway. Write-back backfill in progress, blocked on the local Avoca key (§2).**

### Done so far
- 2026-08-11: pushed to `main`; Railway auto-deployed. GitHub auth moved to macOS keychain (no more plaintext PAT in the file — revoking the old token on GitHub is still open, see §2). `AVOCA_API_KEY` added to Railway. **Whether the 08-17 cron actually ran clean with that key is unverified** — that's step 1 below.
- 2026-08-19 (local, **not yet pushed**): skip filter in `pipeline.py` extended to re-process `"Avoca"`-placeholder calls, so default runs (including the cron) can upgrade them via Step 3c without `--no-skip`. `push_to_github.command` staging list now includes `HANDOFF.md` and `diagnose_avoca_match.py` so they travel with the repo.
- **Write-back 2026-08-04** (first live run of the new code): 63 calls, 34 already classified and skipped, 29 classified, **8 written** — `61919277`, `61919416`, `61924272`, `61925422`, `61925429`, `61927604` → Missed Call; `61925165`, `61936812` → Follow Up Call. Zero errors. That day had no Avoca calls, so Step 3c wasn't exercised.
- **Write-back 2026-08-08** (two runs, 08-11 and 08-19): **7 written** — `62012336` → Follow Up Call; `62013113`, `62015798` → Wrong Number / Hang Up / Spam; `62015795`, `62015800` + two more → Missed Call. **But Step 3c failed both times with Avoca 401** — the 4 Avoca-handled calls that day (`62011818`, `62011854`, `62012219`, `62015804`) are still unclassified.
- No unmapped Avoca enums or job-type mismatches in any run so far.

### ✅ Verified 2026-08-20 — the 08-17 Railway cron ran clean
Read directly from the Railway deploy log. Step 3c worked end to end in production:
- `38 call(s) handled by Avoca AI` → `Got 50 calls from Avoca` → **`33/38 matched`** (4 by exact ST Job ID, 29 by phone+timestamp).
- `Mapped Avoca call reasons: {'Follow Up Call': 11, 'Wrong Number / Hang Up / Spam': 6, 'Avoca': 11, 'Vendor / Marketing': 1, 'Requires Different Service': 2, 'Maintenance': 1}`
- **`Wrote 21 Avoca-derived Call Reason field(s) back to ServiceTitan.`** 6 booked calls correctly skipped.
- No 401, no `Avoca API not configured`, no unmapped enums.

So the Railway key was alive all along; only the local copy was stale. **Days 08-10 → 08-17 are already covered** by the cron with working Avoca classification — the backfill only needs to reach days before that.

### ⚠️ NEW ISSUE found in that same log — needs Taylor, not code
Roughly ten lines per run read:

```
[skip] call 62047157: 'Avoca' has no matching ST reason ID
```

Step 3c resolves each reason name to an ID from ServiceTitan's own Call Reason list (`get_call_reasons`). Every other name resolves fine — `Follow Up Call` (id=69), `Wrong Number / Hang Up / Spam` (id=70), `Vendor / Marketing` (id=72), `Requires Different Service` (id=74), `Maintenance` (id=57026985). Only **`Avoca` returns no ID**, which means the "Avoca" Call Reason is missing, renamed, or **deactivated** in ServiceTitan.

Consequence: the ~11 calls per run that map to the `"Avoca"` placeholder — the intentionally-ambiguous ones meant for Julie's manual review — **cannot be written at all**, so they never reach her queue and stay blank.

**Action (governance #1 — a human changes ST, not the classifier):** Taylor checks ServiceTitan → Settings → Operations → Call Reasons for "Avoca". If it's deactivated, reactivate it and the affected calls self-heal on the next run. If it was deliberately deleted, that's a real decision to make — the mapping table needs a different fallback bucket, and `avoca_mapping.py` + `rules.py` must be updated together.

### Remaining, in order
1. **Push the pending local change** (`push_to_github.command`) — the skip-filter fix must be on Railway before the next Monday cron, and the backfill below assumes it locally.
2. **Resolve the `"Avoca"` reason-ID issue above** — otherwise the backfill will hit the same `[skip]` wall for placeholder calls.
3. **Re-run 2026-08-08 write-back** — first real local test of Step 3c writing to ST:
   ```bash
   python3 run.py --start 2026-08-08 --end 2026-08-08 --write-back
   ```
   Calls with a real reason are skipped automatically; the 4 unclassified Avoca calls (`62011818`, `62011854`, `62012219`, `62015804`) get processed. Spot-check them in ServiceTitan afterward. Then **backfill the pre-cron gap** — 08-10 onward is already covered by the cron, so this only needs the earlier days:
   ```bash
   python3 run.py --start 2026-08-02 --end 2026-08-09 --write-back
   ```
   With the skip-filter fix, this also re-attempts any calls stuck on the `"Avoca"` placeholder from old runs — no `--no-skip` needed, and Julie's manual reclassifications are untouched (they no longer say "Avoca"). Consider going further back than 08-02 as well; every day before the Avoca work shipped has the same gap.
4. **Confirm the next Monday cron** still shows the healthy Step 3c signals above, plus the `[skip] ... has no matching ST reason ID` lines gone once step 2 is resolved.
5. If Avoca support ever fixes the `/transcript` endpoint (ticket pending), a richer classify-from-transcript path becomes possible — but the deterministic mapping is cheaper and already accurate, so don't rush back.

---

## 7. Known traps

- **`--end` is inclusive.** Step 1 compensates with `end_date + 1 day` (ServiceTitan's `createdBefore` is exclusive). Any *new* time window built inside the pipeline must cover the same span or it silently drops the last day — AND must be clamped to `now()` if the API rejects future timestamps. **Dialpad rejects them; Avoca doesn't** (verified 2026-08-10 — don't "helpfully" clamp the Avoca window, and don't remove the Dialpad clamp).
- **Avoca's `created_at` is stamped when the call ENDS, not when it starts.** `_build_avoca_call` computes `started_at = created_at - duration_seconds`; ST's `received_at` then lands ~36–120s earlier (ring/queue before Avoca answers), inside the linker's 180s window. Duration is still treated as connected time because Avoca's AI engages the instant it answers.
- **Avoca emits variable-precision fractional seconds** (e.g. `.42763`, 5 digits). Python 3.9's `fromisoformat` rejects those; 3.11 (Railway) accepts them — local and prod can silently disagree. `_parse_avoca_timestamp` normalizes to 6 digits; on genuine parse failure it returns a 1970 sentinel (never `now()`, which used to plant today's timestamp on ~20% of records).
- **Dialpad's list endpoint defaults to 25/page.** Always pass `limit`; watch for the truncation warning from `get_calls_in_window`. The silent 1250-call cap suppressed match rates for months.
- **`skip_already_classified` runs BEFORE the Avoca split.** Historically this meant calls tagged with the `"Avoca"` placeholder were filtered out before Step 3c could ever upgrade them. **Fixed 2026-08-19:** the filter now re-processes `"Avoca"`-labeled calls the same way it re-processes `"Missed Call"` — the placeholder is a review flag, not a real classification. This is idempotent (ambiguous enums map back to "Avoca"; anything Julie manually reclassified no longer says "Avoca" and is skipped). Don't revert it, and be cautious with `--no-skip` on write-back runs — that re-runs *every* call through Claude and CAN overwrite manual reclassifications.
- **`stats["written_back"]` uses `+=`, not `=`.** Both Step 3c and Step 5 write to ServiceTitan and both increment the same counters. Don't "simplify" these to plain assignment — Step 5 would erase Step 3c's count.
- **Dialpad `/call/{id}` is rate-limited to 10/min.** The enrichment loop sleeps 6s every 9 calls. Don't remove that.
- **`.command` files created programmatically lack the executable bit** and macOS refuses to run them on double-click. Fix: `chmod +x <file>`.
- **Outbound calls** match on `callee_phone`, not `caller_phone` — `caller_phone` is C&R's own line and would never match. Handled in `linker._customer_phone`.

---

## 8. Related C&R systems (context, not in this repo)

- **EGIA monthly KPI report** — `cr-monthly-egia-report` skill + 2 scheduled tasks + `cr-vital-kpis-egia` live artifact. Consumes these Call Reasons. Emails Julie if Avoca calls are left unclassified.
- **ServiceTitan "Is Lead?" flags** on Call Reasons are kept in sync with the EGIA classifier buckets (5 true / 11 false).
- **Julie Templin** does manual review of anything left tagged `"Avoca"`.
