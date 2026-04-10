"""
3-step email sequence runner.
Handles: initial email, follow-up 1 (day 2-3), follow-up 2 (day 5).
"""

import time
import logging

import config
from db import get_leads_needing_step, update_lead
from sender.smtp_sender import EmailSender

log = logging.getLogger(__name__)


STEPS = [
    {"key": "email_1", "config_key": "email_1", "delay_days": 0},
    {"key": "follow_up_1", "config_key": "follow_up_1", "delay_days": 2},
    {"key": "follow_up_2", "config_key": "follow_up_2", "delay_days": 5},
]


def run_sequence(sender_name="", dry_run=False):
    """Run all pending sequence steps."""
    sender = None if dry_run else EmailSender()
    total_sent = 0

    for step in STEPS:
        leads = get_leads_needing_step(step["key"], step["delay_days"])

        if not leads:
            log.info(f"  [dim]{step['key']}: no leads ready[/dim]")
            continue

        log.info(f"  [bold]{step['key']}[/bold]: {len(leads)} leads ready")

        seq = config.EMAIL_SEQUENCES[step["config_key"]]

        for lead in leads:
            # Build email
            store_name = lead.get("store_name") or lead["domain"]
            first_line = lead.get("first_line") or f"Saw your {store_name} store."

            subject = seq["subject"].format(
                store_name=store_name,
                domain=lead["domain"],
            )
            body = seq["body"].format(
                first_line=first_line,
                store_name=store_name,
                domain=lead["domain"],
                sender_name=sender_name or "Alex",
            )

            if dry_run:
                log.info(f"    [yellow]DRY RUN[/yellow] → {lead['email']} | {subject}")
                total_sent += 1
                continue

            # Check capacity
            if sender.get_remaining_capacity() <= 0:
                log.info("  [red]Daily limit reached across all inboxes[/red]")
                return total_sent

            success, msg = sender.send_email(
                to_email=lead["email"],
                subject=subject,
                body=body,
                lead_id=lead["id"],
                step=step["key"],
                sender_name=sender_name,
            )

            if success:
                log.info(f"    [green]OK[/green] → {lead['email']} | {msg}")
                update_lead(lead["id"], status="contacted")
                total_sent += 1
            else:
                log.info(f"    [red]FAIL[/red] → {lead['email']} | {msg}")

            time.sleep(config.DELAY_BETWEEN_EMAILS)

    return total_sent
