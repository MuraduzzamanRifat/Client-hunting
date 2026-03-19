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
from src.email_finder import enrich_leads, save_enriched_csv
from src.sheets_manager import SheetsManager
from src.lead_scoring import update_sheet_scores
from src.email_sender import run_outreach


def run_pipeline(keyword: str, location: str, num_results: int,
                 skip_email: bool = False, score_only: bool = False):
    """Execute the full lead generation pipeline."""

    start_time = time.time()
    stats = {
        "leads_scraped": 0,
        "emails_found": 0,
        "leads_uploaded": 0,
        "emails_sent": 0,
    }

    print("\n" + "=" * 60)
    print("  GOOGLE MAPS LEAD GENERATION PIPELINE")
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # ── Initialize Google Sheets ─────────────────────────────────
    sheets = SheetsManager()
    if not sheets.authenticate():
        print("\n[ERROR] Google Sheets auth failed. Cannot continue.")
        return stats

    sheets.open_or_create_sheet()

    # ── Score-only mode ──────────────────────────────────────────
    if score_only:
        print("\n[MODE] Score-only — re-scoring existing leads")
        update_sheet_scores(sheets)
        _print_summary(stats, start_time)
        return stats

    # ── Step 1: Scrape Leads ─────────────────────────────────────
    print("\n" + "-" * 40)
    print("  STEP 1: Scraping Google Maps")
    print("-" * 40)

    csv_path = ""
    try:
        leads = fetch_leads(keyword, location, num_results)
        stats["leads_scraped"] = len(leads)
        if leads:
            csv_path = save_to_csv(leads, keyword, location)
    except Exception as e:
        print(f"\n[ERROR] Scraping failed: {e}")
        print("  Continuing to next step...")

    if not csv_path:
        print("\n[!] No leads scraped. Pipeline cannot continue.")
        _print_summary(stats, start_time)
        return stats

    # ── Step 2: Enrich with Emails ───────────────────────────────
    print("\n" + "-" * 40)
    print("  STEP 2: Email Enrichment")
    print("-" * 40)

    enriched_path = ""
    try:
        enriched = enrich_leads(csv_path)
        stats["emails_found"] = sum(1 for l in enriched if l.get("Email"))
        if enriched:
            enriched_path = save_enriched_csv(enriched)
    except Exception as e:
        print(f"\n[ERROR] Enrichment failed: {e}")
        print("  Continuing with un-enriched data...")
        enriched_path = csv_path  # Fall back to raw CSV

    # ── Step 3: Upload to Google Sheets ──────────────────────────
    print("\n" + "-" * 40)
    print("  STEP 3: Google Sheets Upload")
    print("-" * 40)

    try:
        raw = sheets.load_csv(enriched_path or csv_path)
        clean = sheets.clean_data(raw)
        sheets.upload_to_sheets(clean)
        stats["leads_uploaded"] = len(clean)
    except Exception as e:
        print(f"\n[ERROR] Sheet upload failed: {e}")
        print("  Continuing to next step...")

    # ── Step 4: Score & Prioritize ───────────────────────────────
    print("\n" + "-" * 40)
    print("  STEP 4: Lead Scoring")
    print("-" * 40)

    try:
        update_sheet_scores(sheets)
    except Exception as e:
        print(f"\n[ERROR] Scoring failed: {e}")

    # ── Step 5: Email Outreach ───────────────────────────────────
    if skip_email:
        print("\n  [SKIPPED] Email outreach (--skip-email flag)")
    else:
        print("\n" + "-" * 40)
        print("  STEP 5: Email Outreach")
        print("-" * 40)

        try:
            result = run_outreach(sheets)
            stats["emails_sent"] = result.get("sent", 0)
        except Exception as e:
            print(f"\n[ERROR] Outreach failed: {e}")

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
    parser.add_argument("--keyword", type=str, help="Search keyword (e.g., 'cafes')")
    parser.add_argument("--location", type=str, help="Location (e.g., 'Key West, Florida')")
    parser.add_argument("--count", type=int, help="Number of leads to fetch")
    args = parser.parse_args()

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
