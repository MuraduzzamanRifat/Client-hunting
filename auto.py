"""Fully automated 24/7 pipeline — collect + send + sync + notify.

Runs forever. Logs to file. Never crashes. Retries on failure.
Syncs to Google Sheets. Sends Telegram updates.

Usage:
    python auto.py              # Run once: collect + send
    python auto.py --loop       # Run 24/7 (every 6 hours)
    python auto.py --collect    # Only collect
    python auto.py --send       # Only send
"""

import sys
import os
import time
import logging
import argparse
from logging.handlers import RotatingFileHandler
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

from config import LOG_FILE
from database import get_stats, get_all_emails_for_sync, init_db
from collectors.website_collector import run_website_collector
from sender import start_sender
from sheets import SheetsManager
from tracker import check_inbox, get_tracking_stats
from notifier import (
    send_telegram, notify_pipeline_start, notify_collection_done,
    notify_sending_done, notify_error, notify_sheets_sync,
    notify_tracking_stats, notify_inbox_check,
)

LOOP_INTERVAL = 6 * 60 * 60  # 6 hours
MAX_RETRIES = 2


def setup_logging():
    logger = logging.getLogger("outreach")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter('%(asctime)s [%(name)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    return logger


def run_with_retry(func, name, retries=MAX_RETRIES):
    log = logging.getLogger("outreach")
    for attempt in range(retries + 1):
        try:
            return func()
        except Exception as e:
            log.error(f"{name} failed (attempt {attempt+1}/{retries+1}): {e}")
            if attempt < retries:
                time.sleep(30 * (attempt + 1))
            else:
                notify_error(name, e)
    return 0


def sync_to_sheets(sheets_mgr):
    """Sync all emails from SQLite to Google Sheets."""
    log = logging.getLogger("outreach")
    try:
        if not sheets_mgr.ws:
            if not sheets_mgr.connect():
                return 0
        rows = get_all_emails_for_sync()
        synced = sheets_mgr.sync_from_db(rows)
        if synced > 0:
            notify_sheets_sync(synced)
        return synced
    except Exception as e:
        log.error(f"Sheets sync failed: {e}")
        return 0


def run_pipeline(collect=True, send=True, sheets_mgr=None):
    log = logging.getLogger("outreach")
    log.info("=" * 50)
    log.info("Pipeline started")

    stats = get_stats()
    log.info(f"DB: {stats['total']} total | {stats['new']} unsent | {stats['sent']} sent | {stats['today_sent']} today | {stats['due_followup']} follow-ups due")
    notify_pipeline_start(stats)

    if collect:
        log.info("--- Website Collection ---")
        web = run_with_retry(run_website_collector, "Websites")
        log.info(f"Websites: {web} new emails")
        notify_collection_done(web)

        # Sync to Sheets after collection
        if sheets_mgr and sheets_mgr.ws:
            sync_to_sheets(sheets_mgr)

    if send:
        log.info("--- Sending Emails ---")
        sent = run_with_retry(start_sender, "Sender")
        log.info(f"Sent: {sent} emails")

        # Update sheet statuses after sending
        if sheets_mgr and sheets_mgr.ws and sent > 0:
            sync_to_sheets(sheets_mgr)

        stats = get_stats()
        notify_sending_done(sent, stats)

    # --- Check inbox for replies/bounces ---
    log.info("--- Checking Inbox ---")
    try:
        inbox_stats = check_inbox()
        log.info(f"Replies: {inbox_stats['replies']}, Bounces: {inbox_stats['bounces']}")
        notify_inbox_check(inbox_stats)
    except Exception as e:
        log.warning(f"Inbox check failed: {e}")

    # --- Send tracking report ---
    tracking = get_tracking_stats()
    log.info(f"Tracking: {tracking['replied']} replies ({tracking['reply_rate']}%), "
             f"{tracking['bounced']} bounces ({tracking['bounce_rate']}%)")
    notify_tracking_stats(tracking)

    stats = get_stats()
    log.info(f"Final: {stats['total']} total | {stats['new']} unsent | {stats['sent']} sent | {stats['today_sent']} today")
    log.info("Pipeline complete")
    log.info("=" * 50)


def main():
    parser = argparse.ArgumentParser(description="Email Outreach 24/7 Pipeline")
    parser.add_argument('--loop', action='store_true', help='Run 24/7 (every 6 hours)')
    parser.add_argument('--collect', action='store_true', help='Only collect emails')
    parser.add_argument('--send', action='store_true', help='Only send emails')
    args = parser.parse_args()

    init_db()
    log = setup_logging()

    # Connect Google Sheets
    sheets_mgr = SheetsManager()
    if not sheets_mgr.connect():
        log.warning("Google Sheets not connected — will retry each cycle")

    do_collect = args.collect or (not args.collect and not args.send)
    do_send = args.send or (not args.collect and not args.send)

    if args.loop:
        log.info(f"24/7 mode: running every {LOOP_INTERVAL // 3600} hours")
        send_telegram("🟢 <b>Email Outreach Bot Started</b>\nRunning 24/7, every 6 hours.")

        while True:
            try:
                run_pipeline(collect=do_collect, send=do_send, sheets_mgr=sheets_mgr)
            except KeyboardInterrupt:
                try:
                    send_telegram("🔴 <b>Bot Stopped</b> (manual)")
                except Exception:
                    pass
                log.info("Stopped by user")
                break
            except Exception as e:
                log.error(f"Pipeline crashed: {e}")
                notify_error("Pipeline", e)

            next_time = datetime.fromtimestamp(time.time() + LOOP_INTERVAL).strftime('%H:%M')
            log.info(f"Next run at {next_time}\n")

            try:
                time.sleep(LOOP_INTERVAL)
            except KeyboardInterrupt:
                try:
                    send_telegram("🔴 <b>Bot Stopped</b> (manual)")
                except Exception:
                    pass
                break
    else:
        run_pipeline(collect=do_collect, send=do_send, sheets_mgr=sheets_mgr)


if __name__ == "__main__":
    main()
