"""Telegram Bot — control the entire outreach system from your phone.

Commands:
    /start   — Start 24/7 pipeline
    /stop    — Stop pipeline
    /status  — Current stats
    /collect — Run collection now
    /send    — Run sending now
    /check   — Check inbox for replies/bounces
    /help    — Show commands
"""

import sys
import os
import logging
import logging.handlers
import threading
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, LOG_FILE, DAILY_SEND_LIMIT
from database import get_stats, init_db
from collectors.website_collector import run_website_collector
from sender import start_sender
from tracker import check_inbox, get_tracking_stats
from sheets import SheetsManager
from notifier import send_telegram, notify_sheets_sync

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler(),
        logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8'),
    ]
)
log = logging.getLogger("outreach.bot")

# Pipeline state
pipeline_thread = None
pipeline_running = False
sheets_mgr = None


def is_authorized(update: Update) -> bool:
    """Only respond to your chat ID."""
    return str(update.effective_chat.id) == TELEGRAM_CHAT_ID


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    await update.message.reply_text(
        "📋 <b>Commands</b>\n\n"
        "/start — Start 24/7 pipeline (collect + send every 6h)\n"
        "/stop — Stop pipeline\n"
        "/status — Current stats + tracking\n"
        "/collect — Run collection now\n"
        "/send — Send emails now\n"
        "/check — Check inbox for replies/bounces\n"
        "/help — This message",
        parse_mode='HTML'
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    stats = get_stats()
    total_sent = stats['sent'] + stats['replied'] + stats['bounced']
    running = "🟢 Running" if pipeline_running else "🔴 Stopped"

    await update.message.reply_text(
        f"📊 <b>Status</b> — {running}\n\n"
        f"📨 Total collected: {stats['total']}\n"
        f"📭 Unsent: {stats['new']}\n"
        f"✅ Sent: {total_sent}\n"
        f"💬 Replies: {stats['replied']} ({stats['reply_rate']}%)\n"
        f"🔴 Bounced: {stats['bounced']} ({stats['bounce_rate']}%)\n"
        f"⏭ Skipped: {stats['skipped']}\n"
        f"📬 Today: {stats['today_sent']}/{DAILY_SEND_LIMIT}\n"
        f"⏳ Follow-ups due: {stats['due_followup']}",
        parse_mode='HTML'
    )


def run_pipeline_loop():
    """Background pipeline loop."""
    global pipeline_running, sheets_mgr
    interval = 6 * 60 * 60  # 6 hours

    if not sheets_mgr:
        sheets_mgr = SheetsManager()
        sheets_mgr.connect()

    send_telegram("🟢 <b>Pipeline Started</b>\nRunning 24/7, every 6 hours.")

    while pipeline_running:
        try:
            stats = get_stats()
            log.info(f"Pipeline cycle — DB: {stats['total']} total | {stats['new']} unsent | {stats['today_sent']} today")
            send_telegram(
                f"🔄 <b>Pipeline Cycle</b>\n"
                f"📊 {stats['total']} total | {stats['new']} unsent | {stats['today_sent']}/{DAILY_SEND_LIMIT} today"
            )

            # Collect
            log.info("Collecting...")
            web = 0
            try:
                web = run_website_collector()
                log.info(f"Collected {web} emails")
            except Exception as e:
                log.error(f"Collection error: {e}")
                send_telegram(f"⚠️ Collection error: {str(e)[:200]}")

            if web > 0:
                send_telegram(f"📥 Collected {web} new emails")

            # Sync to Sheets
            if sheets_mgr and sheets_mgr.ws:
                try:
                    from database import get_all_emails_for_sync
                    rows = get_all_emails_for_sync()
                    synced = sheets_mgr.sync_from_db(rows)
                    if synced > 0:
                        notify_sheets_sync(synced)
                except Exception as e:
                    log.warning(f"Sheets sync error: {e}")

            # Send
            log.info("Sending...")
            sent = 0
            try:
                sent = start_sender()
                log.info(f"Sent {sent} emails")
            except Exception as e:
                log.error(f"Sending error: {e}")
                send_telegram(f"⚠️ Sending error: {str(e)[:200]}")

            # Check inbox
            try:
                inbox = check_inbox()
                if inbox['replies'] > 0 or inbox['bounces'] > 0:
                    send_telegram(
                        f"📬 <b>Inbox</b>\n"
                        f"💬 Replies: {inbox['replies']} | 🔴 Bounces: {inbox['bounces']}"
                    )
            except Exception as e:
                log.warning(f"Inbox check error: {e}")

            # Summary
            stats = get_stats()
            total_sent = stats['sent'] + stats['replied'] + stats['bounced']
            send_telegram(
                f"✅ <b>Cycle Done</b>\n"
                f"📥 Collected: {web} | 📤 Sent: {sent}\n"
                f"📊 Total: {stats['total']} | Replies: {stats['replied']} ({stats['reply_rate']}%)\n"
                f"📬 Today: {stats['today_sent']}/{DAILY_SEND_LIMIT}"
            )

        except Exception as e:
            log.error(f"Pipeline error: {e}")
            send_telegram(f"⚠️ Pipeline error: {str(e)[:200]}")

        if not pipeline_running:
            break

        next_time = datetime.fromtimestamp(time.time() + interval).strftime('%H:%M')
        log.info(f"Next cycle at {next_time}")

        # Sleep in small chunks so /stop works fast
        for _ in range(interval // 10):
            if not pipeline_running:
                break
            time.sleep(10)

    send_telegram("🔴 <b>Pipeline Stopped</b>")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    global pipeline_thread, pipeline_running

    if pipeline_running:
        await update.message.reply_text("Already running. Use /stop first.")
        return

    pipeline_running = True
    pipeline_thread = threading.Thread(target=run_pipeline_loop, daemon=True)
    pipeline_thread.start()
    await update.message.reply_text("🟢 Pipeline started. You'll get updates here.")


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    global pipeline_running

    if not pipeline_running:
        await update.message.reply_text("Already stopped.")
        return

    pipeline_running = False
    await update.message.reply_text("🔴 Stopping pipeline... (may take a few seconds)")


async def cmd_collect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    await update.message.reply_text("📥 Starting collection...")

    def do_collect():
        try:
            count = run_website_collector()
            send_telegram(f"📥 Collection done: {count} new emails")
        except Exception as e:
            send_telegram(f"⚠️ Collection error: {str(e)[:200]}")

    threading.Thread(target=do_collect, daemon=True).start()


async def cmd_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    await update.message.reply_text("📤 Starting to send...")

    def do_send():
        try:
            sent = start_sender()
            stats = get_stats()
            send_telegram(
                f"📤 Sending done: {sent} emails\n"
                f"📬 Today: {stats['today_sent']}/{DAILY_SEND_LIMIT}"
            )
        except Exception as e:
            send_telegram(f"⚠️ Sending error: {str(e)[:200]}")

    threading.Thread(target=do_send, daemon=True).start()


async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    await update.message.reply_text("📬 Checking inbox...")

    def do_check():
        try:
            inbox = check_inbox()
            stats = get_stats()
            send_telegram(
                f"📬 <b>Inbox Check</b>\n"
                f"💬 New replies: {inbox['replies']}\n"
                f"🔴 New bounces: {inbox['bounces']}\n\n"
                f"📊 Total replies: {stats['replied']} ({stats['reply_rate']}%)\n"
                f"📊 Total bounced: {stats['bounced']} ({stats['bounce_rate']}%)"
            )
        except Exception as e:
            send_telegram(f"⚠️ Inbox check error: {str(e)[:200]}")

    threading.Thread(target=do_check, daemon=True).start()


def main():
    init_db()
    log.info("Starting Telegram bot...")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("collect", cmd_collect))
    app.add_handler(CommandHandler("send", cmd_send))
    app.add_handler(CommandHandler("check", cmd_check))
    app.add_handler(CommandHandler("help", cmd_help))

    send_telegram(
        "🤖 <b>Bot Online</b>\n\n"
        "Commands:\n"
        "/start — Start 24/7 pipeline\n"
        "/stop — Stop pipeline\n"
        "/status — Stats + tracking\n"
        "/collect — Collect now\n"
        "/send — Send now\n"
        "/check — Check replies/bounces\n"
        "/help — Help"
    )

    log.info("Bot running. Waiting for commands...")
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
