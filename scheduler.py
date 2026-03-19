"""
Background Scheduler — Runs the pipeline automatically on a schedule.

Can run standalone or alongside the Flask web dashboard.
Default: every 6 hours.
"""

import os
import sys
import time
import threading
from datetime import datetime

from src.scraper import fetch_leads, save_to_csv
from src.email_finder import enrich_leads, save_enriched_csv
from src.sheets_manager import SheetsManager
from src.lead_scoring import update_sheet_scores
from src.email_sender import run_outreach
import config

# ── Schedule config (from env or defaults) ───────────────────────────
SCHEDULE_HOURS = int(os.getenv("SCHEDULE_HOURS", "6"))
SCHEDULE_KEYWORD = os.getenv("SCHEDULE_KEYWORD", "")
SCHEDULE_LOCATION = os.getenv("SCHEDULE_LOCATION", "")
SCHEDULE_COUNT = int(os.getenv("SCHEDULE_COUNT", "20"))
SCHEDULE_SEND_EMAILS = os.getenv("SCHEDULE_SEND_EMAILS", "false").lower() == "true"

# Support multiple keyword/location pairs via comma-separated values
# e.g., SCHEDULE_KEYWORD="cafes,restaurants,barbershops"
#        SCHEDULE_LOCATION="Key West,Miami,Orlando"
KEYWORDS = [k.strip() for k in SCHEDULE_KEYWORD.split(",") if k.strip()]
LOCATIONS = [l.strip() for l in SCHEDULE_LOCATION.split(",") if l.strip()]


def run_scheduled_pipeline():
    """Run the pipeline for all keyword/location combinations."""
    if not KEYWORDS or not LOCATIONS:
        print("[SCHEDULER] No SCHEDULE_KEYWORD or SCHEDULE_LOCATION set in .env. Skipping.")
        return

    print(f"\n{'='*60}")
    print(f"  SCHEDULED RUN — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Keywords : {KEYWORDS}")
    print(f"  Locations: {LOCATIONS}")
    print(f"{'='*60}\n")

    for keyword in KEYWORDS:
        for location in LOCATIONS:
            try:
                print(f"\n--- Running: '{keyword}' in '{location}' ---")

                # Step 1: Scrape
                leads = fetch_leads(keyword, location, SCHEDULE_COUNT)
                if not leads:
                    print(f"  No leads for '{keyword}' in '{location}'. Skipping.")
                    continue
                csv_path = save_to_csv(leads, keyword, location)

                # Step 2: Enrich
                enriched = enrich_leads(csv_path)
                enriched_path = save_enriched_csv(enriched)

                # Step 3: Upload
                sheets = SheetsManager()
                if not sheets.authenticate():
                    print("  Sheets auth failed. Skipping.")
                    continue
                sheets.open_or_create_sheet()
                raw = sheets.load_csv(enriched_path)
                clean = sheets.clean_data(raw)
                sheets.upload_to_sheets(clean)

                # Step 4: Score
                update_sheet_scores(sheets)

                # Step 5: Send emails
                if SCHEDULE_SEND_EMAILS:
                    run_outreach(sheets)

                emails_found = sum(1 for l in enriched if l.get("Email"))
                print(f"  Done: {len(leads)} scraped, {emails_found} emails found")

            except Exception as e:
                print(f"  [ERROR] Pipeline failed for '{keyword}' in '{location}': {e}")
                continue

    print(f"\n  Scheduled run complete. Next run in {SCHEDULE_HOURS} hours.")


def start_scheduler_loop():
    """Run the scheduler in an infinite loop."""
    print(f"[SCHEDULER] Starting — runs every {SCHEDULE_HOURS} hours")
    print(f"[SCHEDULER] Keywords: {KEYWORDS}")
    print(f"[SCHEDULER] Locations: {LOCATIONS}")
    print(f"[SCHEDULER] Send emails: {SCHEDULE_SEND_EMAILS}")

    while True:
        run_scheduled_pipeline()
        sleep_seconds = SCHEDULE_HOURS * 3600
        print(f"\n[SCHEDULER] Sleeping {SCHEDULE_HOURS} hours until next run...")
        time.sleep(sleep_seconds)


def start_scheduler_thread():
    """Start the scheduler as a background thread (used by app.py)."""
    if not KEYWORDS or not LOCATIONS:
        print("[SCHEDULER] No keywords/locations configured. Scheduler not started.")
        return
    thread = threading.Thread(target=start_scheduler_loop, daemon=True)
    thread.start()
    print("[SCHEDULER] Background thread started.")


# ── Standalone entry point ───────────────────────────────────────────
if __name__ == "__main__":
    if "--once" in sys.argv:
        run_scheduled_pipeline()
    else:
        start_scheduler_loop()
