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


def _tg_notify(text: str):
    """Send a Telegram notification (best-effort)."""
    try:
        import requests as _req
        import config as _cfg
        if _cfg.TELEGRAM_BOT_TOKEN and _cfg.TELEGRAM_CHAT_ID:
            _req.post(
                f"https://api.telegram.org/bot{_cfg.TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": _cfg.TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
                timeout=10,
            )
    except Exception as e:
        print(f"  [SCHEDULER] Telegram notify failed: {e}")


def run_scheduled_pipeline():
    """Read targets from Google Sheet and run pipeline for each."""
    # Lazy imports to keep startup fast
    from src.scraper import fetch_leads, save_to_csv
    from src.sheets_manager import SheetsManager
    from src.lead_scoring import update_sheet_scores, score_all_leads
    from src.metrics import log_run
    import config

    run_start = datetime.now()
    print(f"\n{'='*60}")
    print(f"  SCHEDULED RUN — {run_start.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    # Connect to Sheets
    sheets = SheetsManager()
    if not sheets.authenticate():
        msg = "⚠️ <b>Scheduler</b>: Google Sheets auth failed — skipping run"
        print("  [SCHEDULER] Google Sheets auth failed!")
        _tg_notify(msg)
        log_run("(auth)", "(failed)", 0, 0, "error", "Google Sheets auth failed")
        return
    sheets.open_or_create_sheet()

    # Ensure Targets tab exists
    _setup_targets_tab(sheets)

    # Read targets
    targets = _read_targets(sheets)
    if not targets:
        print("  [SCHEDULER] No active targets found in Targets tab. Add rows and set Active=Yes.")
        _tg_notify(
            "ℹ️ <b>Scheduler</b>: No active targets in Targets sheet.\n"
            "Add rows with <code>Active = Yes</code> to start collecting leads."
        )
        return

    print(f"  Active targets: {len(targets)}")
    for t in targets:
        print(f"    - '{t['keyword']}' in '{t['location']}' ({t['count']} leads)")

    # Run pipeline for each target
    total_scraped = 0
    total_emails = 0
    run_results = []  # [(keyword, location, leads, emails, status)]

    # Open SMTP once for the entire batch
    from src.email_sender import open_smtp_connection, send_single_lead
    import os as _os
    smtp = open_smtp_connection()
    from_addr = _os.getenv("EMAIL_FROM") or _os.getenv("EMAIL_USER", "")
    if not smtp:
        print("  [SCHEDULER] WARNING: SMTP unavailable — leads saved but emails skipped")

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
                log_run(keyword, location, 0, 0, "no_results")
                run_results.append((keyword, location, 0, 0, "no_results"))
                continue

            total_scraped += len(leads)
            save_to_csv(leads, keyword, location)

            # Step 2: Process each lead inline (1:1 collect → send)
            from src.email_finder import find_email_for_lead
            from src.metrics import log_event
            import time as _time

            emails_found = 0
            uploaded = 0

            for lead in leads:
                name = lead.get("Name", "Unknown")
                website = lead.get("Website", "").strip()
                phone = lead.get("Phone", "").strip()
                facebook = lead.get("Facebook", "").strip()

                email = find_email_for_lead(lead) if website else ""

                if email:
                    lead["Email"] = email
                    lead["Email Status"] = "Email Found"
                    emails_found += 1
                    log_event("collected", recipient=email, details=name)

                    scored = score_all_leads([lead])
                    lead.update(scored[0])
                    lead["Status"] = "New"
                    sheets.upload_to_sheets(sheets.clean_data([lead]))
                    uploaded += 1

                    if smtp and from_addr:
                        _time.sleep(config.SEND_DELAY_AFTER_COLLECT)
                        success = send_single_lead(lead, smtp, from_addr)
                        if success:
                            all_leads = sheets.read_leads()
                            for idx, row in enumerate(all_leads):
                                if row.get("Email", "").lower() == email.lower():
                                    sheets.update_row(idx + 2, {
                                        "Status": "Email Sent",
                                        "Contacted": "Yes",
                                        "Outreach Type": "Email",
                                    })
                                    break
                            print(f"  Sent to {email}")

                elif phone:
                    lead["Email"] = ""
                    lead["Email Status"] = "No Email Found"
                    lead["Status"] = "New"
                    lead["Outreach Type"] = "Call Queue"
                    lead["Contact Method"] = "Phone"
                    sheets.upload_to_sheets(sheets.clean_data([lead]))
                    uploaded += 1

                elif not website and not facebook:
                    print(f"  Skipped {name} — no contact data")
                    continue

                else:
                    lead["Email"] = ""
                    lead["Email Status"] = "No Email Found"
                    lead["Status"] = "New"
                    lead["Outreach Type"] = "Needs Review"
                    sheets.upload_to_sheets(sheets.clean_data([lead]))
                    uploaded += 1

            # Step 3: Score sheet
            update_sheet_scores(sheets)
            total_emails += emails_found

            log_run(keyword, location, len(leads), emails_found, "success", leads_uploaded=uploaded)
            run_results.append((keyword, location, len(leads), emails_found, "success"))
            print(f"  Done: {len(leads)} scraped, {emails_found} emails found/sent")

        except Exception as e:
            err_msg = str(e)
            print(f"  [ERROR] Failed for '{keyword}' in '{location}': {err_msg}")
            log_run(keyword, location, 0, 0, "error", error=err_msg)
            run_results.append((keyword, location, 0, 0, f"error: {err_msg[:60]}"))
            continue

    # Close SMTP
    if smtp:
        try:
            smtp.quit()
        except Exception:
            pass

    duration = int((datetime.now() - run_start).total_seconds())

    print(f"\n{'='*60}")
    print(f"  SCHEDULED RUN COMPLETE")
    print(f"  Total scraped: {total_scraped} | Total emails: {total_emails}")
    print(f"  Next run in {SCHEDULE_HOURS} hours")
    print(f"{'='*60}")

    # Send Telegram run summary
    lines = [f"📋 <b>Scheduler Run Complete</b>"]
    lines.append(f"🕐 {run_start.strftime('%H:%M')} | ⏱ {duration}s | {len(targets)} target(s)\n")
    for kw, loc, scraped, emails, status in run_results:
        icon = "✅" if status == "success" else ("⚠️" if status == "no_results" else "❌")
        lines.append(f"{icon} <b>{kw}</b> / {loc}")
        if status == "success":
            lines.append(f"   Scraped: {scraped} | Emails: {emails}")
        else:
            lines.append(f"   {status}")
    lines.append(f"\n<b>Total:</b> {total_scraped} leads | {total_emails} emails")
    lines.append(f"⏭ Next run in {SCHEDULE_HOURS}h")
    _tg_notify("\n".join(lines))


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
