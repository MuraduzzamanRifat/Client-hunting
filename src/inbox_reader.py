"""
Inbox Reader — Fetches and categorizes incoming emails via IMAP.
Uses only Python stdlib (imaplib, email) — no extra dependencies.
"""

import imaplib
import email
import email.header
import email.utils
import re
from datetime import datetime
from html import unescape

import config


# ── Category keywords ────────────────────────────────────────────────
MARKETING_KEYWORDS = [
    "unsubscribe", "newsletter", "promo", "promotion", "offer", "deal",
    "discount", "sale", "campaign", "limited time", "act now", "free trial",
    "click here", "opt out", "opt-out", "bulk", "mass mail", "coupon",
]
MARKETING_DOMAINS = [
    "mailchimp.com", "sendgrid.net", "hubspot.com", "constantcontact.com",
    "mailgun.org", "sendinblue.com", "brevo.com", "drip.com", "klaviyo.com",
    "convertkit.com", "getresponse.com", "aweber.com", "campaignmonitor.com",
]
BUSINESS_KEYWORDS = [
    "invoice", "proposal", "meeting", "contract", "partnership", "order",
    "payment", "quote", "inquiry", "lead", "project", "deadline", "budget",
    "estimate", "agreement", "consultation", "appointment", "schedule",
    "service", "client", "customer", "delivery", "shipment", "receipt",
]


def _decode_header(raw: str) -> str:
    """Decode RFC 2047 encoded email headers."""
    if not raw:
        return ""
    parts = email.header.decode_header(raw)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return " ".join(decoded)


def _strip_html(html: str) -> str:
    """Remove HTML tags and decode entities."""
    text = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _get_body(msg: email.message.Message) -> str:
    """Extract plain text body from email message."""
    if msg.is_multipart():
        plain = ""
        html = ""
        for part in msg.walk():
            ct = part.get_content_type()
            disp = str(part.get("Content-Disposition", ""))
            if "attachment" in disp:
                continue
            try:
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                charset = part.get_content_charset() or "utf-8"
                text = payload.decode(charset, errors="replace")
            except Exception:
                continue
            if ct == "text/plain":
                plain = text
            elif ct == "text/html" and not plain:
                html = text
        return plain if plain else _strip_html(html)
    else:
        try:
            payload = msg.get_payload(decode=True)
            if not payload:
                return ""
            charset = msg.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace")
            if msg.get_content_type() == "text/html":
                return _strip_html(text)
            return text
        except Exception:
            return ""


def _parse_date(msg: email.message.Message) -> str:
    """Parse email date to ISO format string."""
    date_str = msg.get("Date", "")
    try:
        parsed = email.utils.parsedate_to_datetime(date_str)
        return parsed.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return date_str[:20] if date_str else "Unknown"


def _extract_sender_email(from_header: str) -> str:
    """Extract just the email address from a From header."""
    match = re.search(r"<([^>]+)>", from_header)
    if match:
        return match.group(1).lower()
    if "@" in from_header:
        return from_header.strip().lower()
    return from_header


def categorize_email(email_dict: dict) -> str:
    """Categorize an email as business, personal, or marketing."""
    subject = email_dict.get("subject", "").lower()
    sender = email_dict.get("sender_email", "").lower()
    snippet = email_dict.get("snippet", "").lower()
    text = f"{subject} {snippet}"

    # Check marketing first (most distinct signals)
    sender_domain = sender.split("@")[-1] if "@" in sender else ""
    if any(d in sender_domain for d in MARKETING_DOMAINS):
        return "marketing"
    if any(kw in text for kw in MARKETING_KEYWORDS):
        return "marketing"

    # Check business
    if any(kw in text for kw in BUSINESS_KEYWORDS):
        return "business"

    return "personal"


def fetch_emails(limit: int = None) -> list[dict]:
    """Fetch recent emails from IMAP inbox."""
    if limit is None:
        limit = config.INBOX_FETCH_LIMIT

    if not config.IMAP_USER or not config.IMAP_PASSWORD:
        print("[INBOX] IMAP credentials not configured")
        return []

    emails = []
    conn = None

    try:
        conn = imaplib.IMAP4_SSL(config.IMAP_SERVER, config.IMAP_PORT, timeout=15)
        conn.login(config.IMAP_USER, config.IMAP_PASSWORD)
        conn.select("INBOX", readonly=True)

        # Get message IDs (newest first)
        status, data = conn.search(None, "ALL")
        if status != "OK" or not data[0]:
            return []

        msg_ids = data[0].split()
        # Take the last N (newest)
        msg_ids = msg_ids[-limit:]
        msg_ids.reverse()  # newest first

        for msg_id in msg_ids:
            try:
                # BODY.PEEK so emails stay unread
                status, msg_data = conn.fetch(msg_id, "(BODY.PEEK[])")
                if status != "OK":
                    continue

                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw)

                subject = _decode_header(msg.get("Subject", "(No subject)"))
                from_raw = _decode_header(msg.get("From", "Unknown"))
                sender_email = _extract_sender_email(msg.get("From", ""))
                date_str = _parse_date(msg)
                body = _get_body(msg)
                snippet = body[:200].strip() if body else ""

                email_dict = {
                    "id": msg_id.decode() if isinstance(msg_id, bytes) else str(msg_id),
                    "subject": subject,
                    "sender": from_raw,
                    "sender_email": sender_email,
                    "date": date_str,
                    "snippet": snippet,
                    "read": False,
                }
                email_dict["category"] = categorize_email(email_dict)
                emails.append(email_dict)

            except Exception as e:
                print(f"[INBOX] Failed to parse message {msg_id}: {e}")
                continue

    except imaplib.IMAP4.error as e:
        print(f"[INBOX] IMAP error: {e}")
    except Exception as e:
        print(f"[INBOX] Connection error: {e}")
    finally:
        if conn:
            try:
                conn.close()
                conn.logout()
            except Exception:
                pass

    return emails


def get_inbox(limit: int = None) -> dict:
    """Fetch emails and return with category counts."""
    emails = fetch_emails(limit)
    counts = {"business": 0, "personal": 0, "marketing": 0, "total": len(emails)}
    for e in emails:
        cat = e.get("category", "personal")
        if cat in counts:
            counts[cat] += 1
    return {"emails": emails, "counts": counts}
