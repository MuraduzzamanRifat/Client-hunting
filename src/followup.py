"""
Follow-Up Engine — Reply-Based Two-Step Email Flow.

Strategy:
  Email 1 (initial outreach) → wait for reply → NO time-based follow-ups

  IF reply arrives:
    - interested / question  → generate conversion email → queue for Telegram approval
    - unclear                → generate conversion email but flag "needs review" in Telegram
    - not_interested         → stop all outreach to this recipient
    - bounce                 → mark bounced, stop
    - out_of_office          → do nothing, wait

Rules:
  - Max 2 emails per recipient (initial + conversion)
  - NEVER send a second email without a reply
  - All emails go through Telegram approval
"""

import os
import sys
import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "approvals.db")
_lock = threading.Lock()

MAX_EMAILS_PER_RECIPIENT = 2    # initial email + conversion email only
REPLY_CHECK_INTERVAL = 300      # seconds between inbox checks (5 min)


# ── Database Setup ───────────────────────────────────────────────────

def _get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_followup_db():
    conn = _get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS recipient_tracker (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            name TEXT DEFAULT '',
            lead_data TEXT DEFAULT '{}',
            emails_sent INTEGER DEFAULT 0,
            last_sent_at TEXT DEFAULT '',
            next_followup_at TEXT DEFAULT '',
            status TEXT DEFAULT 'active',
            reply_detected INTEGER DEFAULT 0,
            reply_at TEXT DEFAULT '',
            bounced INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            sheet_row INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_tracker_email ON recipient_tracker(email);
        CREATE INDEX IF NOT EXISTS idx_tracker_status ON recipient_tracker(status);
    """)
    conn.close()


init_followup_db()


# ── Recipient Status Management ──────────────────────────────────────

def track_sent(email: str, name: str = "", lead_data: dict = None, sheet_row: int = 0):
    """Record that an email was sent to this recipient."""
    now = datetime.now().isoformat()
    email_lower = email.lower().strip()

    with _lock:
        conn = _get_db()
        row = conn.execute("SELECT * FROM recipient_tracker WHERE email = ?", (email_lower,)).fetchone()

        if row:
            new_count = row["emails_sent"] + 1
            status = "stopped" if new_count >= MAX_EMAILS_PER_RECIPIENT else row["status"]
            conn.execute(
                "UPDATE recipient_tracker SET emails_sent=?, last_sent_at=?, "
                "next_followup_at='', status=?, updated_at=? WHERE email=?",
                (new_count, now, status, now, email_lower)
            )
        else:
            # First email sent — wait for reply, no automatic follow-up scheduled
            conn.execute(
                "INSERT INTO recipient_tracker "
                "(email, name, lead_data, emails_sent, last_sent_at, next_followup_at, "
                "status, created_at, updated_at, sheet_row) "
                "VALUES (?, ?, ?, 1, ?, '', 'active', ?, ?, ?)",
                (email_lower, name, json.dumps(lead_data or {}), now, now, now, sheet_row)
            )

        conn.commit()
        conn.close()


def mark_replied(email: str):
    """Mark recipient as replied with negative intent — stops all outreach."""
    now = datetime.now().isoformat()
    with _lock:
        conn = _get_db()
        conn.execute(
            "UPDATE recipient_tracker SET status='replied', reply_detected=1, "
            "reply_at=?, next_followup_at='', updated_at=? WHERE email=?",
            (now, now, email.lower().strip())
        )
        conn.commit()
        conn.close()


def mark_interested_reply(email: str):
    """Mark recipient as replied with positive intent — ready for conversion email."""
    now = datetime.now().isoformat()
    with _lock:
        conn = _get_db()
        conn.execute(
            "UPDATE recipient_tracker SET status='interested_replied', reply_detected=1, "
            "reply_at=?, next_followup_at='', updated_at=? WHERE email=?",
            (now, now, email.lower().strip())
        )
        conn.commit()
        conn.close()


def mark_unclear_reply(email: str):
    """Mark recipient as replied with unclear intent — Telegram will decide."""
    now = datetime.now().isoformat()
    with _lock:
        conn = _get_db()
        conn.execute(
            "UPDATE recipient_tracker SET status='unclear_reply', reply_detected=1, "
            "reply_at=?, next_followup_at='', updated_at=? WHERE email=?",
            (now, now, email.lower().strip())
        )
        conn.commit()
        conn.close()


def mark_conversion_queued(email: str):
    """Mark that a conversion email has been queued — prevents duplicate queuing."""
    now = datetime.now().isoformat()
    with _lock:
        conn = _get_db()
        conn.execute(
            "UPDATE recipient_tracker SET status='conversion_queued', updated_at=? WHERE email=?",
            (now, email.lower().strip())
        )
        conn.commit()
        conn.close()


def mark_bounced(email: str):
    """Mark recipient as bounced — stops all outreach."""
    now = datetime.now().isoformat()
    with _lock:
        conn = _get_db()
        conn.execute(
            "UPDATE recipient_tracker SET status='bounced', bounced=1, "
            "next_followup_at='', updated_at=? WHERE email=?",
            (now, email.lower().strip())
        )
        conn.commit()
        conn.close()


def can_send(email: str) -> bool:
    """
    Check if we can send to this recipient.

    Allowed:
      - Never contacted (not in DB)
      - Status: interested_replied or unclear_reply (conversion email pending)

    Blocked:
      - replied (negative), bounced, stopped, conversion_queued
      - Max emails reached
    """
    conn = _get_db()
    row = conn.execute(
        "SELECT * FROM recipient_tracker WHERE email = ?",
        (email.lower().strip(),)
    ).fetchone()
    conn.close()

    if not row:
        return True  # never contacted

    if row["status"] in ("replied", "bounced", "stopped", "conversion_queued"):
        return False

    if row["status"] in ("interested_replied", "unclear_reply"):
        return True  # conversion email allowed

    # "active" — first email sent, waiting for reply
    if row["emails_sent"] >= MAX_EMAILS_PER_RECIPIENT:
        return False

    return True


def get_all_recipients(status: str = "", limit: int = 50) -> list[dict]:
    """Get all tracked recipients."""
    conn = _get_db()
    if status:
        rows = conn.execute(
            "SELECT * FROM recipient_tracker WHERE status = ? ORDER BY updated_at DESC LIMIT ?",
            (status, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM recipient_tracker ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_followup_summary() -> dict:
    """Get summary counts by status."""
    conn = _get_db()
    rows = conn.execute(
        "SELECT status, COUNT(*) as cnt FROM recipient_tracker GROUP BY status"
    ).fetchall()
    conn.close()
    summary = {r["status"]: r["cnt"] for r in rows}
    summary["total"] = sum(v for k, v in summary.items() if k != "total")
    return summary


# ── Reply Detection ──────────────────────────────────────────────────

def check_replies() -> list[dict]:
    """
    Check IMAP inbox for replies from tracked recipients.
    Classifies each reply's intent and updates recipient status.

    Returns list of reply dicts for follow-up processing.
    """
    import imaplib
    import email as email_lib
    import re
    from src.reply_classifier import classify_reply

    if not config.IMAP_USER or not config.IMAP_PASSWORD:
        return []

    conn = _get_db()
    # Only check recipients who are "active" (waiting for first reply)
    active = conn.execute(
        "SELECT email FROM recipient_tracker WHERE status = 'active'"
    ).fetchall()
    conn.close()

    if not active:
        return []

    tracked_emails = {r["email"] for r in active}
    new_replies = []

    try:
        imap = imaplib.IMAP4_SSL(config.IMAP_SERVER, config.IMAP_PORT, timeout=15)
        imap.login(config.IMAP_USER, config.IMAP_PASSWORD)
        imap.select("INBOX", readonly=True)

        since_date = (datetime.now() - timedelta(days=7)).strftime("%d-%b-%Y")
        status, data = imap.search(None, f'(SINCE {since_date})')

        if status != "OK" or not data[0]:
            imap.logout()
            return []

        msg_ids = data[0].split()
        for msg_id in msg_ids[-50:]:
            try:
                _, msg_data = imap.fetch(msg_id, "(BODY.PEEK[])")
                raw = msg_data[0][1]
                msg = email_lib.message_from_bytes(raw)

                from_header = msg.get("From", "")
                from_match = re.search(
                    r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', from_header
                )
                if not from_match:
                    continue
                sender = from_match.group(1).lower()

                if sender not in tracked_emails:
                    continue

                # Extract body
                body_text = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            try:
                                body_text = part.get_payload(decode=True).decode(
                                    part.get_content_charset() or "utf-8", errors="replace")
                            except Exception:
                                pass
                            break
                else:
                    try:
                        body_text = msg.get_payload(decode=True).decode(
                            msg.get_content_charset() or "utf-8", errors="replace")
                    except Exception:
                        pass

                classification = classify_reply(body_text, sender)
                intent = classification["intent"]
                action = classification["action"]

                # Update status based on action
                if action == "mark_bounced":
                    mark_bounced(sender)
                elif action == "stop_followups":
                    mark_replied(sender)
                elif action == "send_conversion":
                    mark_interested_reply(sender)
                elif action == "ask_telegram":
                    mark_unclear_reply(sender)
                # "wait" (OOO) → no status change

                new_replies.append({
                    "email": sender,
                    "intent": intent,
                    "action": action,
                    "confidence": classification["confidence"],
                    "reason": classification["reason"],
                    "snippet": body_text[:200].strip(),
                })
                tracked_emails.discard(sender)

            except Exception:
                continue

        imap.close()
        imap.logout()

    except Exception as e:
        print(f"[FOLLOWUP] IMAP check error: {e}")

    return new_replies


# ── Main Follow-Up Cycle ─────────────────────────────────────────────

def run_followup_cycle() -> dict:
    """
    Check for replies and trigger conversion emails for interested leads.

    Flow per reply:
      interested / question → generate conversion email → queue for Telegram approval
      unclear               → queue conversion email with "Unclear Reply" flag in Telegram
      not_interested        → stop (already marked by check_replies)
      bounce                → mark bounced (already handled)
      out_of_office         → do nothing
    """
    from src.telegram_bot import queue_email, _send_message
    from src.ai_personalizer import generate_conversion_email

    print(f"[FOLLOWUP] Running reply check at {datetime.now().strftime('%H:%M:%S')}")

    new_replies = check_replies()
    queued_conversions = 0

    for reply in new_replies:
        email = reply["email"]
        intent = reply["intent"]
        action = reply["action"]
        snippet = reply.get("snippet", "")[:120]
        reason = reply.get("reason", "")
        confidence_pct = int(reply["confidence"] * 100)

        # Get full recipient record for lead data
        conn = _get_db()
        recipient = conn.execute(
            "SELECT * FROM recipient_tracker WHERE email = ?", (email,)
        ).fetchone()
        conn.close()

        if not recipient:
            continue

        lead_data = json.loads(recipient.get("lead_data", "{}"))
        name = lead_data.get("Name", email)

        # ── Telegram notification for every reply ──────────────────
        intent_icon = {
            "interested": "[HOT]",
            "question":   "[QUESTION]",
            "unclear":    "[UNCLEAR]",
            "not_interested": "[DECLINED]",
            "out_of_office":  "[OOO]",
            "bounce":     "[BOUNCE]",
        }
        icon = intent_icon.get(intent, "[REPLY]")

        if config.TELEGRAM_CHAT_ID:
            msg = (
                f"<b>{icon} Reply from {name}</b>\n\n"
                f"<b>Email:</b> {email}\n"
                f"<b>Intent:</b> {intent} ({confidence_pct}% confidence)\n"
                f"<b>Action:</b> {reason}\n"
            )
            if snippet:
                msg += f"\n<pre>{snippet}</pre>"
            _send_message(config.TELEGRAM_CHAT_ID, msg)

        # ── Queue conversion email for interested / question ───────
        if action in ("send_conversion", "ask_telegram"):
            conversion = generate_conversion_email(lead_data)

            # Flag unclear replies so user can identify them in Telegram
            service_label = conversion["service"]
            if action == "ask_telegram":
                service_label = f"[UNCLEAR REPLY] {service_label}"

            queue_email(
                recipient=email,
                subject=conversion["subject"],
                body=conversion["body"],
                lead_data=lead_data,
                service=service_label,
                angle=conversion["angle"],
                sheet_row=recipient["sheet_row"],
            )
            mark_conversion_queued(email)
            queued_conversions += 1
            print(f"[FOLLOWUP] Conversion email queued for {email} (intent: {intent})")

    if new_replies:
        print(f"[FOLLOWUP] Processed {len(new_replies)} replies, queued {queued_conversions} conversion emails")

    return {"replies": len(new_replies), "queued_conversions": queued_conversions}


# ── Background Thread ────────────────────────────────────────────────

def start_followup_loop():
    """Run the follow-up checker in an infinite loop."""
    print(f"[FOLLOWUP] Starting reply monitor (check every {REPLY_CHECK_INTERVAL}s)")
    while True:
        try:
            run_followup_cycle()
        except Exception as e:
            print(f"[FOLLOWUP] Error: {e}")
        time.sleep(REPLY_CHECK_INTERVAL)


def start_followup_thread():
    """Start the reply checker as a background thread."""
    thread = threading.Thread(target=start_followup_loop, daemon=True)
    thread.start()
    print("[FOLLOWUP] Reply monitor thread started.")


def send_daily_summary():
    """Send daily follow-up summary to Telegram."""
    from src.telegram_bot import _send_message
    summary = get_followup_summary()

    text = (
        f"<b>Daily Outreach Summary</b>\n\n"
        f"Total tracked: {summary.get('total', 0)}\n"
        f"Waiting for reply: {summary.get('active', 0)}\n"
        f"Interested (conversion pending): {summary.get('interested_replied', 0)}\n"
        f"Conversion queued: {summary.get('conversion_queued', 0)}\n"
        f"Replied negative: {summary.get('replied', 0)}\n"
        f"Bounced: {summary.get('bounced', 0)}\n"
        f"Stopped (max reached): {summary.get('stopped', 0)}"
    )
    if config.TELEGRAM_CHAT_ID:
        _send_message(config.TELEGRAM_CHAT_ID, text)
