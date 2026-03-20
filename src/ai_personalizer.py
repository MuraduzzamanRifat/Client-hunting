"""
Email Personalization Engine — Two-Step Smart Flow.

Email 1 — First Contact (sent immediately after lead found):
  - Plain text ONLY. No links. No portfolio. No selling.
  - Identifies ONE specific problem:
      No website  → "I noticed your business doesn't have a website"
      Has website → "I noticed your site isn't showing up on Google"
  - Soft CTA: asks if they want help, no pressure.
  - Goal: start a conversation, not close a deal.

Email 2 — Conversion (sent ONLY when lead replies with positive intent):
  - Triggered by reply_classifier detecting: interested / question
  - Now includes portfolio link (mjrifat.com)
  - More direct CTA — collaboration/hiring invitation
  - Still personalized to their specific problem.
"""

import random


# ── Angle Detection ──────────────────────────────────────────────────

def get_email_angle(lead: dict) -> str:
    """
    Determine the service angle.
    - No website  → "website"   (WordPress creation)
    - Has website → "seo"       (SEO + content automation)
    """
    return "website" if not lead.get("Website") else "seo"


# ── Email 1: Problem-Based First Contact ─────────────────────────────

def _first_email_subject(name: str, angle: str) -> str:
    subjects = {
        "seo": [
            f"Something I noticed about {name}",
            f"Quick question — {name}",
            f"{name} — spotted something on Google",
            f"Are you getting found on Google, {name}?",
            f"{name} — a thought about your online visibility",
        ],
        "website": [
            f"Quick question — {name}",
            f"{name} — do you have a website?",
            f"Thought about {name}",
            f"Hey {name} — your customers are searching for you",
            f"{name} — noticed something",
        ],
    }
    return random.choice(subjects.get(angle, subjects["seo"]))


def _first_email_body(name: str, angle: str) -> str:
    if angle == "website":
        options = [
            (
                f"Hey {name},\n\n"
                "I was looking at local businesses in your area and came across your listing. "
                "I noticed you don't have a website yet.\n\n"
                "These days most customers Google a business before they visit or call. "
                "Without a website, they end up finding your competitors instead.\n\n"
                "Is that something you've been thinking about?\n\n"
                "Mj"
            ),
            (
                f"Hey {name},\n\n"
                "Found your business while researching your area. "
                "You've got a listing but no website attached to it.\n\n"
                "That means anyone searching for what you offer online won't find you — "
                "they'll find someone else.\n\n"
                "Would you be open to exploring that?\n\n"
                "Mj"
            ),
            (
                f"Hi {name},\n\n"
                "I came across your business and noticed there's no website. "
                "That's common, but it means you're invisible to anyone "
                "who searches online before deciding where to go.\n\n"
                "Just wanted to flag it — is that something you'd want to fix?\n\n"
                "Mj"
            ),
            (
                f"Hey {name},\n\n"
                "I noticed your business doesn't have a website yet. "
                "Most people check online before visiting anywhere new — "
                "so right now, those customers can't find you.\n\n"
                "Have you thought about getting one?\n\n"
                "Mj"
            ),
        ]
    else:  # seo
        options = [
            (
                f"Hey {name},\n\n"
                "I was researching businesses in your area and came across your site. "
                "I noticed it's not showing up on Google for the searches your customers are likely using.\n\n"
                "That means people looking for what you offer right now are finding your competitors first.\n\n"
                "Is that something you'd want to look into?\n\n"
                "Mj"
            ),
            (
                f"Hey {name},\n\n"
                "I found your business while looking at your local market. "
                "Your website exists, but it's not ranking on Google where your customers are searching.\n\n"
                "There's a real gap there — people who should be finding you aren't.\n\n"
                "Would it be helpful to talk about what's causing that?\n\n"
                "Mj"
            ),
            (
                f"Hi {name},\n\n"
                "Quick one — I was looking at businesses like yours in your area and noticed "
                "your site isn't appearing in the top results when people search for what you offer.\n\n"
                "Your competitors are showing up above you. That's fixable.\n\n"
                "Interested in hearing how?\n\n"
                "Mj"
            ),
            (
                f"Hey {name},\n\n"
                "I noticed your website isn't optimized for search — "
                "so when someone searches for your type of business nearby, "
                "you're not the first result they see.\n\n"
                "That's a lot of customers going elsewhere without knowing you exist.\n\n"
                "Is that something you'd want to change?\n\n"
                "Mj"
            ),
        ]
    return random.choice(options)


