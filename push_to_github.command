#!/bin/bash
# One-time setup: initialize git repo and push to GitHub.
# Double-click this file to run it.

cd "$(dirname "$0")"

echo "=== Pushing C&R Call Classifier to GitHub ==="
echo ""

# Initialize git if needed
if [ ! -d .git ]; then
    echo "Initializing git repository..."
    git init
    git branch -M main
fi

# Add the remote (ignore error if already exists)
git remote add origin https://github.com/Ligon4594/call-classifier.git 2>/dev/null

# Stage all files (respecting .gitignore)
echo "Staging files..."
git add -A

echo ""
echo "Files that will be pushed:"
git status --short
echo ""

# Verify .env is NOT included
if git status --short | grep -q "\.env$"; then
    echo "WARNING: .env would be committed! Aborting."
    read -p "Press Enter to close..."
    exit 1
fi

echo "Looks good — no secrets in the commit."
echo ""

# Commit
git commit -m "Initial commit: C&R Call Classifier pipeline

- ServiceTitan API client (call pull + write-back)
- Dialpad API client (transcript/recap + operator resolution)
- Claude Haiku classifier (15 Call Reasons + 15 Job Types)
- Batch linker (ST ↔ Dialpad matching by phone + timestamp)
- Pipeline orchestrator (end-to-end flow)
- Weekly email reporter via Resend
- Railway deployment config (Monday 7 AM CDT cron)
"

# Push
echo ""
echo "Pushing to GitHub..."
echo "(You may be prompted to log in to GitHub — use your browser to authenticate)"
echo ""
git push -u origin main

echo ""
echo "=== Done! ==="
echo "Your code is now at: https://github.com/Ligon4594/call-classifier"
echo ""
read -p "Press Enter to close..."
