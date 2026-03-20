"""
Google Maps Lead Generation Pipeline — Main Orchestrator.

Runs the full pipeline in sequence:
  1. Scrape leads from Google Maps (SerpAPI)
  2. Enrich leads with email addresses
  3. Upload to Google Sheets CRM
  4. Score and prioritize leads
  5. Send personalized outreach emails

Usage:
  python main.py                  # full interactive pipeline
  python main.py --skip-email     # everything except sending
  python main.py --score-only     # just re-score existing sheet
"""

import argparse
import sys
import time
from datetime import datetime

from src.scraper import fetch_leads, save_to_csv
from src.email_finder import find_email_for_lead
from src.sheets_manager import SheetsManager
from src.lead_scoring import update_sheet_scores, score_all_leads
from src.telegram_bot import queue_email
from src.ai_personalizer import generate_personalized_email
from src.metrics import log_event, log_run
import config


def run_pipeline(keyword: str, location: str, num_results: int,
                 skip_email: bool = False, score_only: bool = False):
    """
    1:1 autonomous pipeline.
    Each lead is processed individually: email found -> send immediately.
    """
    start_time = time.time()
    stats = {"leads_scraped": 0, "emails_found": 0, "leads_uploaded": 0, "emails_sent": 0}

    print("\n" + "=" * 60)
    print("  GOOGLE MAPS LEAD GENERATION — 1:1 AUTONOMOUS")
    print(f"  Started : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Keyword : {keyword}")
    print(f"  Location: {location}")
    print(f"  Count   : {num_results}")
    print("=" * 60)

    # ── Sheets ────────────────────────────────────────────────────
    sheets = SheetsManager()
    if not sheets.authenticate():
        print("\n[ERROR] Google Sheets auth failed.")
        return stats
    sheets.open_or_create_sheet()

    if score_only:
        print("\n[MODE] Score-only — re-scoring existing sheet")
        update_sheet_scores(sheets)
        _print_summary(stats, start_time)
        return stats

    # ── Step 1: Scrape ────────────────────────────────────────────
    print("\n" + "-" * 40)
    print("  STEP 1  Scraping Google Maps")
    print("-" * 40)
    leads = fetch_leads(keyword, location, num_results)
    stats["leads_scraped"] = len(leads)
    if not leads:
        print("[!] No leads found. Stopping.")
        _print_summary(stats, start_time)
        return stats
    save_to_csv(leads, keyword, location)

    # ── Step 2: Telegram bot check ───────────────────────────────
    if not skip_email:
        print("\n" + "-" * 40)
        print("  STEP 2  Approval mode: Telegram")
        print("-" * 40)
        if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID:
            print(f"  Emails will be sent to Telegram for your approval before sending.")
        else:
            print("  [!] Telegram not configured — emails will be queued but not notified")

    # ── Step 3: Process each lead inline (1:1) ────────────────────
    print("\n" + "-" * 40)
    print("  STEP 3  Processing leads (collect -> send)")
    print("-" * 40)

    for i, lead in enumerate(leads, 1):
        name = lead.get("Name", "Unknown")
        website = lead.get("Website", "").strip()
        phone = lead.get("Phone", "").strip()
        facebook = lead.get("Facebook", "").strip()
        angle = "SEO" if website else "web creation"

        print(f"\n  [{i}/{len(leads)}] {name}")
        print(f"    Website : {website or '—'}  |  Phone: {phone or '—'}")

        # Find email
        email = find_email_for_lead(lead) if website else ""

        if email:
            lead["Email"] = email
            lead["Email Status"] = "Email Found"
            stats["emails_found"] += 1
            log_event("collected", recipient=email, details=name)
            print(f"    Email   : {email}  OK")

            # Score the lead
            scored = score_all_leads([lead])
            lead.update(scored[0])
            lead["Status"] = "New"

            # Upload to sheet (skip if duplicate)
            existing = sheets.read_leads()
            already_contacted = any(
                r.get("Email", "").lower() == email.lower()
                and str(r.get("Contacted", "")).lower() == "yes"
                for r in existing
            )
            if already_contacted:
                print(f"    Status  : Already contacted - skip")
                continue

            sheets.upload_to_sheets(sheets.clean_data([lead]))
            stats["leads_uploaded"] += 1

            if skip_email:
                print(f"    Status  : Saved (--skip-email)")
            else:
                # Find this lead's row index in the sheet
                all_leads = sheets.read_leads()
                sheet_row = 0
                for idx, r in enumerate(all_leads):
                    if r.get("Email", "").lower() == email.lower():
                        sheet_row = idx + 2
                        break

                # Queue for Telegram approval — email is NOT sent yet
                content = generate_personalized_email(lead)
                queue_email(
                    recipient=email,
                    subject=content["subject"],
                    body=content["body"],
                    lead_data=lead,
                    service=content.get("service", ""),
                    angle=content.get("angle", ""),
                    sheet_row=sheet_row,
                )
                stats["emails_sent"] += 1
                print(f"    Angle   : {angle}")
                print(f"    Status  : Queued — approve on Telegram to send")

        elif phone:
            lead["Email"] = ""
            lead["Email Status"] = "No Email Found"
            lead["Status"] = "New"
            lead["Outreach Type"] = "Call Queue"
            lead["Contact Method"] = "Phone"
            sheets.upload_to_sheets(sheets.clean_data([lead]))
            stats["leads_uploaded"] += 1
            print(f"    Status  : [PHONE] Call Queue ({phone})")

        elif not website and not facebook:
            print(f"    Status  : [SKIP]  Skipped (no contact data)")
            continue

        else:
            lead["Email"] = ""
            lead["Email Status"] = "No Email Found"
            lead["Status"] = "New"
            lead["Outreach Type"] = "Needs Review"
            sheets.upload_to_sheets(sheets.clean_data([lead]))
            stats["leads_uploaded"] += 1
            print(f"    Status  : [REVIEW] Needs Review (has site, no email found)")

    # ── Step 4: Score sheet ───────────────────────────────────────
    print("\n" + "-" * 40)
    print("  STEP 4  Scoring sheet")
    print("-" * 40)
    try:
        update_sheet_scores(sheets)
        print("  Done.")
    except Exception as e:
        print(f"  [!] Scoring error: {e}")

    log_run(keyword, location, stats["leads_scraped"], stats["emails_found"],
            "success", leads_uploaded=stats["leads_uploaded"])
    _print_summary(stats, start_time)
    return stats


