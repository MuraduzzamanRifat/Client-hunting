"""
Follow-Up Engine — Tracks recipients, detects replies, schedules follow-ups.

Rules:
  - Max 3 emails per recipient (initial + 2 follow-ups)
  - Stop immediately if reply detected
  - Stop if any email bounced
  - Follow-up delays: 3 days, 5 days (configurable)
  - Each follow-up uses a different tone/angle
  - All follow-ups go through Telegram approval
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

# ── Config ───────────────────────────────────────────────────────────
MAX_EMAILS_PER_RECIPIENT = 3
FOLLOWUP_DELAYS_DAYS = [3, 5]  # days after 1st email, days after 2nd
REPLY_CHECK_INTERVAL = 300  # seconds (5 min)


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
        CREATE INDEX IF NOT EXISTS idx_tracker_followup ON recipient_tracker(next_followup_at);
    """)
    conn.close()


init_followup_db()


# ── Recipient Tracking ───────────────────────────────────────────────

def track_sent(email: str, name: str = "", lead_data: dict = None, sheet_row: int = 0):
    """Record that an email was sent to this recipient."""
    now = datetime.now().isoformat()
    email_lower = email.lower().strip()

    with _lock:
        conn = _get_db()
        row = conn.execute("SELECT * FROM recipient_tracker WHERE email = ?", (email_lower,)).fetchone()

        if row:
            new_count = row["emails_sent"] + 1
            if new_count >= MAX_EMAILS_PER_RECIPIENT:
                status = "stopped"
                next_followup = ""
            else:
                status = "active"
                delay_idx = min(new_count - 1, len(FOLLOWUP_DELAYS_DAYS) - 1)
                next_date = datetime.now() + timedelta(days=FOLLOWUP_DELAYS_DAYS[delay_idx])
                next_followup = next_date.isoformat()

            conn.execute(
                "UPDATE recipient_tracker SET emails_sent=?, last_sent_at=?, "
                "next_followup_at=?, status=?, updated_at=? WHERE email=?",
                (new_count, now, next_followup, status, now, email_lower)
            )
        else:
            next_date = datetime.now() + timedelta(days=FOLLOWUP_DELAYS_DAYS[0])
            conn.execute(
                "INSERT INTO recipient_tracker (email, name, lead_data, emails_sent, last_sent_at, "
                "next_followup_at, status, created_at, updated_at, sheet_row) "
                "VALUES (?, ?, ?, 1, ?, ?, 'active', ?, ?, ?)",
                (email_lower, name, json.dumps(lead_data or {}), now,
                 next_date.isoformat(), now, now, sheet_row)
            )

        conn.commit()
        conn.close()


def mark_replied(email: str):
    """Mark recipient as replied — stops all follow-ups."""
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


def mark_bounced(email: str):
    """Mark recipient as bounced — stops all follow-ups."""
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
    """Check if we can send to this recipient."""
    conn = _get_db()
    row = conn.execute("SELECT * FROM recipient_tracker WHERE email = ?",
                       (email.lower().strip(),)).fetchone()
    conn.close()

    if not row:
        return True  # never contacted
    if row["status"] in ("stopped", "replied", "bounced"):
        return False
    if row["emails_sent"] >= MAX_EMAILS_PER_RECIPIENT:
        return False
    return True


def get_due_followups() -> list[dict]:
    """Get recipients due for a follow-up."""
    now = datetime.now().isoformat()
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM recipient_tracker WHERE status = 'active' "
        "AND next_followup_at != '' AND next_followup_at <= ? "
        "AND emails_sent < ?",
        (now, MAX_EMAILS_PER_RECIPIENT)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


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
    summary["total"] = sum(summary.values())
    return summary


# ── Follow-Up Email Generation ───────────────────────────────────────
#
# Strategy per attempt:
#   Follow-up 1 (friendly reminder): Short, casual, "just bumping this up"
#   Follow-up 2 (value-driven): New angle, fresh insight, different benefit
#   Follow-up 3 (soft close): "Should I stop reaching out?" — triggers loss aversion
#
# Rules:
#   - Never reference "my previous email" or "following up" in subject
#   - Each email must feel like a standalone message, not a chain
#   - No exclamation marks, no ALL CAPS, no "just checking in"
#   - Under 80 words body (short = human)

