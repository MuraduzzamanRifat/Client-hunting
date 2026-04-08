"""Email sender via SMTP — fully automated.

Sends initial emails + follow-ups. Plain text only (no spam flags).
Random delays between emails to look human.
"""

import random
import smtplib
import imaplib
import time
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

from config import (
    SMTP_HOST, SMTP_PORT, SMTP_EMAIL, SMTP_PASSWORD,
    SENDER_NAME, DAILY_SEND_LIMIT, SEND_DELAY_MIN, SEND_DELAY_MAX,
    IMAP_HOST, IMAP_PORT, IMAP_EMAIL, IMAP_PASSWORD,
    EXTENSION_URL, PURCHASE_EXTENSION_URL,
)
from database import (
    get_unsent_emails, get_followup_emails,
    mark_sent, mark_failed, get_today_send_count, was_sent_today,
)
from templates import get_template, get_followup_template
from validator import validate_email

log = logging.getLogger("outreach.sender")


def create_email(to_email, subject, body):
    """Create email with clean HTML links (hides UTM params)."""
    msg = MIMEMultipart('alternative')
    msg['From'] = f'{SENDER_NAME} <{SMTP_EMAIL}>'
    msg['To'] = to_email
    msg['Subject'] = subject

    # Plain text fallback (strip UTM for plain)
    plain_body = body.replace(EXTENSION_URL, 'https://proworkspace.online/')
    plain_body = plain_body.replace(PURCHASE_EXTENSION_URL, 'https://proworkspace.online/purchase')
    msg.attach(MIMEText(plain_body, 'plain', 'utf-8'))

    # HTML version — clean anchor links
    html_body = body.replace('\n', '<br>\n')
    html_body = html_body.replace(
        EXTENSION_URL,
        f'<a href="{EXTENSION_URL}">proworkspace.online</a>'
    )
    html_body = html_body.replace(
        PURCHASE_EXTENSION_URL,
        f'<a href="{PURCHASE_EXTENSION_URL}">proworkspace.online/purchase</a>'
    )
    html_body = f'<div style="font-family:sans-serif;font-size:14px;color:#222;">{html_body}</div>'
    msg.attach(MIMEText(html_body, 'html', 'utf-8'))

    return msg


def save_to_sent_folder(msg):
    """Save a copy of sent email to IMAP Sent folder."""
    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        imap.login(IMAP_EMAIL, IMAP_PASSWORD)
        # Try common Sent folder names
        for folder in ['INBOX.Sent', 'Sent', 'INBOX/Sent']:
            status, _ = imap.select(folder)
            if status == 'OK':
                imap.append(folder, '\\Seen', None, msg.as_bytes())
                break
        imap.logout()
    except Exception as e:
        log.warning(f"Could not save to Sent folder: {e}")


def connect_smtp():
    """Connect and login to SMTP. Returns connection or None."""
    try:
        if SMTP_PORT == 465:
            smtp = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30)
        else:
            smtp = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30)
            smtp.starttls()
        smtp.login(SMTP_EMAIL, SMTP_PASSWORD)
        return smtp
    except Exception as e:
        log.error(f"SMTP connection failed: {e}")
        return None


MAX_BOUNCE_RATE = 0.03  # 3% — stop sending if exceeded


def send_batch(smtp, emails, template_fn, email_type, sent_count, total_limit):
    """Send a batch of emails. Returns number sent."""
    batch_sent = 0
    batch_bounced = 0

    for email_row in emails:
        if sent_count + batch_sent >= total_limit:
            break

        # Auto-stop if bounce rate exceeds 3%
        if batch_sent > 5 and batch_bounced / batch_sent > MAX_BOUNCE_RATE:
            log.error(f"BOUNCE RATE {batch_bounced}/{batch_sent} exceeds 3% — STOPPING to protect domain")
            break

        # Skip if already sent today (prevent double-send)
        if was_sent_today(email_row['id']):
            log.info(f"  Skipping {email_row['email']} (already sent today)")
            continue

        # Validate email before sending
        is_valid, reason = validate_email(email_row['email'])
        if not is_valid:
            log.info(f"  Skipping {email_row['email']} (invalid: {reason})")
            mark_failed(email_row['id'])
            continue

        try:
            if email_type == 'followup':
                subject, body = template_fn(
                    name=email_row['name'],
                    followup_num=email_row['followup_count'] + 1
                )
            else:
                subject, body = template_fn(name=email_row['name'])

            to = email_row['email']
            log.info(f"[{email_type}] {to} — {subject}")

            msg = create_email(to, subject, body)
            smtp.sendmail(SMTP_EMAIL, to, msg.as_string())
            save_to_sent_folder(msg)

            mark_sent(email_row['id'], subject, 'smtp', email_type)
            batch_sent += 1

            # Random delay
            if batch_sent < len(emails):
                delay = random.uniform(SEND_DELAY_MIN, SEND_DELAY_MAX)
                log.info(f"  Waiting {delay:.0f}s...")
                time.sleep(delay)

        except smtplib.SMTPRecipientsRefused:
            log.warning(f"  BOUNCED: {email_row['email']}")
            mark_failed(email_row['id'])
            batch_bounced += 1
        except smtplib.SMTPException as e:
            log.warning(f"  SMTP error: {e}")
            mark_failed(email_row['id'])
            # Reconnect
            try:
                smtp.quit()
            except Exception:
                pass
            new_smtp = connect_smtp()
            if new_smtp:
                smtp = new_smtp
            else:
                log.error("SMTP reconnect failed — stopping batch")
                break
        except Exception as e:
            log.warning(f"  Error: {e}")
            mark_failed(email_row['id'])

    return batch_sent


def start_sender():
    """Main sending routine — initial emails + follow-ups."""
    if not SMTP_HOST or not SMTP_PASSWORD:
        log.error("SMTP not configured — fill SMTP_HOST and SMTP_PASSWORD in config.py")
        return 0

    today_count = get_today_send_count()
    remaining = DAILY_SEND_LIMIT - today_count

    if remaining <= 0:
        log.info(f"Daily limit reached ({DAILY_SEND_LIMIT} sent today)")
        return 0

    # Get emails to send
    new_emails = get_unsent_emails(limit=remaining)
    followup_emails = get_followup_emails(limit=max(0, remaining - len(new_emails)))

    if not new_emails and not followup_emails:
        log.info("No emails to send (no new, no follow-ups due)")
        return 0

    log.info(f"To send: {len(new_emails)} new + {len(followup_emails)} follow-ups (today: {today_count}/{DAILY_SEND_LIMIT})")

    smtp = connect_smtp()
    if not smtp:
        return 0

    log.info(f"SMTP connected: {SMTP_EMAIL} @ {SMTP_HOST}")

    total_sent = 0

    # Send new emails first
    if new_emails:
        sent = send_batch(smtp, new_emails, get_template, 'initial', total_sent, remaining)
        total_sent += sent
        log.info(f"Initial: {sent} sent")

    # Then follow-ups
    if followup_emails and total_sent < remaining:
        sent = send_batch(smtp, followup_emails, get_followup_template, 'followup', total_sent, remaining)
        total_sent += sent
        log.info(f"Follow-ups: {sent} sent")

    try:
        smtp.quit()
    except Exception:
        pass

    log.info(f"Done: {total_sent} total emails sent")
    return total_sent


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(message)s')
    start_sender()
