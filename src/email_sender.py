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

# Simple email format validation
EMAIL_VALID_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')

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
    msg["From"] = from_email
    msg["To"] = to_email
    msg["Subject"] = subject

    # Plain text version
    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        smtp_conn.sendmail(from_email, to_email, msg.as_string())
        return True
    except smtplib.SMTPException as e:
        log.warning(f"  Failed to send to {to_email}: {e}")
        return False


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
    Main outreach loop.

    1. Read qualified leads from sheet
    2. Generate personalized email for each
    3. Send via Gmail SMTP
    4. Update sheet (Contacted=Yes, Status=Email Sent)
    5. Respect daily cap and delays

    Returns summary stats.
    """
    cap = max_emails or config.MAX_EMAILS_PER_DAY

    print(f"\n{'='*50}")
    print(f"  Email Outreach")
    print(f"  Daily cap: {cap}")
    print(f"{'='*50}\n")

    qualified = read_qualified_leads(sheets_mgr)
    if not qualified:
        print("  No qualified leads to contact.")
        return {"sent": 0, "failed": 0, "skipped": 0}

    print(f"  Qualified leads: {len(qualified)}")
    to_send = qualified[:cap]
    print(f"  Will send: {len(to_send)} (capped at {cap})\n")

    # Connect SMTP
    smtp = _connect_smtp()
    if not smtp:
        return {"sent": 0, "failed": 0, "skipped": len(to_send)}

    sent = 0
    failed = 0

    try:
        for i, (row_idx, lead) in enumerate(to_send, 1):
            name = lead.get("Name", "Unknown")
            email = lead["Email"]

            # Generate personalized email
            email_content = generate_personalized_email(lead)
            subject = email_content["subject"]
            body = email_content["body"]

            print(f"  [{i}/{len(to_send)}] Sending to {name} ({email})...", end=" ")

            from_addr = config.EMAIL_FROM or config.EMAIL_USER
            success = send_email(smtp, email, subject, body, from_addr)

            if success:
                sent += 1
                print("sent")
                # Update sheet
                sheets_mgr.update_row(row_idx, {
                    "Contacted": "Yes",
                    "Status": "Email Sent",
                    "Notes": f"Sent {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                })
            else:
                failed += 1
                print("failed")

            # Delay between sends
            if i < len(to_send):
                delay = random.uniform(config.EMAIL_DELAY_MIN, config.EMAIL_DELAY_MAX)
                print(f"      Waiting {delay:.0f}s...")
                time.sleep(delay)

    finally:
        smtp.quit()
        log.info("  SMTP connection closed.")

    print(f"\n  Outreach complete: {sent} sent, {failed} failed")
    return {"sent": sent, "failed": failed, "skipped": len(qualified) - len(to_send)}


# ── Standalone test ──────────────────────────────────────────────────
if __name__ == "__main__":
    print("Email sender module. Run via main.py for full pipeline.")
    print("To test, run: python main.py --skip-email")
