#!/usr/bin/env python3
"""
C&R Call Classifier — Main entry point.

Usage:
  python run.py                        # Classify last 7 days, no write-back
  python run.py --days 3               # Classify last 3 days
  python run.py --start 2026-04-01 --end 2026-04-08
  python run.py --write-back           # Actually write to ServiceTitan
  python run.py --send-email           # Send the report via SMTP
  python run.py --dry-run              # Print prompts, don't call Claude

Designed to run as a Railway cron job or from Taylor's Mac.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

# Load .env if present (for local runs)
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            if key and val:
                os.environ.setdefault(key, val)

from src.classifier import Classifier
from src.dialpad import DialpadClient
from src.pipeline import run_pipeline
from src.reporter import render_html_report, render_text_report, send_report
from src.servicetitan import ServiceTitanClient


def main():
    parser = argparse.ArgumentParser(description="C&R Call Classifier Pipeline")
    parser.add_argument("--days", type=int, default=7, help="Number of days to look back (default: 7)")
    parser.add_argument("--start", type=str, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, help="End date (YYYY-MM-DD, exclusive)")
    parser.add_argument("--write-back", action="store_true", help="Write classifications to ServiceTitan")
    parser.add_argument("--send-email", action="store_true", help="Send the report via SMTP")
    parser.add_argument("--dry-run", action="store_true", help="Print prompts instead of calling Claude")
    parser.add_argument("--no-skip", action="store_true", help="Don't skip already-classified calls")
    parser.add_argument("--quiet", action="store_true", help="Suppress progress output")
    args = parser.parse_args()

    # Date range
    if args.start and args.end:
        start_date = date.fromisoformat(args.start)
        end_date = date.fromisoformat(args.end)
    else:
        end_date = date.today()
        start_date = end_date - timedelta(days=args.days)

    print(f"C&R Call Classifier — Processing {start_date} to {end_date}", file=sys.stderr)
    print(f"  write-back: {args.write_back}, send-email: {args.send_email}, dry-run: {args.dry_run}",
          file=sys.stderr)
    print(file=sys.stderr)

    # Initialize clients
    st_client = ServiceTitanClient()
    dp_client = DialpadClient()
    classifier = Classifier(mode="dry_run" if args.dry_run else "live")

    # Run the pipeline
    classifications, pipeline_stats = run_pipeline(
        start_date=start_date,
        end_date=end_date,
        st_client=st_client,
        dp_client=dp_client,
        classifier=classifier,
        write_back=args.write_back,
        skip_already_classified=not args.no_skip,
        verbose=not args.quiet,
    )

    # Generate report
    # NOTE: Using Resend sandbox (onboarding@resend.dev) which can only send
    # to the account owner's email. Once crhvacpro.com is verified in Resend,
    # add Julie back: "julietemplin@crhvacpro.com"
    recipients = [
        os.environ.get("REPORT_RECIPIENT", "tligon@crhvacpro.com"),
    ]

    mismatches = pipeline_stats.get("job_type_mismatches", [])

    text_report = render_text_report(
        start_date=start_date,
        end_date=end_date,
        classifications=classifications,
        total_st_calls=pipeline_stats.get("total_st_calls", 0),
        matched_dialpad=pipeline_stats.get("matched_dialpad", 0),
        written_back=pipeline_stats.get("written_back", 0),
        reason_field_updated=pipeline_stats.get("reason_field_updated", 0),
        job_type_mismatches=mismatches,
    )

    # Always print the text report to stdout
    print(text_report)

    # Send email if requested
    if args.send_email:
        html_report = render_html_report(
            start_date=start_date,
            end_date=end_date,
            classifications=classifications,
            total_st_calls=pipeline_stats.get("total_st_calls", 0),
            matched_dialpad=pipeline_stats.get("matched_dialpad", 0),
            written_back=pipeline_stats.get("written_back", 0),
            reason_field_updated=pipeline_stats.get("reason_field_updated", 0),
            job_type_mismatches=mismatches,
        )
        try:
            send_report(
                subject=f"C&R Call Classification Report — {start_date} to {end_date}",
                text_body=text_report,
                html_body=html_report,
                recipients=recipients,
            )
            print(f"\nReport emailed to: {', '.join(recipients)}", file=sys.stderr)
        except Exception as e:
            print(f"\nFailed to send email: {e}", file=sys.stderr)
            print("(Report was still printed above — you can forward it manually.)", file=sys.stderr)


if __name__ == "__main__":
    main()