def _detect_service(lead_data: dict) -> str:
    """Detect service angle from lead data."""
    has_website = bool(lead_data.get("Website"))
    if not has_website:
        return "website"
    try:
        rating = float(lead_data.get("Rating", 0) or 0)
    except (ValueError, TypeError):
        rating = 0
    return "seo_fix" if 0 < rating < 4.0 else "seo_grow"


def generate_followup_email(recipient: dict) -> dict:
    """
    Generate a context-aware follow-up email.
    Each attempt uses a completely different structure, tone, and angle.
    """
    import random
    lead_data = json.loads(recipient.get("lead_data", "{}"))
    name = lead_data.get("Name", recipient.get("name", "there"))
    attempt = recipient["emails_sent"]  # 1 = first followup, 2 = final
    service = _detect_service(lead_data)

    try:
        rating = float(lead_data.get("Rating", 0) or 0)
    except (ValueError, TypeError):
        rating = 0
    try:
        reviews = int(str(lead_data.get("Reviews", 0) or 0).replace(",", ""))
    except (ValueError, TypeError):
        reviews = 0

    if attempt == 1:
        email = _followup_friendly_reminder(name, service, rating, reviews, lead_data)
    else:
        email = _followup_soft_close(name, service, lead_data)

    signoff = random.choice(["Cheers", "Talk soon", "Best", "Take care"])
    email["body"] += f"{signoff},\nMj\nmjrifat.com"
    email["body"] += "\n\n---\nDon't want to hear from me? Just reply 'stop' and I'll remove you immediately."
    return email


def _followup_friendly_reminder(name: str, service: str, rating: float, reviews: int, lead_data: dict) -> dict:
    """Follow-up 1: Friendly, short, new angle — NOT a 'just checking in'."""
    import random

    if service == "website":
        options = [
            {
                "subject": f"Thought of {name} today",
                "body": (
                    f"Hey {name},\n\n"
                    "I was helping another local business get their first website set up this week "
                    "and it reminded me of you.\n\n"
                    "They went from zero online presence to getting calls within the first month. "
                    "Nothing fancy — just a clean site that shows up on Google.\n\n"
                    "If that sounds useful, I'd be happy to share what I did. Just reply.\n\n"
                ),
            },
            {
                "subject": f"Quick question for {name}",
                "body": (
                    f"Hey {name},\n\n"
                    "Genuine question — do people ever ask if you have a website?\n\n"
                    "I ask because I work with local businesses and it's one of the first things "
                    "customers look for. If you've thought about it but haven't gotten around to it, "
                    "I might be able to help.\n\n"
                    "Either way, no pressure. Just curious.\n\n"
                ),
            },
            {
                "subject": f"Something I noticed about {name}",
                "body": (
                    f"Hi {name},\n\n"
                    f"I looked up your business again and you've got {reviews} reviews "
                    f"with a {rating} rating — that's really solid.\n\n"
                    "The only thing missing is a website where people can learn more "
                    "before they visit. I build these for local businesses and it's "
                    "usually simpler than people expect.\n\n"
                    "Interested in seeing what it could look like?\n\n"
                ),
            },
        ]
    elif service == "seo_fix":
        options = [
            {
                "subject": f"Found something about {name} on Google",
                "body": (
                    f"Hey {name},\n\n"
                    "I ran a quick search for businesses like yours in your area "
                    "and noticed you're not showing up where I'd expect.\n\n"
                    "There are a few things that could fix that pretty quickly. "
                    "Would you want me to share what I found? Takes two minutes to explain.\n\n"
                ),
            },
            {
                "subject": f"Competitors are ahead of {name} on Google",
                "body": (
                    f"Hi {name},\n\n"
                    "I was doing some research and noticed a few of your competitors "
                    "are ranking above you for the keywords your customers are searching.\n\n"
                    "It's fixable — usually a few tweaks make a big difference. "
                    "Happy to show you what I mean if you're curious.\n\n"
                ),
            },
        ]
    else:  # seo_grow
        options = [
            {
                "subject": f"Idea for {name} — more traffic from Google",
                "body": (
                    f"Hey {name},\n\n"
                    "I was thinking about your business and had an idea. "
                    f"With {reviews} reviews and a {rating}-star rating, you've already "
                    "got the trust factor. The next step is getting more people to find you.\n\n"
                    "I've been helping businesses set up automated blog content that "
                    "keeps Google happy. It runs on autopilot once it's set up.\n\n"
                    "Want me to explain how it works? Quick reply is all I need.\n\n"
                ),
            },
            {
                "subject": f"{name} — saw something interesting",
                "body": (
                    f"Hi {name},\n\n"
                    "I was looking at your area and there's a gap in Google search results "
                    "for businesses like yours. The ones filling that gap are getting "
                    "a lot of free traffic.\n\n"
                    "I think you could be one of them with the right SEO setup. "
                    "Would it be helpful if I showed you what I mean?\n\n"
                ),
            },
        ]

    return random.choice(options)


