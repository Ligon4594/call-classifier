#!/bin/bash
# ============================================================
# Fix the local AVOCA_API_KEY in .env   (v2 — tolerant paste)
# ============================================================
# Verified 2026-08-20: the RAILWAY copy of the key is alive (the
# 08-17 cron ran Step 3c clean and wrote 21 reasons to ST). Only
# the local .env copy is dead. This script updates it in place.
#
# Steps:
#   1. In Railway -> call-classifier -> Variables, click the
#      three-dot menu next to AVOCA_API_KEY and choose "Copy".
#   2. Double-click this file.
#   3. Paste at the prompt and hit Enter.
#
# v2 changes: no more strict format gate (that was a guess and it
# rejected a good paste). This version auto-strips a leading
# "AVOCA_API_KEY=", quotes, and stray whitespace; shows a MASKED
# description of what it got; and lets the real Avoca API decide.
# On any failure it restores your previous .env automatically.
# The key itself is never printed or logged.
# ============================================================

cd "$(dirname "$0")"

if [ ! -f .env ]; then
    echo "ERROR: .env not found next to this script."
    echo "Press any key to close..."; read -n 1 -s; exit 1
fi

echo "Paste the AVOCA_API_KEY value copied from Railway, then press Enter."
echo "(Nothing appears as you paste — input is hidden on purpose.)"
echo
read -r -s RAW
echo

# --- Clean it up -------------------------------------------------------
# Strip all whitespace/newlines first.
CLEANED="$(printf '%s' "$RAW" | tr -d '[:space:]')"

# Pull out the FIRST well-formed avoca key found anywhere in the paste.
# This transparently handles every mangling seen so far:
#   - Railway copying "AVOCA_API_KEY=avoca_..." (name AND value)
#   - the value pasted TWICE (hidden input shows nothing, so it's easy
#     to hit Cmd-V again thinking the first one didn't register — this
#     produced a 140-char doubled key on 2026-08-20)
#   - surrounding quotes, trailing newlines, stray characters
NEW_KEY="$(printf '%s' "$CLEANED" \
    | grep -oE 'avoca_[0-9a-fA-F]{64}' \
    | head -n 1)"

if [ -n "$NEW_KEY" ] && [ "${#CLEANED}" -ne "${#NEW_KEY}" ]; then
    echo "NOTE: the paste contained ${#CLEANED} characters; extracted the"
    echo "      well-formed ${#NEW_KEY}-character key from inside it."
    echo "      (Usually means it was pasted twice, or copied as name=value.)"
    echo
fi

# No well-formed key found — fall back to the cleaned blob, minus the
# obvious wrappers, and let the Avoca API be the judge.
if [ -z "$NEW_KEY" ]; then
    NEW_KEY="$CLEANED"
    NEW_KEY="${NEW_KEY#AVOCA_API_KEY=}"
    NEW_KEY="${NEW_KEY%\"}"; NEW_KEY="${NEW_KEY#\"}"
    NEW_KEY="${NEW_KEY%\'}"; NEW_KEY="${NEW_KEY#\'}"
    if [ -n "$NEW_KEY" ]; then
        echo "NOTE: couldn't find a standard 'avoca_' + 64-hex key in the paste."
        echo "      Trying the pasted value as-is; the test below will confirm."
        echo
    fi
fi

LEN=${#NEW_KEY}

if [ "$LEN" -eq 0 ]; then
    echo "ERROR: nothing was pasted (0 characters received)."
    echo "The clipboard may have been empty, or the paste didn't reach Terminal."
    echo "Try: click into the Terminal window first, then Cmd-V, then Enter."
    echo
    echo "Press any key to close..."; read -n 1 -s; exit 1
fi

# Masked description — safe to read aloud / paste back into chat.
echo "Received a key: ${LEN} characters, starts with '${NEW_KEY:0:6}', ends with '${NEW_KEY: -4}'."
if [ "$LEN" -lt 20 ]; then
    echo "NOTE: that's shorter than expected for an API key. Continuing anyway —"
    echo "      the connection test below is the real check."
fi
echo

# --- Write it ----------------------------------------------------------
cp .env .env.bak

NEW_KEY="$NEW_KEY" python3 - <<'PY'
import os, re
key = os.environ["NEW_KEY"]
with open(".env") as f:
    text = f.read()
new_text, n = re.subn(r"(?m)^AVOCA_API_KEY=.*$", "AVOCA_API_KEY=" + key, text)
if n == 0:
    new_text = text.rstrip("\n") + "\nAVOCA_API_KEY=" + key + "\n"
    print("  (no existing AVOCA_API_KEY line found — appended one)")
else:
    print(f"  (replaced {n} AVOCA_API_KEY line)")
with open(".env", "w") as f:
    f.write(new_text)
PY

echo
echo "Testing the connection to Avoca..."
echo "----------------------------------------------------------------"
if python3 test_avoca_connection.py; then
    echo "----------------------------------------------------------------"
    echo
    echo "SUCCESS — the local Avoca key works."
    echo "Your previous .env was backed up as .env.bak"
else
    echo "----------------------------------------------------------------"
    echo
    echo "Connection test FAILED — restoring your previous .env."
    mv .env.bak .env
    echo "Restored. Nothing was changed."
    echo
    echo "Copy the masked line above ('Received a key: ...') back into the"
    echo "chat — that tells Claude what shape the value was, without"
    echo "revealing the secret itself."
fi

echo
echo "Press any key to close..."
read -n 1 -s
