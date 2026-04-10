"""
SMTP email sender with inbox rotation and rate limiting.
"""

import smtplib
import time
import uuid
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

import config
from db import get_daily_send_count, record_send


class EmailSender:
    def __init__(self):
        self.inboxes = config.get_sender_inboxes()
        if not self.inboxes:
            raise ValueError("No sender inboxes configured. Set SENDER_INBOXES in .env")
        self._current_index = 0

    def _get_next_inbox(self):
        """Rotate through inboxes, skipping any that hit daily limit."""
        attempts = 0
        while attempts < len(self.inboxes):
            inbox = self.inboxes[self._current_index]
            self._current_index = (self._current_index + 1) % len(self.inboxes)

            sent_today = get_daily_send_count(inbox["email"])
            limit = self._get_daily_limit()

            if sent_today < limit:
                return inbox

            attempts += 1

        return None  # All inboxes exhausted

    def _get_daily_limit(self):
        """Calculate daily limit based on warmup period."""
        # During warmup, send fewer
        return config.DAILY_LIMIT_PER_INBOX

    def send_email(self, to_email, subject, body, lead_id, step, sender_name=""):
        """Send a single email. Returns True on success."""
        inbox = self._get_next_inbox()
        if not inbox:
            return False, "All inboxes hit daily limit"

        from_email = inbox["email"]
        if not sender_name:
            sender_name = from_email.split("@")[0].replace(".", " ").title()

        msg = MIMEMultipart("alternative")
        msg["From"] = f"{sender_name} <{from_email}>"
        msg["To"] = to_email
        msg["Subject"] = subject
        msg["Message-ID"] = f"<{uuid.uuid4()}@{from_email.split('@')[1]}>"

        # Plain text
        msg.attach(MIMEText(body, "plain"))

        try:
            host = inbox.get("host", config.SMTP_HOST)
            port = inbox.get("port", config.SMTP_PORT)
            use_ssl = inbox.get("ssl", config.SMTP_USE_SSL)

            if use_ssl or port == 465:
                server = smtplib.SMTP_SSL(host, port, timeout=30)
            else:
                server = smtplib.SMTP(host, port, timeout=30)
                server.ehlo()
                server.starttls()
                server.ehlo()

            with server:
                server.login(from_email, inbox["password"])
                server.sendmail(from_email, to_email, msg.as_string())

            record_send(lead_id, step, from_email, msg["Message-ID"])
            return True, f"Sent via {from_email}"

        except smtplib.SMTPAuthenticationError:
            return False, f"Auth failed for {from_email}"
        except smtplib.SMTPRecipientsRefused:
            return False, f"Recipient refused: {to_email}"
        except Exception as e:
            return False, f"SMTP error: {str(e)}"

    def get_remaining_capacity(self):
        """How many more emails can be sent today across all inboxes."""
        total = 0
        limit = self._get_daily_limit()
        for inbox in self.inboxes:
            sent = get_daily_send_count(inbox["email"])
            remaining = max(0, limit - sent)
            total += remaining
        return total
