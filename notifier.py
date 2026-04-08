"""Telegram notifications — keeps you posted on pipeline status."""

import logging
import requests

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

log = logging.getLogger("outreach.telegram")


def send_telegram(message):
    """Send a message to Telegram. Silent on failure."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        resp = requests.post(url, json={
            'chat_id': TELEGRAM_CHAT_ID,
            'text': message,
            'parse_mode': 'HTML',
        }, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        log.warning(f"Telegram send failed: {e}")
        return False


def notify_pipeline_start(stats):
    send_telegram(
        f"🚀 <b>Pipeline Started</b>\n"
        f"📊 DB: {stats['total']} total | {stats['new']} unsent | {stats['sent']} sent\n"
        f"📬 Today sent: {stats['today_sent']}/50"
    )


def notify_collection_done(web_count):
    send_telegram(
        f"📥 <b>Collection Done</b>\n"
        f"🌐 Websites: {web_count} new emails"
    )


def notify_sending_done(sent_count, stats):
    send_telegram(
        f"📤 <b>Sending Done</b>\n"
        f"✅ Sent: {sent_count} emails\n"
        f"📊 DB: {stats['total']} total | {stats['new']} unsent | {stats['sent']} sent\n"
        f"📬 Today: {stats['today_sent']}/50"
    )


def notify_error(phase, error):
    send_telegram(
        f"⚠️ <b>Error in {phase}</b>\n"
        f"{str(error)[:200]}"
    )


def notify_sheets_sync(synced_count):
    if synced_count > 0:
        send_telegram(f"📋 Synced {synced_count} emails to Google Sheets")


def notify_daily_summary(stats):
    send_telegram(
        f"📊 <b>Daily Summary</b>\n"
        f"Total: {stats['total']} | New: {stats['new']} | Sent: {stats['sent']}\n"
        f"Today sent: {stats['today_sent']}/50\n"
        f"Follow-ups due: {stats['due_followup']}"
    )
