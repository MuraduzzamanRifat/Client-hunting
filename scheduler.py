"""
Background Scheduler — Runs the pipeline automatically on a schedule.

Reads target niches and locations from a "Targets" Google Sheet tab,
so you can add/remove/edit targets from anywhere without touching code.

Targets sheet format:
  | Keyword      | Location              | Count | Active |
  | cafes        | Key West, Florida     | 20    | Yes    |
  | restaurants  | Miami, Florida        | 30    | Yes    |
  | barbershops  | Orlando, Florida      | 20    | No     |  ← skipped

Runs every SCHEDULE_HOURS (default 6).
"""

import os
import sys
import time
import threading
from datetime import datetime

SCHEDULE_HOURS = int(os.getenv("SCHEDULE_HOURS", "6"))
SCHEDULE_SEND_EMAILS = os.getenv("SCHEDULE_SEND_EMAILS", "false").lower() == "true"
TARGETS_TAB_NAME = "Targets"

# Column headers expected in the Targets tab
COL_KEYWORD = "Keyword"
COL_LOCATION = "Location"
COL_COUNT = "Count"
COL_ACTIVE = "Active"


def _setup_targets_tab(sheets_mgr) -> None:
    """Create the Targets tab if it doesn't exist, with example rows."""
    try:
        ws = sheets_mgr.sheet.worksheet(TARGETS_TAB_NAME)
        # Tab exists — check if it has headers
        if not ws.row_values(1):
            ws.update("A1", [[COL_KEYWORD, COL_LOCATION, COL_COUNT, COL_ACTIVE]])
            ws.update("A2", [["cafes", "Key West, Florida", "20", "Yes"]])
            ws.format("A1:D1", {"textFormat": {"bold": True}})
    except Exception:
        # Tab doesn't exist — create it
        ws = sheets_mgr.sheet.add_worksheet(title=TARGETS_TAB_NAME, rows=50, cols=4)
        ws.update("A1", [[COL_KEYWORD, COL_LOCATION, COL_COUNT, COL_ACTIVE]])
        ws.update("A2", [["cafes", "Key West, Florida", "20", "Yes"]])
        ws.format("A1:D1", {"textFormat": {"bold": True}})
        print(f"  [SCHEDULER] Created '{TARGETS_TAB_NAME}' tab with example row.")


def _read_targets(sheets_mgr) -> list[dict]:
    """Read active targets from the Targets tab."""
    try:
        ws = sheets_mgr.sheet.worksheet(TARGETS_TAB_NAME)
        records = ws.get_all_records()
        targets = []
        for row in records:
            active = str(row.get(COL_ACTIVE, "")).strip().lower()
            keyword = str(row.get(COL_KEYWORD, "")).strip()
            location = str(row.get(COL_LOCATION, "")).strip()
            count = int(row.get(COL_COUNT, 20) or 20)

            if active == "yes" and keyword and location:
                targets.append({"keyword": keyword, "location": location, "count": count})

        return targets
    except Exception as e:
        print(f"  [SCHEDULER] Failed to read targets: {e}")
        return []


def run_scheduled_pipeline():
    """Read targets from Google Sheet and run pipeline for each."""
    # Lazy imports to keep startup fast
    from src.scraper import fetch_leads, save_to_csv
    from src.email_finder import enrich_leads, save_enriched_csv
    from src.sheets_manager import SheetsManager
    from src.lead_scoring import update_sheet_scores
    from src.email_sender import run_outreach
    import config
    print(f"\n{'='*60}")
    print(f"  SCHEDULED RUN — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    # Connect to Sheets
    sheets = SheetsManager()
    if not sheets.authenticate():
        print("  [SCHEDULER] Google Sheets auth failed!")
        return
    sheets.open_or_create_sheet()

    # Ensure Targets tab exists
    _setup_targets_tab(sheets)

    # Read targets
    targets = _read_targets(sheets)
    if not targets:
        print("  [SCHEDULER] No active targets found in Targets tab. Add rows and set Active=Yes.")
        return

    print(f"  Active targets: {len(targets)}")
    for t in targets:
        print(f"    - '{t['keyword']}' in '{t['location']}' ({t['count']} leads)")

    # Run pipeline for each target
    total_scraped = 0
    total_emails = 0

    for i, target in enumerate(targets, 1):
        keyword = target["keyword"]
        location = target["location"]
        count = target["count"]

        try:
            print(f"\n--- [{i}/{len(targets)}] '{keyword}' in '{location}' ---")

            # Step 1: Scrape
            leads = fetch_leads(keyword, location, count)
            if not leads:
                print(f"  No leads found. Skipping.")
                continue
            csv_path = save_to_csv(leads, keyword, location)
            total_scraped += len(leads)

            # Step 2: Enrich
            enriched = enrich_leads(csv_path)
            enriched_path = save_enriched_csv(enriched)
            emails_found = sum(1 for l in enriched if l.get("Email"))
            total_emails += emails_found

            # Step 3: Upload (reuse same sheets connection)
            raw = sheets.load_csv(enriched_path)
            clean = sheets.clean_data(raw)
            sheets.upload_to_sheets(clean)

            # Step 4: Score
            update_sheet_scores(sheets)

            # Step 5: Send emails
            if SCHEDULE_SEND_EMAILS:
                run_outreach(sheets)

            print(f"  Done: {len(leads)} scraped, {emails_found} emails found")

        except Exception as e:
            print(f"  [ERROR] Failed for '{keyword}' in '{location}': {e}")
            continue

    print(f"\n{'='*60}")
    print(f"  SCHEDULED RUN COMPLETE")
    print(f"  Total scraped: {total_scraped} | Total emails: {total_emails}")
    print(f"  Next run in {SCHEDULE_HOURS} hours")
    print(f"{'='*60}")


def start_scheduler_loop():
    """Run the scheduler in an infinite loop."""
    print(f"[SCHEDULER] Starting — runs every {SCHEDULE_HOURS} hours")
    print(f"[SCHEDULER] Send emails: {SCHEDULE_SEND_EMAILS}")
    print(f"[SCHEDULER] Targets are read from the '{TARGETS_TAB_NAME}' tab in Google Sheets")

    while True:
        try:
            run_scheduled_pipeline()
        except Exception as e:
            print(f"[SCHEDULER] Unexpected error: {e}")
        sleep_seconds = SCHEDULE_HOURS * 3600
        print(f"\n[SCHEDULER] Sleeping {SCHEDULE_HOURS} hours until next run...")
        time.sleep(sleep_seconds)


def start_scheduler_thread():
    """Start the scheduler as a background thread (used by app.py)."""
    thread = threading.Thread(target=start_scheduler_loop, daemon=True)
    thread.start()
    print("[SCHEDULER] Background thread started.")


# ── Standalone entry point ───────────────────────────────────────────
if __name__ == "__main__":
    if "--once" in sys.argv:
        run_scheduled_pipeline()
    else:
        start_scheduler_loop()
