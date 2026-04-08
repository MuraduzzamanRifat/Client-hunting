"""Email sender via SMTP — fully automated.

Sends initial emails + follow-ups. Plain text only (no spam flags).
Random delays between emails to look human.
"""

import random
import smtplib
import time
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from config import (
    SMTP_HOST, SMTP_PORT, SMTP_EMAIL, SMTP_PASSWORD,
    SENDER_NAME, DAILY_SEND_LIMIT, SEND_DELAY_MIN, SEND_DELAY_MAX,
)
from database import (
    get_unsent_emails, get_followup_emails,
    mark_sent, mark_failed, get_today_send_count, was_sent_today,
)
from templates import get_template, get_followup_template

log = logging.getLogger("outreach.sender")


def create_email(to_email, subject, body):
    """Create a natural-looking plain text email."""
    msg = MIMEMultipart('alternative')
    msg['From'] = f'{SENDER_NAME} <{SMTP_EMAIL}>'
    msg['To'] = to_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain', 'utf-8'))
    return msg


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


def send_batch(smtp, emails, template_fn, email_type, sent_count, total_limit):
    """Send a batch of emails. Returns number sent."""
    batch_sent = 0

    for email_row in emails:
        if sent_count + batch_sent >= total_limit:
            break

        # Skip if already sent today (prevent double-send)
        if was_sent_today(email_row['id']):
            log.info(f"  Skipping {email_row['email']} (already sent today)")
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
