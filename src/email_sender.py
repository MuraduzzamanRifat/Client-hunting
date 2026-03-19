"""
Step 4 — Email Outreach System.

Reads qualified leads from Google Sheets, generates personalized emails
via the AI personalizer, sends via any SMTP provider, and updates the sheet.
Supports Gmail, Outlook, Yahoo, Zoho, or any custom webmail/SMTP server.
"""

import os
import re
import smtplib
import sys
import time
import random
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from src.ai_personalizer import generate_personalized_email
from src.metrics import log_event, log_metric

# Track sent emails in this session to prevent duplicates
_sent_this_session = set()

# Simple email format validation
EMAIL_VALID_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')

# Risky email patterns to skip (catch-all, role-based, disposable)
SKIP_PREFIXES = ["info@", "admin@", "support@", "noreply@", "no-reply@",
                 "contact@", "sales@", "help@", "webmaster@"]
DISPOSABLE_DOMAINS = ["mailinator.com", "tempmail.com", "throwaway.email",
                      "guerrillamail.com", "yopmail.com", "10minutemail.com"]


def _is_safe_email(email_addr: str) -> bool:
    """Check if an email is safe to send to (not role-based, disposable, or risky)."""
    email_lower = email_addr.lower()
    if any(email_lower.startswith(p) for p in SKIP_PREFIXES):
        return False
    domain = email_lower.split("@")[-1] if "@" in email_lower else ""
    if domain in DISPOSABLE_DOMAINS:
        return False
    return True

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def read_qualified_leads(sheets_mgr) -> list[tuple[int, dict]]:
    """
    Read leads from Google Sheets that qualify for email outreach.

    Returns list of (row_index, lead_dict) tuples.
    row_index is 1-based (row 2 = first data row).
    """
    leads = sheets_mgr.read_leads()
    qualified = []

    for i, lead in enumerate(leads):
        row_idx = i + 2  # 1-based, skip header

        email = str(lead.get("Email", "")).strip()
        contacted = str(lead.get("Contacted", "")).strip().lower()
        outreach = str(lead.get("Outreach Type", "")).strip()

        if email and contacted != "yes" and outreach == "Email":
            if EMAIL_VALID_RE.match(email):
                qualified.append((row_idx, lead))

    return qualified


def send_email(smtp_conn, to_email: str, subject: str, body: str, from_email: str) -> bool:
    """Send a single email via SMTP. Returns True on success."""
    msg = MIMEMultipart("alternative")
    msg["From"] = f"Mj <{from_email}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    msg["Reply-To"] = from_email

    # Unsubscribe header (required by Gmail/Yahoo since Feb 2024)
    msg["List-Unsubscribe"] = f"<mailto:{from_email}?subject=Unsubscribe>"
    msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

    # Add unsubscribe line to body
    body_with_unsub = body + "\n\n---\nDon't want to hear from me? Just reply 'unsubscribe' and I'll remove you immediately."

    msg.attach(MIMEText(body_with_unsub, "plain", "utf-8"))

    try:
        smtp_conn.sendmail(from_email, to_email, msg.as_string())
        return True
    except smtplib.SMTPException as e:
        log.warning(f"  Failed to send to {to_email}: {e}")
        return False


from src.retry import retry


@retry(max_attempts=3, delay=5, backoff=2, exceptions=(smtplib.SMTPException, OSError))
def _connect_smtp():
    """
    Establish SMTP connection with any email provider.
    Supports TLS (port 587) and SSL (port 465).
    """
    if not config.EMAIL_USER or not config.EMAIL_PASSWORD:
        log.error("Email credentials not set. Check EMAIL_USER and EMAIL_PASSWORD in .env")
        return None

    try:
        if config.SMTP_USE_SSL:
            # SSL connection (port 465)
            server = smtplib.SMTP_SSL(config.SMTP_SERVER, config.SMTP_PORT, timeout=30)
        else:
            # TLS connection (port 587)
            server = smtplib.SMTP(config.SMTP_SERVER, config.SMTP_PORT, timeout=30)
            server.starttls()

        server.login(config.EMAIL_USER, config.EMAIL_PASSWORD)
        log.info(f"  SMTP connected to {config.SMTP_SERVER}:{config.SMTP_PORT}")
        return server
    except smtplib.SMTPException as e:
        log.error(f"  SMTP connection failed: {e}")
        return None


