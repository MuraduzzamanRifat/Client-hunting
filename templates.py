"""Email templates — short, curiosity-driven, no hard sell.

Rule: 50-100 words max. Personal hook > problem > offer > soft CTA.
First email = build curiosity. Follow-ups = add value.
"""

import random
from config import SENDER_NAME, EXTENSION_URL, PURCHASE_EXTENSION_URL, SUBJECT_LINES, FOLLOWUP_SUBJECT_LINES


def get_template(name=None):
    """Returns (subject, body) for initial outreach. ~60-80 words."""
    first_name = name.strip().split()[0] if name and name.strip() else ""
    greeting = f"Hey {first_name}," if first_name else "Hey,"
    subject = random.choice(SUBJECT_LINES)

    templates = [
        f"""{greeting}

Noticed you're freelancing on Upwork. Quick question — are you happy with your proposal response rate?

Most freelancers I talk to waste 80% of their connects on jobs that were never going to reply.

I built something that fixes that. Curious?

{EXTENSION_URL}

— {SENDER_NAME}""",

        f"""{greeting}

What if you could tell which Upwork jobs will actually respond before you apply?

That's exactly what I built. It scores jobs, fixes your profile, and writes proposals using AI.

Might be useful for you: {EXTENSION_URL}

— {SENDER_NAME}""",

        f"""{greeting}

The freelancers landing the best Upwork clients aren't writing better proposals — they're picking better jobs to apply to.

I built a Chrome extension that does exactly that. It also reviews your profile and tells you what to fix.

Worth a look? {EXTENSION_URL}

— {SENDER_NAME}""",

        f"""{greeting}

Upwork is a numbers game — but most freelancers play it wrong.

Instead of sending 50 proposals and hoping, what if you only applied to jobs where you'd actually get a response?

I built a tool for that: {EXTENSION_URL}

Takes 2 minutes to try.

— {SENDER_NAME}""",

        f"""{greeting}

One thing that separates top Upwork freelancers from everyone else — they know which jobs to skip.

I built ProWorkSpace to give every freelancer that same advantage. AI job scoring + proposal writing + profile audit.

Here if you want to check it out: {EXTENSION_URL}

— {SENDER_NAME}""",
    ]

    return subject, random.choice(templates)


def get_followup_template(name=None, followup_num=1):
    """Returns (subject, body) for follow-ups. Even shorter."""
    first_name = name.strip().split()[0] if name and name.strip() else ""
    greeting = f"Hey {first_name}," if first_name else "Hey,"
    subject = random.choice(FOLLOWUP_SUBJECT_LINES)

    if followup_num == 1:
        templates = [
            f"""{greeting}

Sent you a note about ProWorkSpace a few days ago. No worries if it's not for you.

Just wanted to mention — freelancers using it are seeing 3-5x better response rates on Upwork.

{EXTENSION_URL}

— {SENDER_NAME}""",

            f"""{greeting}

Following up quickly. I know inboxes get busy.

If you're still spending hours writing Upwork proposals manually, ProWorkSpace might save you real time.

{EXTENSION_URL}

— {SENDER_NAME}""",
        ]
    else:
        templates = [
            f"""{greeting}

Last note from me on this. ProWorkSpace is helping freelancers win more Upwork jobs with less effort.

If you're ever curious: {EXTENSION_URL}

Good luck with everything!

— {SENDER_NAME}""",
        ]

    return subject, random.choice(templates)
