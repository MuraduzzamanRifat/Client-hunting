"""Local PC collector — run this on your PC to collect emails.

Collects via Playwright (real browser), syncs to Google Sheets.
Koyeb picks up new emails from Sheets and sends them.

Usage: python collect_local.py
"""

import sys
import os
import logging

sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(message)s')

from database import init_db, get_stats, get_all_emails_for_sync
from collectors.website_collector import run_website_collector
from sheets import SheetsManager
from notifier import send_telegram

init_db()


def main():
    send_telegram("📥 <b>PC Collection Started</b>")

    # Collect
    count = 0
    try:
        count = run_website_collector()
    except Exception as e:
        send_telegram(f"⚠️ Collection error: {str(e)[:200]}")

    send_telegram(f"📥 <b>Collected {count} new emails</b>")

    # Sync to Sheets
    sm = SheetsManager()
    if sm.connect():
        rows = get_all_emails_for_sync()
        synced = sm.sync_from_db(rows)
        if synced > 0:
            send_telegram(f"📋 Synced {synced} emails to Sheets")

    stats = get_stats()
    send_telegram(
        f"✅ <b>PC Collection Done</b>\n"
        f"📨 Total: {stats['total']} | New: {stats['new']} | Sent: {stats['sent']}"
    )


if __name__ == "__main__":
    main()