def run_outreach(sheets_mgr, max_emails: int = 0) -> dict:
    """
    Main outreach loop with deliverability protection.

    1. Read qualified leads from sheet
    2. Generate personalized email for each
    3. Send via SMTP with bounce/rate tracking
    4. Update sheet (Contacted=Yes, Status=Email Sent)
    5. Auto-pause if bounce rate exceeds threshold

    Returns summary stats.
    """
    cap = max_emails or config.MAX_EMAILS_PER_DAY
    max_per_hour = getattr(config, "MAX_EMAILS_PER_HOUR", 15)
    max_bounce_rate = getattr(config, "MAX_BOUNCE_RATE", 0.03)

    print(f"\n{'='*50}")
    print(f"  Email Outreach")
    print(f"  Daily cap: {cap} | Hourly cap: {max_per_hour}")
    print(f"  Max bounce rate: {max_bounce_rate*100:.0f}%")
    print(f"{'='*50}\n")

    qualified = read_qualified_leads(sheets_mgr)
    if not qualified:
        print("  No qualified leads to contact.")
        return {"sent": 0, "failed": 0, "skipped": 0, "bounced": 0}

    print(f"  Qualified leads: {len(qualified)}")
    to_send = qualified[:cap]
    print(f"  Will send: {len(to_send)} (capped at {cap})\n")

    # Connect SMTP
    smtp = _connect_smtp()
    if not smtp:
        return {"sent": 0, "failed": 0, "skipped": len(to_send), "bounced": 0}

    sent = 0
    failed = 0
    bounced = 0
    hour_count = 0
    hour_start = time.time()
    paused = False

    try:
        for i, (row_idx, lead) in enumerate(to_send, 1):
            # Hourly rate limit
            if hour_count >= max_per_hour:
                elapsed = time.time() - hour_start
                if elapsed < 3600:
                    wait = 3600 - elapsed
                    print(f"  Hourly limit reached ({max_per_hour}). Waiting {wait/60:.0f}min...")
                    time.sleep(wait)
                hour_count = 0
                hour_start = time.time()

            # Bounce rate protection
            total_attempts = sent + failed
            if total_attempts >= 5:  # need minimum sample
                current_bounce_rate = failed / total_attempts
                if current_bounce_rate > max_bounce_rate:
                    print(f"\n  PAUSED: Bounce rate {current_bounce_rate*100:.1f}% exceeds {max_bounce_rate*100:.0f}% threshold")
                    print(f"  Sent {sent}, Failed {failed}. Stopping to protect domain reputation.")
                    paused = True
                    break

            name = lead.get("Name", "Unknown")
            email_addr = lead["Email"]

            # Skip risky emails
            if not _is_safe_email(email_addr):
                print(f"  [{i}/{len(to_send)}] Skipping {email_addr} (risky/role-based)")
                continue

            # Skip duplicates
            if email_addr.lower() in _sent_this_session:
                print(f"  [{i}/{len(to_send)}] Skipping {email_addr} (duplicate)")
                continue
            _sent_this_session.add(email_addr.lower())

            # Generate personalized email
            email_content = generate_personalized_email(lead)
            subject = email_content["subject"]
            body = email_content["body"]

            print(f"  [{i}/{len(to_send)}] Sending to {name} ({email_addr})...", end=" ")

            from_addr = config.EMAIL_FROM or config.EMAIL_USER
            success = send_email(smtp, email_addr, subject, body, from_addr)

            if success:
                sent += 1
                hour_count += 1
                print("sent")
                log_event("sent", recipient=email_addr, subject=subject, status="ok")
                sheets_mgr.update_row(row_idx, {
                    "Contacted": "Yes",
                    "Status": "Email Sent",
                    "Notes": f"Sent {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                })
            else:
                failed += 1
                bounced += 1
                print("failed")
                log_event("bounced", recipient=email_addr, subject=subject, status="failed")

            # Human-like random delay
            if i < len(to_send):
                delay = random.uniform(config.EMAIL_DELAY_MIN, config.EMAIL_DELAY_MAX)
                print(f"      Waiting {delay:.0f}s...")
                time.sleep(delay)

    finally:
        smtp.quit()
        log.info("  SMTP connection closed.")

    status = "PAUSED (bounce protection)" if paused else "complete"
    print(f"\n  Outreach {status}: {sent} sent, {failed} failed, {bounced} bounced")
    return {"sent": sent, "failed": failed, "skipped": len(qualified) - len(to_send), "bounced": bounced}


def run_outreach_with_approval(sheets_mgr, max_emails: int = 0) -> dict:
    """
    Queue emails for Telegram approval instead of sending directly.
    Uses the approval queue when APPROVAL_MODE = 'telegram'.
    Falls back to direct sending when APPROVAL_MODE = 'auto'.
    """
    if config.APPROVAL_MODE == "auto":
        return run_outreach(sheets_mgr, max_emails)

    from src.telegram_bot import queue_email

    cap = max_emails or config.MAX_EMAILS_PER_DAY

    print(f"\n{'='*50}")
    print(f"  Email Outreach (Telegram Approval Mode)")
    print(f"  Daily cap: {cap}")
    print(f"{'='*50}\n")

    qualified = read_qualified_leads(sheets_mgr)
    if not qualified:
        print("  No qualified leads to contact.")
        return {"queued": 0, "skipped": 0}

    to_queue = qualified[:cap]
    print(f"  Qualified: {len(qualified)} | Queueing: {len(to_queue)}\n")

    queued = 0
    for i, (row_idx, lead) in enumerate(to_queue, 1):
        email_addr = lead["Email"]
        name = lead.get("Name", "Unknown")

        if not _is_safe_email(email_addr):
            print(f"  [{i}/{len(to_queue)}] Skipping {email_addr} (risky)")
            continue

        if email_addr.lower() in _sent_this_session:
            print(f"  [{i}/{len(to_queue)}] Skipping {email_addr} (duplicate)")
            continue
        _sent_this_session.add(email_addr.lower())

        email_content = generate_personalized_email(lead)
        queue_email(
            recipient=email_addr,
            subject=email_content["subject"],
            body=email_content["body"],
            lead_data=lead,
            service=email_content["service"],
            angle=email_content["angle"],
            sheet_row=row_idx,
        )
        queued += 1
        print(f"  [{i}/{len(to_queue)}] Queued for approval: {name} ({email_addr})")

    print(f"\n  Queued {queued} emails for Telegram approval")
    return {"queued": queued, "skipped": len(qualified) - queued}


# ── Standalone test ──────────────────────────────────────────────────
if __name__ == "__main__":
    print("Email sender module. Run via main.py for full pipeline.")
    print("To test, run: python main.py --skip-email")
