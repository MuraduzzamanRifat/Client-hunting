"""Unified Telegram Bot — Controls both LeadGen + Email Outreach.

One bot, one chat, two systems.

Outreach commands:
    /outreach   — Start outreach pipeline (collect + send every 6h)
    /ostop      — Stop outreach pipeline
    /ostatus    — Outreach stats + tracking
    /collect    — Collect emails now
    /send       — Send outreach emails now
    /check      — Check inbox for replies/bounces

LeadGen commands (from old gmaps system):
    /pending    — Pending email approvals
    /stats      — LeadGen daily summary
    /followup   — Follow-up status
    /status     — System health
    /pause      — Pause LeadGen sending
    /resume     — Resume LeadGen sending
    /automode   — Toggle auto-approve

General:
    /help       — Show all commands
"""

import sys
import os
import json
import time
import threading
import logging
import logging.handlers
import requests as req
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, LOG_FILE, DAILY_SEND_LIMIT
from database import get_stats, init_db, get_all_emails_for_sync
from collectors.website_collector import run_website_collector
from sender import start_sender
from tracker import check_inbox
from sheets import SheetsManager
from notifier import send_telegram, notify_sheets_sync

# Logging
log = logging.getLogger("outreach.bot")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler(),
        logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8'),
    ]
)

# --- Telegram API (raw, no library needed) ---
API_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


def tg_send(text, reply_markup=None):
    """Send message to Telegram."""
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        req.post(f"{API_BASE}/sendMessage", json=payload, timeout=10)
    except Exception as e:
        log.warning(f"Telegram send error: {e}")


