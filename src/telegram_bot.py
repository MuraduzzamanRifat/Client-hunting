"""
Telegram Bot — Human-in-the-loop email approval system.

Flow:
1. AI generates email → queued in SQLite
2. Bot sends preview to Telegram with Approve/Edit/Reject/Regenerate buttons
3. User approves → email sent immediately
4. User edits → bot waits for new text → confirms → sends
5. User rejects → email discarded
6. User regenerates → AI creates new version → sends new preview

Commands:
  /start     — Welcome message
  /pending   — Show pending approvals count
  /stats     — Daily summary
  /automode  — Toggle auto-approve (skip Telegram approval)
"""

import json
import os
import sys
import time
import threading
import sqlite3
import requests
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from src.metrics import log_event

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "approvals.db")
_lock = threading.Lock()
_edit_state = {}  # chat_id -> {"queue_id": id, "step": "subject"|"body"}

API_BASE = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"


# ── Database ─────────────────────────────────────────────────────────

def _get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = _get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS approval_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            recipient TEXT NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            lead_data TEXT DEFAULT '{}',
            service TEXT DEFAULT '',
            angle TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            telegram_msg_id TEXT DEFAULT '',
            decided_at TEXT DEFAULT '',
            decision_by TEXT DEFAULT '',
            sheet_row INTEGER DEFAULT 0,
            sent_at TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS approval_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            queue_id INTEGER,
            action TEXT NOT NULL,
            details TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_queue_status ON approval_queue(status);
    """)
    conn.close()


init_db()


# ── Telegram API Helpers ─────────────────────────────────────────────

def _send_message(chat_id: str, text: str, reply_markup: dict = None, parse_mode: str = "HTML") -> dict:
    """Send a message via Telegram API."""
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        resp = requests.post(f"{API_BASE}/sendMessage", json=payload, timeout=10)
        return resp.json()
    except Exception as e:
        print(f"[TELEGRAM] Send error: {e}")
        return {}


def _edit_message(chat_id: str, msg_id: str, text: str, reply_markup: dict = None) -> dict:
    payload = {"chat_id": chat_id, "message_id": msg_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        resp = requests.post(f"{API_BASE}/editMessageText", json=payload, timeout=10)
        return resp.json()
    except Exception as e:
        print(f"[TELEGRAM] Edit error: {e}")
        return {}


def _answer_callback(callback_id: str, text: str = ""):
    try:
        requests.post(f"{API_BASE}/answerCallbackQuery",
                      json={"callback_query_id": callback_id, "text": text}, timeout=5)
    except Exception:
        pass


# ── Queue Management ─────────────────────────────────────────────────

def _is_duplicate(recipient: str) -> bool:
    """Check if recipient already has a pending/approved email in the queue."""
    conn = _get_db()
    row = conn.execute(
        "SELECT id FROM approval_queue WHERE recipient = ? AND status IN ('pending', 'approved') LIMIT 1",
        (recipient,)
    ).fetchone()
    conn.close()
    return row is not None


def queue_email(recipient: str, subject: str, body: str, lead_data: dict = None,
                service: str = "", angle: str = "", sheet_row: int = 0) -> int:
    """Add email to approval queue and notify Telegram. Skips duplicates."""
    if _is_duplicate(recipient):
        print(f"  [QUEUE] Skipping duplicate: {recipient}")
        return -1

    # Check follow-up limit
    from src.followup import can_send
    if not can_send(recipient):
        print(f"  [QUEUE] Skipping {recipient} (follow-up limit/replied/bounced)")
        return -1

    with _lock:
        conn = _get_db()
        cur = conn.execute(
            "INSERT INTO approval_queue (created_at, recipient, subject, body, lead_data, service, angle, sheet_row) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (datetime.now().isoformat(), recipient, subject, body,
             json.dumps(lead_data or {}), service, angle, sheet_row)
        )
        queue_id = cur.lastrowid
        conn.commit()
        conn.close()

    _log_action(queue_id, "queued", f"To: {recipient}")

    # Scan email quality before proceeding
    from src.watchdog import scan_email_quality
    quality = scan_email_quality(subject, body)
    if not quality["safe"]:
        _log_action(queue_id, "quality_blocked", f"Score: {quality['score']}, Issues: {quality['issues']}")
        print(f"  [QUEUE] Blocked #{queue_id} — quality score {quality['score']}: {quality['issues'][:2]}")
        # Still queue it but flag for review
        if config.TELEGRAM_CHAT_ID:
            issues_text = "\n".join(f"  - {i}" for i in quality["issues"])
            _send_message(config.TELEGRAM_CHAT_ID,
                          f"<b>Quality Warning</b> #{queue_id}\n\n"
                          f"Score: {quality['score']}/100\n"
                          f"Issues:\n{issues_text}\n\n"
                          f"Email still queued for review.")

    # Auto-approve mode
    if config.APPROVAL_MODE == "auto":
        if quality["safe"]:  # only auto-approve if quality passes
            approve_email(queue_id)
        return queue_id

    # Send to Telegram for approval
    _send_approval_preview(queue_id, recipient, subject, body, service, angle, lead_data)
    return queue_id


def _send_approval_preview(queue_id: int, recipient: str, subject: str, body: str,
                           service: str, angle: str, lead_data: dict = None):
    """Send email preview to Telegram with action buttons."""
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        print("[TELEGRAM] Bot token or chat ID not configured")
        return

    name = (lead_data or {}).get("Name", "Unknown")
    rating = (lead_data or {}).get("Rating", "-")
    website = (lead_data or {}).get("Website", "None")

    # Truncate body for preview (Telegram has 4096 char limit)
    preview_body = body[:800] + ("..." if len(body) > 800 else "")

    text = (
        f"<b>New Email for Approval</b> #{queue_id}\n"
        f"{'─' * 30}\n\n"
        f"<b>To:</b> {recipient}\n"
        f"<b>Business:</b> {name}\n"
        f"<b>Rating:</b> {rating} | <b>Website:</b> {website or 'None'}\n"
        f"<b>Service:</b> {service} | <b>Angle:</b> {angle}\n\n"
        f"<b>Subject:</b> {subject}\n\n"
        f"<pre>{preview_body}</pre>"
    )

    buttons = {
        "inline_keyboard": [
            [
                {"text": "Approve", "callback_data": f"approve_{queue_id}"},
                {"text": "Reject", "callback_data": f"reject_{queue_id}"},
            ],
            [
                {"text": "Edit", "callback_data": f"edit_{queue_id}"},
                {"text": "Regenerate", "callback_data": f"regen_{queue_id}"},
            ]
        ]
    }

    result = _send_message(config.TELEGRAM_CHAT_ID, text, reply_markup=buttons)
    if result.get("ok"):
        msg_id = str(result["result"]["message_id"])
        with _lock:
            conn = _get_db()
            conn.execute("UPDATE approval_queue SET telegram_msg_id = ? WHERE id = ?", (msg_id, queue_id))
            conn.commit()
            conn.close()


def approve_email(queue_id: int) -> bool:
    """Approve and send the email."""
    conn = _get_db()
    row = conn.execute("SELECT * FROM approval_queue WHERE id = ? AND status = 'pending'", (queue_id,)).fetchone()
    conn.close()

    if not row:
        return False

    # Send the email
    from src.email_sender import send_email, _connect_smtp
    smtp = _connect_smtp()
    if not smtp:
        _log_action(queue_id, "send_failed", "SMTP connection failed")
        return False

    from_addr = config.EMAIL_FROM or config.EMAIL_USER
    success = send_email(smtp, row["recipient"], row["subject"], row["body"], from_addr)
    smtp.quit()

    if success:
        with _lock:
            conn = _get_db()
            conn.execute(
                "UPDATE approval_queue SET status = 'approved', decided_at = ?, sent_at = ? WHERE id = ?",
                (datetime.now().isoformat(), datetime.now().isoformat(), queue_id)
            )
            conn.commit()
            conn.close()

        log_event("sent", recipient=row["recipient"], subject=row["subject"], status="approved")
        _log_action(queue_id, "approved_sent", f"Sent to {row['recipient']}")

        # Track for follow-ups
        from src.followup import track_sent
        lead_data = json.loads(row["lead_data"] or "{}")
        track_sent(row["recipient"], name=lead_data.get("Name", ""),
                   lead_data=lead_data, sheet_row=row["sheet_row"])

        # Update Google Sheet if we have a row reference
        if row["sheet_row"] > 0:
            _update_sheet_row(row["sheet_row"])

        # Notify Telegram
        _send_message(config.TELEGRAM_CHAT_ID,
                      f"Sent #{queue_id} to {row['recipient']}")
        return True
    else:
        _log_action(queue_id, "send_failed", f"SMTP error for {row['recipient']}")
        log_event("failed", recipient=row["recipient"], subject=row["subject"], status="smtp_error")
        return False


def reject_email(queue_id: int):
    """Reject and discard the email."""
    with _lock:
        conn = _get_db()
        conn.execute(
            "UPDATE approval_queue SET status = 'rejected', decided_at = ? WHERE id = ?",
            (datetime.now().isoformat(), queue_id)
        )
        conn.commit()
        conn.close()
    _log_action(queue_id, "rejected", "")


def update_email(queue_id: int, subject: str = None, body: str = None):
    """Update email content after editing. Logs changes for AI learning."""
    with _lock:
        conn = _get_db()
        # Save original before overwriting (for learning)
        if subject or body:
            row = conn.execute("SELECT subject, body FROM approval_queue WHERE id = ?", (queue_id,)).fetchone()
            if row:
                changes = {}
                if subject and subject != row["subject"]:
                    changes["subject_before"] = row["subject"]
                    changes["subject_after"] = subject
                if body and body != row["body"]:
                    changes["body_before"] = row["body"][:200]
                    changes["body_after"] = body[:200]
                if changes:
                    _log_action(queue_id, "edit_diff", json.dumps(changes))

        if subject:
            conn.execute("UPDATE approval_queue SET subject = ? WHERE id = ?", (subject, queue_id))
        if body:
            conn.execute("UPDATE approval_queue SET body = ? WHERE id = ?", (body, queue_id))
        conn.commit()
        conn.close()
    _log_action(queue_id, "edited", "Content updated")


def regenerate_email(queue_id: int):
    """Regenerate email using AI personalizer."""
    conn = _get_db()
    row = conn.execute("SELECT * FROM approval_queue WHERE id = ?", (queue_id,)).fetchone()
    conn.close()
    if not row:
        return

    from src.ai_personalizer import generate_personalized_email
    lead_data = json.loads(row["lead_data"] or "{}")
    new_email = generate_personalized_email(lead_data)

    update_email(queue_id, subject=new_email["subject"], body=new_email["body"])
    _log_action(queue_id, "regenerated", "AI created new version")

    # Send new preview
    _send_approval_preview(queue_id, row["recipient"], new_email["subject"],
                           new_email["body"], new_email["service"], new_email["angle"], lead_data)


def _update_sheet_row(row_idx: int):
    """Update Google Sheet after sending."""
    try:
        from src.sheets_manager import SheetsManager
        sheets = SheetsManager()
        if sheets.authenticate():
            sheets.open_or_create_sheet()
            sheets.update_row(row_idx, {
                "Contacted": "Yes",
                "Status": "Email Sent",
                "Notes": f"Sent {datetime.now().strftime('%Y-%m-%d %H:%M')} (approved via Telegram)",
            })
    except Exception as e:
        print(f"[TELEGRAM] Sheet update failed: {e}")


def _log_action(queue_id: int, action: str, details: str = ""):
    conn = _get_db()
    conn.execute(
        "INSERT INTO approval_log (timestamp, queue_id, action, details) VALUES (?, ?, ?, ?)",
        (datetime.now().isoformat(), queue_id, action, details)
    )
    conn.commit()
    conn.close()


# ── Stats & Queries ──────────────────────────────────────────────────

def get_pending_count() -> int:
    conn = _get_db()
    row = conn.execute("SELECT COUNT(*) as cnt FROM approval_queue WHERE status = 'pending'").fetchone()
    conn.close()
    return row["cnt"]


def get_daily_summary() -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    conn = _get_db()
    rows = conn.execute(
        "SELECT status, COUNT(*) as cnt FROM approval_queue WHERE created_at LIKE ? GROUP BY status",
        (f"{today}%",)
    ).fetchall()
    conn.close()
    return {r["status"]: r["cnt"] for r in rows}


def get_queue(status: str = "", limit: int = 20) -> list[dict]:
    conn = _get_db()
    if status:
        rows = conn.execute(
            "SELECT * FROM approval_queue WHERE status = ? ORDER BY created_at DESC LIMIT ?",
            (status, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM approval_queue ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Telegram Polling Loop ────────────────────────────────────────────

def _handle_callback(callback):
    """Handle inline button presses."""
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
        _answer_callback(callback_id, "Sending email...")
        success = approve_email(queue_id)
        status_text = "Sent" if success else "Failed to send"
        _edit_message(chat_id, msg_id,
                      f"#{queue_id} — <b>{status_text}</b>")

    elif action == "reject":
        _answer_callback(callback_id, "Email rejected")
        reject_email(queue_id)
        _edit_message(chat_id, msg_id,
                      f"#{queue_id} — <b>Rejected</b>")

    elif action == "edit":
        _answer_callback(callback_id, "Send new subject line...")
        _edit_state[chat_id] = {"queue_id": queue_id, "step": "subject", "msg_id": msg_id}
        _send_message(chat_id,
                      f"Editing #{queue_id}\n\nSend me the <b>new subject line</b>:\n(or send /skip to keep current)")

    elif action == "regen":
        _answer_callback(callback_id, "Regenerating...")
        _edit_message(chat_id, msg_id,
                      f"#{queue_id} — <b>Regenerating...</b>")
        regenerate_email(queue_id)


def _handle_message(msg):
    """Handle text messages (commands and edit responses)."""
    chat_id = str(msg.get("chat", {}).get("id", ""))
    text = msg.get("text", "").strip()

    # Check if user is in edit mode
    if chat_id in _edit_state:
        state = _edit_state[chat_id]
        queue_id = state["queue_id"]

        if state["step"] == "subject":
            if text != "/skip":
                update_email(queue_id, subject=text)
            _edit_state[chat_id]["step"] = "body"
            _send_message(chat_id,
                          f"Now send me the <b>new email body</b>:\n(or send /skip to keep current)")
            return

        elif state["step"] == "body":
            if text != "/skip":
                update_email(queue_id, body=text)
            del _edit_state[chat_id]

            # Show updated version with approve/reject
            conn = _get_db()
            row = conn.execute("SELECT * FROM approval_queue WHERE id = ?", (queue_id,)).fetchone()
            conn.close()

            if row:
                preview = row["body"][:500]
                buttons = {
                    "inline_keyboard": [[
                        {"text": "Approve Edited", "callback_data": f"approve_{queue_id}"},
                        {"text": "Reject", "callback_data": f"reject_{queue_id}"},
                    ]]
                }
                _send_message(chat_id,
                              f"<b>Updated #{queue_id}</b>\n\n"
                              f"<b>Subject:</b> {row['subject']}\n\n"
                              f"<pre>{preview}</pre>",
                              reply_markup=buttons)
            return

    # Commands
    if text == "/start":
        _send_message(chat_id,
                      "<b>LeadGen AI Bot</b>\n\n"
                      "I'll send you emails for approval before they go out.\n\n"
                      "<b>Commands:</b>\n"
                      "/pending — Pending approvals\n"
                      "/stats — Daily summary\n"
                      "/followup — Follow-up status\n"
                      "/status — System health\n"
                      "/pause — Pause all sending\n"
                      "/resume — Resume sending\n"
                      "/report — Daily audit report\n"
                      "/automode — Toggle auto-approve")

    elif text == "/pending":
        count = get_pending_count()
        _send_message(chat_id, f"<b>Pending approvals:</b> {count}")

    elif text == "/stats":
        summary = get_daily_summary()
        total = sum(summary.values())
        text_out = (
            f"<b>Today's Summary</b>\n\n"
            f"Total: {total}\n"
            f"Approved: {summary.get('approved', 0)}\n"
            f"Rejected: {summary.get('rejected', 0)}\n"
            f"Pending: {summary.get('pending', 0)}"
        )
        _send_message(chat_id, text_out)

    elif text == "/followup":
        from src.followup import get_followup_summary, get_due_followups
        summary = get_followup_summary()
        due = get_due_followups()
        text_out = (
            f"<b>Follow-Up Status</b>\n\n"
            f"Active: {summary.get('active', 0)}\n"
            f"Replied: {summary.get('replied', 0)}\n"
            f"Stopped: {summary.get('stopped', 0)}\n"
            f"Bounced: {summary.get('bounced', 0)}\n\n"
            f"Due now: {len(due)} follow-ups"
        )
        _send_message(chat_id, text_out)

    elif text in ("/status", "/resume", "/pause", "/report"):
        from src.watchdog import handle_watchdog_command
        response = handle_watchdog_command(text)
        if response:
            _send_message(chat_id, response)

    elif text == "/automode":
        current = config.APPROVAL_MODE
        new_mode = "auto" if current == "telegram" else "telegram"
        config.APPROVAL_MODE = new_mode
        _send_message(chat_id,
                      f"Approval mode: <b>{new_mode}</b>\n"
                      f"{'Emails will be sent without approval.' if new_mode == 'auto' else 'All emails require your approval.'}")


def _poll_updates(offset: int = 0) -> int:
    """Long-poll for Telegram updates."""
    try:
        resp = requests.get(f"{API_BASE}/getUpdates",
                            params={"offset": offset, "timeout": 30}, timeout=35)
        data = resp.json()
        if not data.get("ok"):
            return offset

        for update in data.get("result", []):
            offset = update["update_id"] + 1

            if "callback_query" in update:
                _handle_callback(update["callback_query"])
            elif "message" in update:
                _handle_message(update["message"])

    except requests.exceptions.Timeout:
        pass
    except Exception as e:
        print(f"[TELEGRAM] Poll error: {e}")
        time.sleep(5)

    return offset


def start_bot_loop():
    """Run the Telegram bot polling loop."""
    if not config.TELEGRAM_BOT_TOKEN:
        print("[TELEGRAM] Bot token not configured. Skipping.")
        return

    print(f"[TELEGRAM] Bot starting (mode: {config.APPROVAL_MODE})...")
    offset = 0
    while True:
        offset = _poll_updates(offset)


def start_bot_thread():
    """Start the bot as a background thread."""
    if not config.TELEGRAM_BOT_TOKEN:
        return
    thread = threading.Thread(target=start_bot_loop, daemon=True)
    thread.start()
    print("[TELEGRAM] Bot thread started.")


# ── Timeout Handler ──────────────────────────────────────────────────

def expire_old_approvals():
    """Auto-reject approvals older than APPROVAL_TIMEOUT_HOURS."""
    cutoff = (datetime.now() - timedelta(hours=config.APPROVAL_TIMEOUT_HOURS)).isoformat()
    with _lock:
        conn = _get_db()
        expired = conn.execute(
            "SELECT id, recipient FROM approval_queue WHERE status = 'pending' AND created_at < ?",
            (cutoff,)
        ).fetchall()
        for row in expired:
            conn.execute(
                "UPDATE approval_queue SET status = 'expired', decided_at = ? WHERE id = ?",
                (datetime.now().isoformat(), row["id"])
            )
            _log_action(row["id"], "expired", f"No response within {config.APPROVAL_TIMEOUT_HOURS}h")
        conn.commit()
        conn.close()

    if expired:
        _send_message(config.TELEGRAM_CHAT_ID,
                      f"<b>{len(expired)} email(s) expired</b> (no response within {config.APPROVAL_TIMEOUT_HOURS}h)")