def _print_summary(stats: dict, start_time: float):
    """Print end-of-run summary."""
    elapsed = time.time() - start_time
    minutes = int(elapsed // 60)
    seconds = int(elapsed % 60)

    print("\n" + "=" * 60)
    print("  PIPELINE COMPLETE")
    print("=" * 60)
    print(f"  Leads scraped  : {stats.get('leads_scraped', 0)}")
    print(f"  Emails found   : {stats.get('emails_found', 0)}")
    print(f"  Leads uploaded : {stats.get('leads_uploaded', 0)}")
    print(f"  Emails sent    : {stats.get('emails_sent', 0)}")
    print(f"  Duration       : {minutes}m {seconds}s")
    print("=" * 60 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Google Maps Lead Generation Pipeline")
    parser.add_argument("--skip-email", action="store_true", help="Run pipeline without sending emails")
    parser.add_argument("--score-only", action="store_true", help="Only re-score existing leads in sheet")
    parser.add_argument("--quality", action="store_true", help="Run data quality audit on existing sheet")
    parser.add_argument("--keyword", type=str, help="Search keyword (e.g., 'cafes')")
    parser.add_argument("--location", type=str, help="Location (e.g., 'Key West, Florida')")
    parser.add_argument("--count", type=int, help="Number of leads to fetch")
    args = parser.parse_args()

    # Quality-only mode
    if args.quality:
        from src.data_quality import run_quality_check, format_audit_report
        sheets = SheetsManager()
        if not sheets.authenticate():
            print("[ERROR] Google Sheets auth failed.")
            sys.exit(1)
        sheets.open_or_create_sheet()
        results = run_quality_check(sheets, verbose=True)
        print(format_audit_report(results))
        sys.exit(0)

    # Interactive input if not provided via CLI args
    if args.score_only:
        keyword, location, count = "", "", 0
    else:
        keyword = args.keyword or input("\n  Enter keyword (e.g., cafes): ").strip()
        location = args.location or input("  Enter location (e.g., Key West, Florida): ").strip()
        count = args.count or int(input("  Number of leads to fetch: ").strip() or "20")

        if not keyword or not location:
            print("[ERROR] Keyword and location are required.")
            sys.exit(1)

    run_pipeline(keyword, location, count,
                 skip_email=args.skip_email,
                 score_only=args.score_only)


if __name__ == "__main__":
    main()