def tg_edit(msg_id, text, reply_markup=None):
    payload = {"chat_id": TELEGRAM_CHAT_ID, "message_id": msg_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        req.post(f"{API_BASE}/editMessageText", json=payload, timeout=10)
    except Exception:
        pass


def tg_answer_callback(callback_id, text=""):
    try:
        req.post(f"{API_BASE}/answerCallbackQuery",
                 json={"callback_query_id": callback_id, "text": text}, timeout=5)
    except Exception:
        pass


# --- Outreach Pipeline State ---
outreach_running = False
outreach_thread = None
sheets_mgr = None


def outreach_loop():
    """Background outreach pipeline loop."""
    global outreach_running, sheets_mgr
    interval = 6 * 60 * 60

    if not sheets_mgr:
        sheets_mgr = SheetsManager()
        sheets_mgr.connect()

    tg_send("🟢 <b>Outreach Pipeline Started</b>\nCollect + send every 6 hours.")

    while outreach_running:
        try:
            stats = get_stats()
            tg_send(
                f"🔄 <b>Outreach Cycle</b>\n"
                f"📊 {stats['total']} total | {stats['new']} unsent | {stats['today_sent']}/{DAILY_SEND_LIMIT} today"
            )

            # Collect
            web = 0
            try:
                web = run_website_collector()
            except Exception as e:
                tg_send(f"⚠️ Collection error: {str(e)[:200]}")

            if web > 0:
                tg_send(f"📥 Collected {web} new emails")

            # Sync to Sheets
            if sheets_mgr and sheets_mgr.ws:
                try:
                    rows = get_all_emails_for_sync()
                    synced = sheets_mgr.sync_from_db(rows)
                    if synced > 0:
                        notify_sheets_sync(synced)
                except Exception:
                    pass

            # Send
            sent = 0
            try:
                sent = start_sender()
            except Exception as e:
                tg_send(f"⚠️ Sending error: {str(e)[:200]}")

            # Check inbox
            try:
                inbox = check_inbox()
                if inbox['replies'] > 0 or inbox['bounces'] > 0:
                    tg_send(f"📬 Replies: {inbox['replies']} | Bounces: {inbox['bounces']}")
            except Exception:
                pass

            # Summary
            stats = get_stats()
            total_sent = stats['sent'] + stats['replied'] + stats['bounced']
            tg_send(
                f"✅ <b>Outreach Cycle Done</b>\n"
                f"📥 Collected: {web} | 📤 Sent: {sent}\n"
                f"📊 Total: {stats['total']} | Replies: {stats['replied']} ({stats['reply_rate']}%)\n"
                f"📬 Today: {stats['today_sent']}/{DAILY_SEND_LIMIT}"
            )

        except Exception as e:
            tg_send(f"⚠️ Pipeline error: {str(e)[:200]}")

        if not outreach_running:
            break

        # Sleep in chunks so /ostop works fast
        for _ in range(interval // 10):
            if not outreach_running:
                break
            time.sleep(10)

    tg_send("🔴 <b>Outreach Pipeline Stopped</b>")


# --- LeadGen Integration (import old system) ---

def try_import_leadgen():
    """Try to import old LeadGen system. Returns True if available."""
    try:
        leadgen_path = os.path.join(os.path.dirname(__file__), '..', 'gmaps-lead-gen')
        leadgen_path = os.path.abspath(leadgen_path)
        if os.path.exists(leadgen_path):
            sys.path.insert(0, leadgen_path)
            return True
    except Exception:
        pass
    return False


LEADGEN_AVAILABLE = try_import_leadgen()


def handle_leadgen_command(text):
    """Route LeadGen commands to old system."""
    if not LEADGEN_AVAILABLE:
        return "LeadGen system not found."

    try:
        if text == "/pending":
            from src.telegram_bot import get_pending_count
            count = get_pending_count()
            return f"<b>Pending approvals:</b> {count}"

        elif text == "/stats":
            from src.telegram_bot import get_daily_summary
            summary = get_daily_summary()
            total = sum(summary.values())
            return (
                f"<b>LeadGen Today</b>\n\n"
                f"Total: {total}\n"
                f"Approved: {summary.get('approved', 0)}\n"
                f"Rejected: {summary.get('rejected', 0)}\n"
                f"Pending: {summary.get('pending', 0)}"
            )

        elif text == "/followup":
            from src.followup import get_followup_summary, get_due_followups
            summary = get_followup_summary()
            due = get_due_followups()
            return (
                f"<b>Follow-Up Status</b>\n\n"
                f"Active: {summary.get('active', 0)}\n"
                f"Replied: {summary.get('replied', 0)}\n"
                f"Stopped: {summary.get('stopped', 0)}\n"
                f"Bounced: {summary.get('bounced', 0)}\n\n"
                f"Due now: {len(due)} follow-ups"
            )

        elif text in ("/status", "/resume", "/pause", "/report"):
            from src.watchdog import handle_watchdog_command
            return handle_watchdog_command(text)

        elif text == "/automode":
            import config as lg_config
            current = lg_config.APPROVAL_MODE
            new_mode = "auto" if current == "telegram" else "telegram"
            lg_config.APPROVAL_MODE = new_mode
            return (
                f"Approval mode: <b>{new_mode}</b>\n"
                f"{'Emails sent without approval.' if new_mode == 'auto' else 'All emails require approval.'}"
            )

    except Exception as e:
        return f"LeadGen error: {str(e)[:200]}"

    return None


def handle_leadgen_callback(callback):
    """Route LeadGen button presses (approve/reject/edit/regen)."""
    if not LEADGEN_AVAILABLE:
        return

    try:
        from src.telegram_bot import approve_email, reject_email, regenerate_email, _edit_state

        data = callback.get("data", "")
        callback_id = callback.get("id", "")
        chat_id = str(callback.get("message", {}).get("chat", {}).get("id", ""))
        msg_id = str(callback.get("message", {}).get("message_id", ""))

        parts = data.split("_", 1)
        if len(parts) != 2:
            return

        action, queue_id_str = parts
        try:
            queue_id = int(queue_id_str)
        except ValueError:
            return

        if action == "approve":
            tg_answer_callback(callback_id, "Sending email...")
            success = approve_email(queue_id)
            status_text = "Sent" if success else "Failed"
            tg_edit(msg_id, f"#{queue_id} — <b>{status_text}</b>")

        elif action == "reject":
            tg_answer_callback(callback_id, "Rejected")
            reject_email(queue_id)
            tg_edit(msg_id, f"#{queue_id} — <b>Rejected</b>")

        elif action == "edit":
            tg_answer_callback(callback_id, "Send new subject...")
            _edit_state[chat_id] = {"queue_id": queue_id, "step": "subject", "msg_id": msg_id}
            tg_send(f"Editing #{queue_id}\n\nSend new <b>subject line</b>:\n(or /skip to keep)")

        elif action == "regen":
            tg_answer_callback(callback_id, "Regenerating...")
            tg_edit(msg_id, f"#{queue_id} — <b>Regenerating...</b>")
            regenerate_email(queue_id)

    except Exception as e:
        log.warning(f"LeadGen callback error: {e}")


# --- Edit state for LeadGen ---
_edit_state = {}


def handle_leadgen_edit(chat_id, text):
    """Handle edit flow for LeadGen emails."""
    if chat_id not in _edit_state:
        return False

    try:
        from src.telegram_bot import update_email, _get_db

        state = _edit_state[chat_id]
        queue_id = state["queue_id"]

        if state["step"] == "subject":
            if text != "/skip":
                update_email(queue_id, subject=text)
            _edit_state[chat_id]["step"] = "body"
            tg_send("Now send <b>new body</b>:\n(or /skip to keep)")
            return True

        elif state["step"] == "body":
            if text != "/skip":
                update_email(queue_id, body=text)
            del _edit_state[chat_id]

            conn = _get_db()
            row = conn.execute("SELECT * FROM approval_queue WHERE id = ?", (queue_id,)).fetchone()
            conn.close()

            if row:
                buttons = {"inline_keyboard": [[
                    {"text": "Approve", "callback_data": f"approve_{queue_id}"},
                    {"text": "Reject", "callback_data": f"reject_{queue_id}"},
                ]]}
                tg_send(
                    f"<b>Updated #{queue_id}</b>\n\n"
                    f"<b>Subject:</b> {row['subject']}\n\n"
                    f"<pre>{row['body'][:500]}</pre>",
                    reply_markup=buttons
                )
            return True

    except Exception as e:
        log.warning(f"LeadGen edit error: {e}")

    return False


# --- Unified Message Handler ---

def handle_message(msg):
    """Handle all text messages and commands."""
    chat_id = str(msg.get("chat", {}).get("id", ""))
    text = msg.get("text", "").strip()

    if chat_id != TELEGRAM_CHAT_ID:
        return

    # LeadGen edit mode
    if handle_leadgen_edit(chat_id, text):
        return

    # --- Outreach Commands ---
    global outreach_running, outreach_thread

    if text == "/outreach":
        if outreach_running:
            tg_send("Already running. Use /ostop first.")
            return
        outreach_running = True
        outreach_thread = threading.Thread(target=outreach_loop, daemon=True)
        outreach_thread.start()
        tg_send("🟢 Outreach pipeline starting...")

    elif text == "/ostop":
        if not outreach_running:
            tg_send("Already stopped.")
            return
        outreach_running = False
        tg_send("🔴 Stopping outreach...")

    elif text == "/ostatus":
        stats = get_stats()
        total_sent = stats['sent'] + stats['replied'] + stats['bounced']
        running = "🟢 Running" if outreach_running else "🔴 Stopped"
        tg_send(
            f"📊 <b>Outreach</b> — {running}\n\n"
            f"📨 Collected: {stats['total']}\n"
            f"📭 Unsent: {stats['new']}\n"
            f"✅ Sent: {total_sent}\n"
            f"💬 Replies: {stats['replied']} ({stats['reply_rate']}%)\n"
            f"🔴 Bounced: {stats['bounced']} ({stats['bounce_rate']}%)\n"
            f"📬 Today: {stats['today_sent']}/{DAILY_SEND_LIMIT}\n"
            f"⏳ Follow-ups: {stats['due_followup']}"
        )

    elif text == "/collect":
        tg_send("📥 Collecting...")
        def do_collect():
            try:
                count = run_website_collector()
                tg_send(f"📥 Done: {count} new emails")
            except Exception as e:
                tg_send(f"⚠️ Error: {str(e)[:200]}")
        threading.Thread(target=do_collect, daemon=True).start()

    elif text == "/send":
        tg_send("📤 Sending...")
        def do_send():
            try:
                sent = start_sender()
                stats = get_stats()
                tg_send(f"📤 Done: {sent} sent | Today: {stats['today_sent']}/{DAILY_SEND_LIMIT}")
            except Exception as e:
                tg_send(f"⚠️ Error: {str(e)[:200]}")
        threading.Thread(target=do_send, daemon=True).start()

    elif text == "/check":
        tg_send("📬 Checking inbox...")
        def do_check():
            try:
                inbox = check_inbox()
                stats = get_stats()
                tg_send(
                    f"📬 <b>Inbox</b>\n"
                    f"💬 New replies: {inbox['replies']}\n"
                    f"🔴 New bounces: {inbox['bounces']}\n\n"
                    f"Total replies: {stats['replied']} ({stats['reply_rate']}%)\n"
                    f"Total bounced: {stats['bounced']} ({stats['bounce_rate']}%)"
                )
            except Exception as e:
                tg_send(f"⚠️ Error: {str(e)[:200]}")
        threading.Thread(target=do_check, daemon=True).start()

    # --- LeadGen Commands ---
    elif text in ("/pending", "/stats", "/followup", "/status", "/pause", "/resume", "/report", "/automode"):
        response = handle_leadgen_command(text)
        if response:
            tg_send(response)

    # --- Help ---
    elif text in ("/help", "/start"):
        lg_status = "✅" if LEADGEN_AVAILABLE else "❌ not found"
        tg_send(
            f"🤖 <b>Unified Bot</b>\n\n"
            f"<b>— Outreach —</b>\n"
            f"/outreach — Start 24/7 pipeline\n"
            f"/ostop — Stop pipeline\n"
            f"/ostatus — Stats + tracking\n"
            f"/collect — Collect now\n"
            f"/send — Send now\n"
            f"/check — Check replies/bounces\n\n"
            f"<b>— LeadGen {lg_status} —</b>\n"
            f"/pending — Pending approvals\n"
            f"/stats — Daily summary\n"
            f"/followup — Follow-up status\n"
            f"/status — System health\n"
            f"/pause — Pause sending\n"
            f"/resume — Resume sending\n"
            f"/automode — Toggle auto-approve"
        )


def handle_callback(callback):
    """Handle button presses (LeadGen approve/reject/edit/regen)."""
    handle_leadgen_callback(callback)


# --- Polling Loop ---

def poll_loop():
    """Main Telegram polling loop."""
    offset = 0
    while True:
        try:
            resp = req.get(f"{API_BASE}/getUpdates",
                          params={"offset": offset, "timeout": 30}, timeout=35)
            data = resp.json()
            if not data.get("ok"):
                continue

            for update in data.get("result", []):
                offset = update["update_id"] + 1

                if "callback_query" in update:
                    handle_callback(update["callback_query"])
                elif "message" in update:
                    handle_message(update["message"])

        except req.exceptions.Timeout:
            pass
        except Exception as e:
            log.warning(f"Poll error: {e}")
            time.sleep(5)


def main():
    init_db()
    log.info("Starting unified bot...")

    tg_send(
        "🤖 <b>Bot Online</b>\n\n"
        "Type /help to see all commands.\n"
        "Type /outreach to start email pipeline."
    )

    log.info("Bot running. Waiting for commands...")
    poll_loop()


if __name__ == "__main__":
    main()