def _followup_soft_close(name: str, service: str, lead_data: dict) -> dict:
    """Follow-up 2 (final): Soft close — 'should I stop reaching out?' triggers response."""
    import random

    options = [
        {
            "subject": f"Should I close your file, {name}?",
            "body": (
                f"Hey {name},\n\n"
                "I've reached out a couple of times and haven't heard back, "
                "which is totally fine — you're busy running a business.\n\n"
                "I just want to make sure I'm not cluttering your inbox. "
                "Should I close your file on my end?\n\n"
                "If the timing is just off, no worries — reply whenever makes sense.\n\n"
            ),
        },
        {
            "subject": f"One last thing — {name}",
            "body": (
                f"Hi {name},\n\n"
                "This is my last reach-out. I don't want to be that person "
                "who keeps showing up in your inbox uninvited.\n\n"
                "If you ever want to chat about "
                f"{'getting a website' if service == 'website' else 'improving your Google presence'}"
                ", my door is always open. Just reply to this email anytime.\n\n"
                "Either way, wishing you all the best with the business.\n\n"
            ),
        },
        {
            "subject": f"Not a good fit, {name}?",
            "body": (
                f"Hey {name},\n\n"
                "Completely understand if this isn't the right time or "
                "if it's just not something you need right now.\n\n"
                "I'll stop reaching out after this. But if anything changes down the road, "
                "feel free to reply to any of my emails — I'll still be here.\n\n"
                "All the best.\n\n"
            ),
        },
        {
            "subject": f"Quick yes or no — {name}",
            "body": (
                f"Hey {name},\n\n"
                "I know your time is valuable so I'll keep this to one question:\n\n"
                f"Is {'getting a website' if service == 'website' else 'growing your online presence'} "
                "something you'd want to explore this year?\n\n"
                "A simple yes or no is totally fine. Either way, I respect your answer.\n\n"
            ),
        },
    ]

    return random.choice(options)


# ── Reply Detection ──────────────────────────────────────────────────

