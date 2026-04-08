"""Fully automated pipeline — collect + send + follow-up.

Runs unattended. Logs to file. Never crashes. Retries on failure.

Usage:
    python auto.py              # Run once: collect + send
    python auto.py --loop       # Run every 6 hours forever
    python auto.py --collect    # Only collect
    python auto.py --send       # Only send (initial + follow-ups)
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
from database import get_stats, init_db
from collectors.facebook_collector import run_facebook_collector
from collectors.website_collector import run_website_collector
from sender import start_sender

LOOP_INTERVAL = 6 * 60 * 60  # 6 hours
MAX_RETRIES = 2


def setup_logging():
    """Log to both console and file with rotation."""
    logger = logging.getLogger("outreach")
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter('%(asctime)s [%(name)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

    # Console
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    # File (10MB max, keep 5 rotations)
    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    return logger


def run_with_retry(func, name, retries=MAX_RETRIES):
    """Run a function with retry on failure."""
    log = logging.getLogger("outreach")
    for attempt in range(retries + 1):
        try:
            result = func()
            return result
        except Exception as e:
            log.error(f"{name} failed (attempt {attempt+1}/{retries+1}): {e}")
            if attempt < retries:
                wait = 30 * (attempt + 1)
                log.info(f"Retrying {name} in {wait}s...")
                time.sleep(wait)
    log.error(f"{name} failed after all retries")
    return 0


def run_pipeline(collect=True, send=True):
    """Run the full pipeline."""
    log = logging.getLogger("outreach")
    log.info("=" * 50)
    log.info("Pipeline started")

    stats = get_stats()
    log.info(f"DB: {stats['total']} total | {stats['new']} unsent | {stats['sent']} sent | {stats['today_sent']} today | {stats['due_followup']} due follow-up")

    if collect:
        log.info("--- Facebook Collection ---")
        fb = run_with_retry(run_facebook_collector, "Facebook")
        log.info(f"Facebook: {fb} new emails")

        log.info("--- Website Collection (freelancer sites + agencies) ---")
        web = run_with_retry(run_website_collector, "Websites")
        log.info(f"Websites: {web} new emails")

    if send:
        log.info("--- Sending Emails ---")
        sent = run_with_retry(start_sender, "Sender")
        log.info(f"Sent: {sent} emails (initial + follow-ups)")

    stats = get_stats()
    log.info(f"Final: {stats['total']} total | {stats['new']} unsent | {stats['sent']} sent | {stats['today_sent']} today")
    log.info("Pipeline complete")
    log.info("=" * 50)


def main():
    parser = argparse.ArgumentParser(description="Email Outreach Auto Pipeline")
    parser.add_argument('--loop', action='store_true', help='Run every 6 hours forever')
    parser.add_argument('--collect', action='store_true', help='Only collect emails')
    parser.add_argument('--send', action='store_true', help='Only send emails')
    args = parser.parse_args()

    init_db()
    log = setup_logging()

    do_collect = args.collect or (not args.collect and not args.send)
    do_send = args.send or (not args.collect and not args.send)

    if args.loop:
        log.info(f"Auto-loop mode: running every {LOOP_INTERVAL // 3600} hours")
        log.info("Press Ctrl+C to stop\n")

        while True:
            try:
                run_pipeline(collect=do_collect, send=do_send)
            except KeyboardInterrupt:
                log.info("Stopped by user")
                break
            except Exception as e:
                log.error(f"Pipeline crashed: {e}")
                log.info("Will retry next cycle...")

            next_run = datetime.now().timestamp() + LOOP_INTERVAL
            next_time = datetime.fromtimestamp(next_run).strftime('%H:%M:%S')
            log.info(f"Next run at {next_time} ({LOOP_INTERVAL // 3600}h from now)\n")

            try:
                time.sleep(LOOP_INTERVAL)
            except KeyboardInterrupt:
                log.info("Stopped by user")
                break
    else:
        run_pipeline(collect=do_collect, send=do_send)


if __name__ == "__main__":
    main()
