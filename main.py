"""Email Outreach CLI — manual control interface."""

import sys
import os
import logging

sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(message)s')

from database import get_stats, get_unsent_emails, get_followup_emails, init_db
from collectors.facebook_collector import run_facebook_collector
from collectors.website_collector import run_website_collector
from sender import start_sender


def show_stats():
    stats = get_stats()
    print("\n--- Email Outreach Stats ---")
    print(f"  Total collected:   {stats['total']}")
    print(f"  Ready to send:     {stats['new']}")
    print(f"  Already sent:      {stats['sent']}")
    print(f"  Skipped/bounced:   {stats['skipped']}")
    print(f"  Sent today:        {stats['today_sent']}")
    print(f"  Due for follow-up: {stats['due_followup']}")
    print("----------------------------\n")


def preview_unsent():
    emails = get_unsent_emails(limit=20)
    followups = get_followup_emails(limit=10)

    if emails:
        print(f"\nNext {len(emails)} new emails:")
        for e in emails:
            print(f"  {e['email']:40s} | {e['source']:10s} | {e['name'] or 'N/A'}")

    if followups:
        print(f"\n{len(followups)} due for follow-up:")
        for e in followups:
            print(f"  {e['email']:40s} | follow-up #{e['followup_count']+1} | last: {e['last_sent_at'][:10]}")

    if not emails and not followups:
        print("No emails to send.")


def main():
    init_db()
    print("Email Outreach Tool — ProWorkSpace Promo\n")
    show_stats()

    while True:
        print("\n=== Menu ===")
        print("  1. Collect from Facebook")
        print("  2. Collect from Websites (freelancers + agencies)")
        print("  3. Collect from Both")
        print("  4. Send emails (initial + follow-ups)")
        print("  5. View stats")
        print("  6. Preview queue")
        print("  0. Exit")

        choice = input("\nChoice: ").strip()

        if choice == '1':
            run_facebook_collector()
        elif choice == '2':
            run_website_collector()
        elif choice == '3':
            fb = run_facebook_collector()
            web = run_website_collector()
            print(f"\nTotal: {fb + web} (FB: {fb}, Web: {web})")
        elif choice == '4':
            show_stats()
            confirm = input("Start sending? (y/n): ").strip().lower()
            if confirm == 'y':
                start_sender()
        elif choice == '5':
            show_stats()
        elif choice == '6':
            preview_unsent()
        elif choice == '0':
            break


if __name__ == "__main__":
    main()