def check_replies():
    """Check IMAP inbox for replies from tracked recipients. Classifies intent."""
    import imaplib
    import email as email_lib
    import re
    from src.reply_classifier import classify_reply

    if not config.IMAP_USER or not config.IMAP_PASSWORD:
        return []

    conn = _get_db()
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

                # Extract sender
                from_header = msg.get("From", "")
                from_match = re.search(r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', from_header)
                if not from_match:
                    continue
                sender = from_match.group(1).lower()

                if sender not in tracked_emails:
                    continue

                # Extract body for classification
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

                # Classify intent
                classification = classify_reply(body_text, sender)
                intent = classification["intent"]
                action = classification["action"]

                if action == "mark_bounced":
                    mark_bounced(sender)
                elif action == "stop_followups":
                    mark_replied(sender)  # still counts as replied
                elif action == "wait":
                    pass  # OOO — don't change status, wait
                else:
                    mark_replied(sender)

                new_replies.append({
                    "email": sender,
                    "intent": intent,
                    "confidence": classification["confidence"],
                    "reason": classification["reason"],
                    "action": action,
                    "snippet": body_text[:150].strip(),
                })
                tracked_emails.discard(sender)

            except Exception:
                continue

        imap.close()
        imap.logout()

    except Exception as e:
        print(f"[FOLLOWUP] IMAP reply check error: {e}")

    return new_replies


# ── Follow-Up Scheduler ──────────────────────────────────────────────

def run_followup_cycle():
    """
    Run one follow-up cycle:
    1. Check for replies
    2. Queue due follow-ups for Telegram approval
    3. Notify about stopped recipients
    """
    from src.telegram_bot import queue_email, _send_message

    print(f"[FOLLOWUP] Running cycle at {datetime.now().strftime('%H:%M:%S')}")

    # Step 1: Check replies with intent classification
    new_replies = check_replies()
    if new_replies:
        for reply in new_replies:
            email = reply["email"]
            intent = reply["intent"]
            snippet = reply.get("snippet", "")[:100]
            reason = reply.get("reason", "")

            intent_emoji = {
                "interested": "🔥", "not_interested": "❌",
                "question": "❓", "out_of_office": "📅", "bounce": "⚠️"
            }
            emoji = intent_emoji.get(intent, "📩")

            print(f"[FOLLOWUP] Reply from {email}: {intent} ({reason})")

            if config.TELEGRAM_CHAT_ID:
                msg = (
                    f"{emoji} <b>Reply Detected</b>\n\n"
                    f"<b>From:</b> {email}\n"
                    f"<b>Intent:</b> {intent} ({int(reply['confidence']*100)}% confidence)\n"
                    f"<b>Action:</b> {reason}\n"
                )
                if snippet:
                    msg += f"\n<pre>{snippet}</pre>"
                _send_message(config.TELEGRAM_CHAT_ID, msg)

    # Step 2: Queue due follow-ups
    due = get_due_followups()
    queued = 0
    stopped = 0

    for recipient in due:
        email = recipient["email"]
        lead_data = json.loads(recipient.get("lead_data", "{}"))

        if recipient["emails_sent"] >= MAX_EMAILS_PER_RECIPIENT:
            # Mark as stopped
            with _lock:
                conn = _get_db()
                conn.execute(
                    "UPDATE recipient_tracker SET status='stopped', next_followup_at='', updated_at=? WHERE email=?",
                    (datetime.now().isoformat(), email)
                )
                conn.commit()
                conn.close()
            stopped += 1
            continue

        # Generate follow-up
        followup = generate_followup_email(recipient)
        queue_email(
            recipient=email,
            subject=followup["subject"],
            body=followup["body"],
            lead_data=lead_data,
            service="Follow-up",
            angle=f"followup_{recipient['emails_sent'] + 1}",
            sheet_row=recipient.get("sheet_row", 0),
        )
        queued += 1

    if queued or stopped:
        print(f"[FOLLOWUP] Queued: {queued} follow-ups, Stopped: {stopped} recipients")

    # Step 3: Notify if any stopped
    if stopped and config.TELEGRAM_CHAT_ID:
        from src.telegram_bot import _send_message
        _send_message(config.TELEGRAM_CHAT_ID,
                      f"<b>{stopped} recipient(s) reached follow-up limit</b>\n"
                      f"Status set to: No Response - Stopped")

    return {"replies": len(new_replies), "queued": queued, "stopped": stopped}


def start_followup_loop():
    """Run the follow-up checker in a loop."""
    print(f"[FOLLOWUP] Starting loop (check every {REPLY_CHECK_INTERVAL}s)")
    while True:
        try:
            run_followup_cycle()
        except Exception as e:
            print(f"[FOLLOWUP] Error: {e}")
        time.sleep(REPLY_CHECK_INTERVAL)


def start_followup_thread():
    """Start follow-up checker as background thread."""
    thread = threading.Thread(target=start_followup_loop, daemon=True)
    thread.start()
    print("[FOLLOWUP] Background thread started.")


def send_daily_summary():
    """Send daily summary to Telegram."""
    from src.telegram_bot import _send_message
    summary = get_followup_summary()

    text = (
        f"<b>Daily Follow-Up Summary</b>\n\n"
        f"Total tracked: {summary.get('total', 0)}\n"
        f"Active: {summary.get('active', 0)}\n"
        f"Replied: {summary.get('replied', 0)}\n"
        f"Stopped (no response): {summary.get('stopped', 0)}\n"
        f"Bounced: {summary.get('bounced', 0)}"
    )
    if config.TELEGRAM_CHAT_ID:
        _send_message(config.TELEGRAM_CHAT_ID, text)
