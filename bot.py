"""LeadOutreach Bot — One bot, one system, everything on Telegram.

Commands:
    /start    — Start pipeline (collect + send every 3h)
    /stop     — Stop pipeline
    /status   — Full stats + tracking
    /collect  — Collect emails now
    /send     — Send emails now
    /check    — Check inbox (replies/bounces)
    /pending  — Pending LeadGen approvals
    /help     — Commands
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

# On Koyeb: config.py doesn't exist (gitignored), use config.example.py (reads env vars)
_dir = os.path.dirname(__file__)
if not os.path.exists(os.path.join(_dir, "config.py")):
    import shutil
    shutil.copy(os.path.join(_dir, "config.example.py"), os.path.join(_dir, "config.py"))

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, LOG_FILE, DAILY_SEND_LIMIT
from database import get_stats, init_db, get_all_emails_for_sync, get_unsent_emails
from collectors.website_collector import run_website_collector
from sender import start_sender
from tracker import check_inbox
from sheets import SheetsManager
from notifier import notify_sheets_sync

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

API_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
INTERVAL = 3 * 60 * 60  # 3 hours

# State
pipeline_running = False
pipeline_thread = None
sheets_mgr = None
_busy = threading.Lock()  # Prevents overlapping collect/send


def tg(text, reply_markup=None):
    """Send message. Returns message_id for later editing."""
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        resp = req.post(f"{API_BASE}/sendMessage", json=payload, timeout=10)
        data = resp.json()
        if data.get("ok"):
            return data["result"]["message_id"]
    except Exception as e:
        log.warning(f"TG send error: {e}")
    return None


def tg_edit(msg_id, text, reply_markup=None):
    if not msg_id:
        return
    payload = {"chat_id": TELEGRAM_CHAT_ID, "message_id": msg_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        req.post(f"{API_BASE}/editMessageText", json=payload, timeout=10)
    except Exception:
        pass


def progress_bar(current, total, width=15):
    """Generate text progress bar: [████░░░░░░] 40%"""
    if total <= 0:
        return "[" + "░" * width + "] 0%"
    pct = min(current / total, 1.0)
    filled = int(width * pct)
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {int(pct * 100)}%"


def tg_answer(callback_id, text=""):
    try:
        req.post(f"{API_BASE}/answerCallbackQuery",
                 json={"callback_query_id": callback_id, "text": text}, timeout=5)
    except Exception:
        pass


def format_stats():
    """Shared stats formatter."""
    stats = get_stats()
    total_sent = stats['sent'] + stats['replied'] + stats['bounced']
    return stats, (
        f"📨 Collected: {stats['total']}\n"
        f"📭 Unsent: {stats['new']}\n"
        f"✅ Sent: {total_sent}\n"
        f"💬 Replies: {stats['replied']} ({stats['reply_rate']}%)\n"
        f"🔴 Bounced: {stats['bounced']} ({stats['bounce_rate']}%)\n"
        f"📬 Today: {stats['today_sent']}/{DAILY_SEND_LIMIT}\n"
        f"⏳ Follow-ups: {stats['due_followup']}"
    )


# --- Pipeline ---

def run_cycle():
    """Run one collect + send + check cycle."""
    global sheets_mgr
    if not _busy.acquire(timeout=300):  # 5 min max wait
        tg("⚠️ Previous cycle still running — skipping")
        return
    try:
        if not sheets_mgr:
            sheets_mgr = SheetsManager()
            sheets_mgr.connect()

        stats = get_stats()
        tg(f"🔄 <b>Cycle Started</b>\n"
           f"📊 {stats['total']} collected | {stats['new']} unsent | {stats['today_sent']}/{DAILY_SEND_LIMIT} today")

        # Collect with progress
        collect_msg = tg("📥 <b>Collecting...</b>\n" + progress_bar(0, 1))
        web = 0
        def on_collect_progress(query_num, total_queries, emails_found):
            tg_edit(collect_msg,
                    f"📥 <b>Collecting...</b>\n"
                    f"{progress_bar(query_num, total_queries)}\n"
                    f"Query {query_num}/{total_queries} | Found: {emails_found} emails")
        try:
            web = run_website_collector(progress_cb=on_collect_progress)
        except Exception as e:
            tg(f"⚠️ Collection: {str(e)[:200]}")
        tg_edit(collect_msg, f"📥 <b>Collection Done</b> — {web} new emails\n{progress_bar(1, 1)}")

        # Sync Sheets
        if sheets_mgr and sheets_mgr.ws:
            try:
                synced = sheets_mgr.sync_from_db(get_all_emails_for_sync())
                if synced > 0:
                    notify_sheets_sync(synced)
            except Exception:
                pass

        # Send with progress
        total_to_send = len(get_unsent_emails(limit=DAILY_SEND_LIMIT))
        send_msg = tg(f"📤 <b>Sending...</b> (0/{total_to_send})\n" + progress_bar(0, total_to_send))
        sent = 0
        def on_send_progress(current, total, email_addr):
            tg_edit(send_msg,
                    f"📤 <b>Sending...</b>\n"
                    f"{progress_bar(current, total)}\n"
                    f"{current}/{total} | Last: {email_addr}")
        try:
            sent = start_sender(progress_cb=on_send_progress)
        except Exception as e:
            tg(f"⚠️ Sending: {str(e)[:200]}")
        tg_edit(send_msg, f"📤 <b>Sending Done</b> — {sent} sent\n{progress_bar(1, 1)}")

        # Check inbox
        try:
            check_inbox()
        except Exception:
            pass

        # Report
        _, report = format_stats()
        tg(f"✅ <b>Cycle Done</b>\n📥 +{web} collected | 📤 {sent} sent\n\n{report}")
    finally:
        _busy.release()


def pipeline_loop():
    global pipeline_running
    tg("🟢 <b>Pipeline Started</b>\nCollect + send every 3 hours.")

    while pipeline_running:
        try:
            run_cycle()
        except Exception as e:
            tg(f"⚠️ Error: {str(e)[:200]}")

        if not pipeline_running:
            break

        next_t = datetime.fromtimestamp(time.time() + INTERVAL).strftime('%H:%M')
        tg(f"⏰ Next cycle at {next_t}")

        for _ in range(INTERVAL // 10):
            if not pipeline_running:
                break
            time.sleep(10)

    tg("🔴 <b>Pipeline Stopped</b>")


# --- LeadGen (import old system if available) ---

def leadgen_available():
    try:
        lg = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'gmaps-lead-gen'))
        if os.path.exists(lg):
            sys.path.insert(0, lg)
            return True
    except Exception:
        pass
    return False


HAS_LEADGEN = leadgen_available()
_edit_state = {}


def handle_leadgen_cmd(text):
    if not HAS_LEADGEN:
        return "LeadGen not available."
    try:
        if text == "/pending":
            from src.telegram_bot import get_pending_count
            return f"<b>Pending:</b> {get_pending_count()}"
        elif text == "/lgstats":
            from src.telegram_bot import get_daily_summary
            s = get_daily_summary()
            return (f"<b>LeadGen Today</b>\nApproved: {s.get('approved',0)} | "
                    f"Rejected: {s.get('rejected',0)} | Pending: {s.get('pending',0)}")
        elif text == "/followup":
            from src.followup import get_followup_summary, get_due_followups
            s = get_followup_summary()
            return (f"<b>Follow-Ups</b>\nActive: {s.get('active',0)} | "
                    f"Replied: {s.get('replied',0)} | Due: {len(get_due_followups())}")
        elif text in ("/health", "/pause", "/resume"):
            from src.watchdog import handle_watchdog_command
            cmd = "/status" if text == "/health" else text
            return handle_watchdog_command(cmd)
        elif text == "/automode":
            import config as lg_cfg
            new = "auto" if lg_cfg.APPROVAL_MODE == "telegram" else "telegram"
            lg_cfg.APPROVAL_MODE = new
            return f"Mode: <b>{new}</b>"
    except Exception as e:
        return f"LeadGen error: {str(e)[:200]}"
    return None


def handle_leadgen_callback(cb):
    if not HAS_LEADGEN:
        return
    try:
        from src.telegram_bot import approve_email, reject_email, regenerate_email
        data = cb.get("data", "")
        cb_id = cb.get("id", "")
        msg_id = str(cb.get("message", {}).get("message_id", ""))
        parts = data.split("_", 1)
        if len(parts) != 2:
            return
        action, qid = parts[0], int(parts[1])

        if action == "approve":
            tg_answer(cb_id, "Sending...")
            ok = approve_email(qid)
            tg_edit(msg_id, f"#{qid} — <b>{'Sent' if ok else 'Failed'}</b>")
        elif action == "reject":
            tg_answer(cb_id, "Rejected")
            reject_email(qid)
            tg_edit(msg_id, f"#{qid} — <b>Rejected</b>")
        elif action == "edit":
            tg_answer(cb_id, "Send new subject...")
            chat_id = str(cb.get("message", {}).get("chat", {}).get("id", ""))
            _edit_state[chat_id] = {"qid": qid, "step": "subject"}
            tg(f"Editing #{qid}\nSend new <b>subject</b> (or /skip):")
        elif action == "regen":
            tg_answer(cb_id, "Regenerating...")
            tg_edit(msg_id, f"#{qid} — <b>Regenerating...</b>")
            regenerate_email(qid)
    except Exception as e:
        log.warning(f"LG callback error: {e}")


def handle_edit(chat_id, text):
    if chat_id not in _edit_state:
        return False
    try:
        from src.telegram_bot import update_email, _get_db
        st = _edit_state[chat_id]
        qid = st["qid"]
        if st["step"] == "subject":
            if text != "/skip":
                update_email(qid, subject=text)
            _edit_state[chat_id]["step"] = "body"
            tg("Send new <b>body</b> (or /skip):")
            return True
        elif st["step"] == "body":
            if text != "/skip":
                update_email(qid, body=text)
            del _edit_state[chat_id]
            conn = _get_db()
            row = conn.execute("SELECT * FROM approval_queue WHERE id = ?", (qid,)).fetchone()
            conn.close()
            if row:
                tg(f"<b>Updated #{qid}</b>\n\n<b>Subject:</b> {row['subject']}\n\n<pre>{row['body'][:500]}</pre>",
                   reply_markup={"inline_keyboard": [[
                       {"text": "Approve", "callback_data": f"approve_{qid}"},
                       {"text": "Reject", "callback_data": f"reject_{qid}"}]]})
            return True
    except Exception as e:
        log.warning(f"Edit error: {e}")
    return False


# --- Message Handler ---

def on_message(msg):
    global pipeline_running, pipeline_thread
    chat_id = str(msg.get("chat", {}).get("id", ""))
    text = msg.get("text", "").strip()

    if chat_id != TELEGRAM_CHAT_ID:
        return

    if handle_edit(chat_id, text):
        return

    if text == "/start" or text == "/help":
        lg = "✅" if HAS_LEADGEN else "❌"
        tg(f"🤖 <b>LeadOutreach Bot</b>\n\n"
           f"/start — Start pipeline (every 3h)\n"
           f"/stop — Stop pipeline\n"
           f"/status — Full report\n"
           f"/collect — Collect now\n"
           f"/send — Send now\n"
           f"/check — Check replies/bounces\n"
           f"/pending — LeadGen approvals {lg}\n"
           f"/lgstats — LeadGen stats {lg}\n"
           f"/followup — Follow-ups {lg}\n"
           f"/health — System health {lg}\n"
           f"/automode — Toggle auto-approve {lg}")

        if not pipeline_running:
            pipeline_running = True
            pipeline_thread = threading.Thread(target=pipeline_loop, daemon=True)
            pipeline_thread.start()

    elif text == "/stop":
        if not pipeline_running:
            tg("Already stopped.")
        else:
            pipeline_running = False
            tg("🔴 Stopping...")

    elif text == "/status":
        running = "🟢 Running" if pipeline_running else "🔴 Stopped"
        _, report = format_stats()
        tg(f"📊 <b>Status</b> {running}\n\n{report}")

    elif text == "/collect":
        if not _busy.acquire(blocking=False):
            tg("⏳ Already running a task. Wait.")
            return
        tg("📥 Collecting...")
        def _c():
            try:
                n = run_website_collector()
                tg(f"📥 Done: {n} new emails")
            except Exception as e:
                tg(f"⚠️ {str(e)[:200]}")
            finally:
                _busy.release()
        threading.Thread(target=_c, daemon=True).start()

    elif text == "/send":
        if not _busy.acquire(blocking=False):
            tg("⏳ Already running a task. Wait.")
            return
        tg("📤 Sending...")
        def _s():
            try:
                n = start_sender()
                s = get_stats()
                tg(f"📤 Done: {n} sent | Today: {s['today_sent']}/{DAILY_SEND_LIMIT}")
            except Exception as e:
                tg(f"⚠️ {str(e)[:200]}")
            finally:
                _busy.release()
        threading.Thread(target=_s, daemon=True).start()

    elif text == "/check":
        tg("📬 Checking...")
        def _k():
            try:
                i = check_inbox()
                s = get_stats()
                tg(f"📬 <b>Inbox</b>\n"
                   f"💬 New replies: {i['replies']}\n"
                   f"🔴 New bounces: {i['bounces']}\n\n"
                   f"Total replies: {s['replied']} ({s['reply_rate']}%)\n"
                   f"Total bounced: {s['bounced']} ({s['bounce_rate']}%)")
            except Exception as e:
                tg(f"⚠️ {str(e)[:200]}")
        threading.Thread(target=_k, daemon=True).start()

    elif text in ("/pending", "/lgstats", "/followup", "/health", "/pause", "/resume", "/automode"):
        r = handle_leadgen_cmd(text)
        if r:
            tg(r)


def on_callback(cb):
    handle_leadgen_callback(cb)


# --- Main ---

def main():
    init_db()
    log.info("Starting LeadOutreach bot...")

    tg("🤖 <b>LeadOutreach Bot Online</b>\nType /start to begin.")

    log.info("Bot running.")
    offset = 0
    while True:
        try:
            resp = req.get(f"{API_BASE}/getUpdates",
                          params={"offset": offset, "timeout": 30}, timeout=35)
            data = resp.json()
            if not data.get("ok"):
                continue
            for u in data.get("result", []):
                offset = u["update_id"] + 1
                if "callback_query" in u:
                    on_callback(u["callback_query"])
                elif "message" in u:
                    on_message(u["message"])
        except req.exceptions.Timeout:
            pass
        except Exception as e:
            log.warning(f"Poll error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
