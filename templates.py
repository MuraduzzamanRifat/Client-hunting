"""Email templates for ProWorkSpace outreach.

Initial emails + follow-up emails. Real pricing ($19/$39).
Random selection per email to avoid pattern detection.
"""

import random
from config import SENDER_NAME, EXTENSION_URL, PURCHASE_EXTENSION_URL, SUBJECT_LINES, FOLLOWUP_SUBJECT_LINES


def get_template(name=None):
    """Returns (subject, body) for initial outreach."""
    first_name = name.split()[0] if name else ""
    greeting = f"Hey {first_name}," if first_name else "Hey,"
    subject = random.choice(SUBJECT_LINES)

    templates = [
        f"""{greeting}

I know the Upwork grind — writing proposals all day and hearing nothing back.

I built ProWorkSpace to fix that. It's a Chrome extension that:

- Analyzes your profile and tells you exactly what to fix
- Uses AI to write proposals that actually get replies
- Shows you which jobs are worth your connects

It's just $19 one-time (not a subscription).

Check it out: {EXTENSION_URL}

— {SENDER_NAME}""",

        f"""{greeting}

Quick question — how many Upwork proposals did you send last week with zero response?

Most freelancers waste 80% of their connects on jobs that were never going to reply.

I built ProWorkSpace — a Chrome extension that:

- Scores your profile and shows what clients actually see
- Writes AI proposals matched to each job
- Tells you which jobs are worth applying to

$19 one-time. No subscription. No tricks.

Try it: {EXTENSION_URL}

— {SENDER_NAME}""",

        f"""{greeting}

The biggest mistake on Upwork? Applying to everything and hoping something sticks.

ProWorkSpace fixes that. It's a Chrome extension that helps you:

- Fix your profile so clients find YOU
- Write better proposals in seconds with AI
- Stop wasting connects on dead-end jobs

Starts at $19 — one payment, use it forever.

{EXTENSION_URL}

— {SENDER_NAME}""",

        f"""{greeting}

If you're freelancing on Upwork, this might save you hours every week.

ProWorkSpace is a Chrome extension that:

- Reviews your profile and gives specific improvement tips
- Generates AI-powered proposals tailored to each job
- Helps you pick jobs where you'll actually get a response

I'm a freelancer too — I built this because I was tired of the same problems.

$19 one-time: {PURCHASE_EXTENSION_URL}

— {SENDER_NAME}""",

        f"""{greeting}

Upwork is competitive. Most proposals get ignored.

ProWorkSpace changes that — it's a Chrome extension that:

- Tells you what's wrong with your profile (and how to fix it)
- Writes proposals using AI that match what clients want
- Filters jobs so you only apply where you have a real shot

$19 for Pro, $39 for Agency (unlimited). One-time payment.

{EXTENSION_URL}

— {SENDER_NAME}""",
    ]

    return subject, random.choice(templates)


def get_followup_template(name=None, followup_num=1):
    """Returns (subject, body) for follow-up emails."""
    first_name = name.split()[0] if name else ""
    greeting = f"Hey {first_name}," if first_name else "Hey,"
    subject = random.choice(FOLLOWUP_SUBJECT_LINES)

    if followup_num == 1:
        templates = [
            f"""{greeting}

I reached out a few days ago about ProWorkSpace — a Chrome extension that helps Upwork freelancers write better proposals and fix their profiles using AI.

Just wanted to make sure you saw it. Freelancers who use it are getting 3-5x more responses on their proposals.

It's $19 one-time (no subscription): {PURCHASE_EXTENSION_URL}

No pressure — just thought it might help.

— {SENDER_NAME}""",

            f"""{greeting}

Following up on my last email. I know inboxes get busy.

ProWorkSpace helps you:
- Stop wasting Upwork connects on the wrong jobs
- Write proposals that actually get responses
- Fix your profile so clients come to you

$19 once. That's less than 4 connects on Upwork.

{EXTENSION_URL}

— {SENDER_NAME}""",
        ]
    else:
        templates = [
            f"""{greeting}

Last time reaching out about this — ProWorkSpace is helping freelancers on Upwork land more clients with less effort.

AI-powered proposals + profile optimization for just $19.

If you're interested: {PURCHASE_EXTENSION_URL}

Either way, good luck with your freelancing!

— {SENDER_NAME}""",
        ]

    return subject, random.choice(templates)
