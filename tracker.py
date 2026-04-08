"""Email tracking — reply/bounce detection via IMAP.

Checks the knock@brandivibe.com inbox for:
- Replies from people we emailed (marks as 'replied')
- Bounce-back notifications (marks as 'bounced')
"""

import imaplib
import email
import logging
from email.header import decode_header

from config import IMAP_HOST, IMAP_PORT, IMAP_EMAIL, IMAP_PASSWORD
from database import get_db

log = logging.getLogger("outreach.tracker")

BOUNCE_SUBJECTS = [
    'delivery status notification',
    'undeliverable',
    'mail delivery failed',
    'returned mail',
    'failure notice',
    'delivery failure',
    'could not be delivered',
    'permanent failure',
    'mailbox not found',
    'address rejected',
]

BOUNCE_SENDERS = [
    'mailer-daemon',
    'postmaster',
    'mail-daemon',
]


def decode_subject(msg):
    """Decode email subject safely."""
    raw = msg.get('Subject', '')
    if not raw:
        return ''
    decoded = decode_header(raw)
    parts = []
    for part, charset in decoded:
        if isinstance(part, bytes):
            parts.append(part.decode(charset or 'utf-8', errors='replace'))
        else:
            parts.append(part)
    return ' '.join(parts).lower()


def decode_from(msg):
    """Extract sender email address."""
    raw = msg.get('From', '')
    if '<' in raw:
        return raw.split('<')[1].split('>')[0].lower().strip()
    return raw.lower().strip()


def check_inbox():
    """Check inbox for replies and bounces. Returns stats dict."""
    if not IMAP_HOST or not IMAP_PASSWORD:
        log.warning("IMAP not configured — skipping inbox check")
        return {'replies': 0, 'bounces': 0}

    stats = {'replies': 0, 'bounces': 0}

    try:
        # Connect to IMAP
        imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        imap.login(IMAP_EMAIL, IMAP_PASSWORD)
        imap.select('INBOX')

        # Get all sent email addresses from DB
        conn = get_db()
        try:
            sent_rows = conn.execute(
                "SELECT id, email FROM emails WHERE status IN ('sent', 'replied', 'bounced')"
            ).fetchall()
        finally:
            conn.close()

        sent_emails = {row['email'].lower(): row['id'] for row in sent_rows}

        if not sent_emails:
            imap.logout()
            return stats

        # Search for recent unseen messages
        _, msg_nums = imap.search(None, 'UNSEEN')
        if not msg_nums[0]:
            imap.logout()
            return stats

        msg_ids = msg_nums[0].split()
        log.info(f"Checking {len(msg_ids)} unread emails...")

        for msg_id in msg_ids[-100:]:  # Check last 100 max
            try:
                _, data = imap.fetch(msg_id, '(RFC822)')
                msg = email.message_from_bytes(data[0][1])

                sender = decode_from(msg)
                subject = decode_subject(msg)

                # Check for bounce
                is_bounce = (
                    any(b in sender for b in BOUNCE_SENDERS) or
                    any(b in subject for b in BOUNCE_SUBJECTS)
                )

                if is_bounce:
                    # Try to find which email bounced from the body
                    body = get_body(msg)
                    for addr, eid in sent_emails.items():
                        if addr in body.lower():
                            mark_status(eid, 'bounced')
                            stats['bounces'] += 1
                            log.info(f"  BOUNCE: {addr}")
                            break

                # Check for reply from someone we emailed
                elif sender in sent_emails:
                    eid = sent_emails[sender]
                    mark_status(eid, 'replied')
                    stats['replies'] += 1
                    log.info(f"  REPLY: {sender}")

            except Exception as e:
                log.warning(f"Error processing message: {e}")
                continue

        imap.logout()

    except Exception as e:
        log.error(f"IMAP error: {e}")

    return stats


def get_body(msg):
    """Extract plain text body from email message."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == 'text/plain':
                try:
                    return part.get_payload(decode=True).decode('utf-8', errors='replace')
                except Exception:
                    return ''
    else:
        try:
            return msg.get_payload(decode=True).decode('utf-8', errors='replace')
        except Exception:
            return ''
    return ''


def mark_status(email_id, status):
    """Update email status in DB."""
    conn = get_db()
    try:
        conn.execute("UPDATE emails SET status = ? WHERE id = ?", (status, email_id))
        conn.commit()
    finally:
        conn.close()


def get_tracking_stats():
    """Get comprehensive tracking stats."""
    conn = get_db()
    try:
        stats = {}
        stats['total'] = conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
        stats['new'] = conn.execute("SELECT COUNT(*) FROM emails WHERE status='new'").fetchone()[0]
        stats['sent'] = conn.execute("SELECT COUNT(*) FROM emails WHERE status='sent'").fetchone()[0]
        stats['replied'] = conn.execute("SELECT COUNT(*) FROM emails WHERE status='replied'").fetchone()[0]
        stats['bounced'] = conn.execute("SELECT COUNT(*) FROM emails WHERE status='bounced'").fetchone()[0]
        stats['skipped'] = conn.execute("SELECT COUNT(*) FROM emails WHERE status='skipped'").fetchone()[0]

        # Calculate rates
        total_sent = stats['sent'] + stats['replied'] + stats['bounced']
        if total_sent > 0:
            stats['reply_rate'] = round(stats['replied'] / total_sent * 100, 1)
            stats['bounce_rate'] = round(stats['bounced'] / total_sent * 100, 1)
        else:
            stats['reply_rate'] = 0
            stats['bounce_rate'] = 0

        # Click rate comes from Google Analytics (UTM params)
        stats['click_note'] = "Check Google Analytics for click data (UTM: freelancer_bd)"

        return stats
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(message)s')
    result = check_inbox()
    print(f"Replies: {result['replies']}, Bounces: {result['bounces']}")
    print()
    stats = get_tracking_stats()
    for k, v in stats.items():
        print(f"  {k}: {v}")