def generate_personalized_email(lead_data: dict) -> dict:
    """
    Generate Email 1 — the first outreach email.

    Rules:
    - Plain text only
    - No links, no portfolio, no selling
    - Identifies one specific problem (no website OR poor SEO)
    - Soft CTA: asks if they want help

    Returns:
        {"subject": str, "body": str, "service": str, "angle": str}
    """
    name = lead_data.get("Name", "there")
    angle = get_email_angle(lead_data)
    service = "WordPress Website" if angle == "website" else "SEO"

    subject = _first_email_subject(name, angle)
    body = _first_email_body(name, angle)

    return {"subject": subject, "body": body, "service": service, "angle": angle}


# ── Email 2: Conversion Email ─────────────────────────────────────────
# Sent ONLY when lead replies with positive intent (interested / question).
# Includes portfolio link. More direct. Still personalized.

def generate_conversion_email(lead_data: dict) -> dict:
    """
    Generate Email 2 — the conversion email.

    Triggered by: reply_classifier detecting interested or question intent.

    Includes:
    - Portfolio/CV link: https://mjrifat.com/
    - Direct CTA for collaboration or hiring
    - Personalized to the specific problem (website vs SEO)

    Returns:
        {"subject": str, "body": str, "service": str, "angle": str}
    """
    name = lead_data.get("Name", "there")
    angle = get_email_angle(lead_data)

    subjects = {
        "website": [
            f"Re: website for {name} — here's what I had in mind",
            f"More details — {name}",
            f"{name} — a few examples I wanted to share",
        ],
        "seo": [
            f"Re: {name} — here's what I'd do",
            f"More details — {name}",
            f"{name} — quick breakdown for you",
        ],
    }
    subject = random.choice(subjects.get(angle, subjects["seo"]))

    if angle == "website":
        service = "WordPress Website"
        service_lines = [
            (
                "I build clean, professional WordPress websites for local businesses — "
                "mobile-friendly, optimized to show up on Google from day one, and easy to manage. "
                "Most businesses I work with start getting calls and inquiries within the first few weeks."
            ),
            (
                "I specialize in WordPress websites for small and local businesses. "
                "Fast, mobile-ready, and set up to rank on Google. "
                "Nothing complicated — just something professional that works."
            ),
            (
                "I build WordPress sites for businesses exactly like yours — "
                "clean design, mobile-ready, and set up so customers can actually find you online."
            ),
        ]
    else:
        service = "SEO + Content Automation"
        service_lines = [
            (
                "I do local and global SEO — getting your site ranking where your customers are searching. "
                "I also set up automated blog content that keeps your site active and helps Google rank you higher, "
                "all running on autopilot once it's live."
            ),
            (
                "My approach: fix the technical SEO issues first, then build consistent content that Google rewards. "
                "I set up content automation so your site keeps growing even when you're busy running the business."
            ),
            (
                "I help businesses rank higher on Google through SEO and automated content — "
                "so more customers find you without you having to do anything extra."
            ),
        ]

    service_line = random.choice(service_lines)

    ctas = [
        "Would you like to set up a quick call to talk through the details?",
        "If you'd like to move forward or have questions, just reply and we can go from there.",
        "Happy to answer anything — just reply and I'll get back to you.",
        "Whenever you're ready, just say the word.",
    ]
    cta = random.choice(ctas)
    signoff = random.choice(["Best", "Cheers", "Talk soon"])

    body = (
        f"Hey {name},\n\n"
        f"Thanks for getting back to me.\n\n"
        f"{service_line}\n\n"
        f"You can view my portfolio and CV here: https://mjrifat.com/ "
        f"— available for collaboration or hiring.\n\n"
        f"{cta}\n\n"
        f"{signoff},\nMj"
    )

    return {"subject": subject, "body": body, "service": service, "angle": f"{angle}_conversion"}


# ── Standalone test ──────────────────────────────────────────────────
if __name__ == "__main__":
    samples = [
        {"Name": "Sunrise Cafe", "Website": "https://sunrisecafe.com", "Rating": "4.7", "Reviews": "230"},
        {"Name": "Joe's Diner", "Website": "https://joesdiner.com", "Rating": "3.1", "Reviews": "12"},
        {"Name": "Beach Bites", "Website": "", "Rating": "4.2", "Reviews": "5"},
    ]

    for i, lead in enumerate(samples, 1):
        print(f"\n{'='*60}")
        print(f"  Sample {i}: {lead['Name']}")

        first = generate_personalized_email(lead)
        print(f"\n  --- EMAIL 1 (First Contact) ---")
        print(f"  Angle: {first['angle']} | Service: {first['service']}")
        print(f"  Subject: {first['subject']}")
        print(f"\n{first['body']}")

        conversion = generate_conversion_email(lead)
        print(f"\n  --- EMAIL 2 (Conversion) ---")
        print(f"  Angle: {conversion['angle']}")
        print(f"  Subject: {conversion['subject']}")
        print(f"\n{conversion['body']}")
