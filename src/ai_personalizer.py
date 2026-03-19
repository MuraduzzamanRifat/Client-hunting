"""
Step 7 — AI Email Personalization Engine.

Generates unique, human-sounding emails for each lead based on
their business data. Uses smart templates with randomized variation
so every email feels different.

Service angles:
  - No website         → WordPress website creation
  - Has website (low)  → Local/Global SEO + n8n content automation
  - Has website (high) → SEO scaling + n8n content automation
"""

import random


# ── Angle Detection ──────────────────────────────────────────────────

def get_email_angle(lead: dict) -> str:
    """
    Determine the service angle based on lead data.

    - No website          → "website"   (WordPress site creation)
    - Has website, <4.0   → "seo_fix"   (SEO improvement + content automation)
    - Has website, >=4.0  → "seo_grow"  (SEO scaling + content automation)
    """
    has_website = bool(lead.get("Website"))
    try:
        rating = float(lead.get("Rating", 0) or 0)
    except (ValueError, TypeError):
        rating = 0

    if not has_website:
        return "website"
    elif rating > 0 and rating < 4.0:
        return "seo_fix"
    else:
        return "seo_grow"


# ── Building Blocks ──────────────────────────────────────────────────

def _random_intro(name: str, angle: str) -> str:
    """Generate a personalized opening line."""
    intros = {
        "seo_grow": [
            f"Hey {name} — I came across your business online and really liked what I saw.",
            f"Hi {name}, your reviews are impressive — clearly you've built something customers love.",
            f"Hey {name} — I was researching local businesses and yours stood out right away.",
            f"Hi {name}, I noticed your business while looking around your area — great reputation.",
            f"Hey {name} — your online presence caught my attention, and I had a quick idea for you.",
        ],
        "seo_fix": [
            f"Hey {name} — I came across your business and noticed something that could help.",
            f"Hi {name}, I was looking at businesses in your area and had a thought about yours.",
            f"Hey {name} — I found your business while researching your niche and wanted to reach out.",
            f"Hi {name}, I noticed your listing and I think there's a real opportunity you're missing.",
            f"Hey {name} — quick one. I was looking at your business online and spotted something.",
        ],
        "website": [
            f"Hey {name} — I noticed your business doesn't have a website yet and wanted to reach out.",
            f"Hi {name}, I came across your listing and saw you're not online yet — that's actually a big opportunity.",
            f"Hey {name} — I found your business on Google Maps but couldn't find a website for you.",
            f"Hi {name}, quick question — have you thought about getting a website for your business?",
            f"Hey {name} — I was looking at local businesses and noticed yours doesn't have a website yet.",
        ],
    }
    return random.choice(intros.get(angle, intros["seo_grow"]))


def _random_hook(angle: str, lead: dict) -> str:
    """Generate a value proposition matched to the service."""
    name = lead.get("Name", "your business")
    try:
        rating = float(lead.get("Rating", 0) or 0)
    except (ValueError, TypeError):
        rating = 0
    try:
        reviews = int(str(lead.get("Reviews", 0) or 0).replace(",", ""))
    except (ValueError, TypeError):
        reviews = 0

    hooks = {
        "seo_grow": [
            f"With a {rating}-star rating and {reviews}+ reviews, you already have trust built up. "
            "But most of your potential customers are finding competitors first on Google. "
            "I help businesses like yours rank higher locally and globally with SEO — "
            "plus I set up automated blog content so your site stays fresh without you lifting a finger.",

            "Your business has a solid reputation, but your website could be bringing in way more traffic. "
            "I specialize in local and global SEO to get you ranking where it matters. "
            "I also set up automated content publishing — fresh blog posts go live on your site "
            "on autopilot, which Google loves.",

            f"A {rating}-star business with {reviews} reviews should be dominating search results. "
            "I can help with that — I do local and global SEO, and I also set up content automation "
            "that publishes blog posts to your site automatically. More content = more traffic = more customers.",

            "You've got the reputation — now it's about making sure people find you first when they search. "
            "I offer SEO services (local + global) and set up automated blog publishing "
            "so your website keeps growing even when you're busy running your business.",
        ],
        "seo_fix": [
            "I think there's a lot of room to grow your online visibility. "
            "I help businesses improve their Google rankings through local and global SEO. "
            "I also set up automated blog content that publishes to your site regularly — "
            "it's one of the easiest ways to climb search results.",

            "Right now, a lot of potential customers are probably finding your competitors first. "
            "I specialize in SEO to fix that — getting you ranking higher locally and beyond. "
            "Plus I set up automated content publishing so your site stays active and Google keeps rewarding it.",

            "The good news is, your online presence has a lot of untapped potential. "
            "With the right SEO strategy and consistent content (which I automate), "
            "you could be pulling in a lot more customers from Google without extra effort on your end.",

            "There are some straightforward SEO improvements that could make a real difference for your visibility. "
            "I also set up content automation — blog posts that publish themselves on a schedule. "
            "It keeps your site fresh and helps Google rank you higher.",
        ],
        "website": [
            "These days, most people Google a business before they visit. Without a website, "
            "you're invisible to all of them. I build clean, professional WordPress websites "
            "for businesses like yours — nothing complicated, just something that looks great "
            "and helps customers find you.",

            "Right now, when someone searches for what you offer, your competitors with websites show up first. "
            "I create WordPress websites for local businesses — fast, mobile-friendly, "
            "and designed to actually bring in customers. It's more affordable than you'd think.",

            "A website is the #1 thing that turns a Google Maps listing into real customer traffic. "
            "I build WordPress sites for businesses exactly like yours — professional, easy to manage, "
            "and set up to rank on Google from day one.",

            "Without a website, you're leaving a lot of business on the table. "
            "I specialize in building WordPress websites for local businesses. "
            "Clean design, mobile-ready, and optimized so people can actually find you online.",

            "I help local businesses get online with professional WordPress websites. "
            "Your listing already shows you've got happy customers — a website would help "
            "you reach the ones who are searching online and can't find you yet.",
        ],
    }
    return random.choice(hooks.get(angle, hooks["seo_grow"]))


def _random_cta(angle: str) -> str:
    """Generate a soft call-to-action matched to the service."""
    ctas = {
        "seo_grow": [
            "Want me to do a quick free audit of your current SEO? No strings attached.",
            "Would you be open to a quick chat about where your site could rank better?",
            "I'd be happy to send you a free report showing where you're losing traffic. Interested?",
            "If you're curious, I can show you exactly what keywords you should be ranking for. Just reply.",
            "Want me to take a look at your site and share a few quick wins? Totally free.",
        ],
        "seo_fix": [
            "I'd love to send you a quick breakdown of what could be improved. Interested?",
            "Want me to take a look and share some ideas? No cost, no pressure.",
            "Would it be helpful if I sent over a few specific suggestions for your site?",
            "If you're open to it, I can do a free quick review and show you what I'd change first.",
            "Happy to share some quick wins that could make a difference. Just reply and I'll send them over.",
        ],
        "website": [
            "Would you be open to seeing what a site could look like for your business? I can mock something up.",
            "If you're curious, I'd be happy to show you a few examples of sites I've built for similar businesses.",
            "Want me to put together a quick idea of what your website could look like? No obligation.",
            "I can send you some examples of what I've done for businesses like yours. Interested?",
            "Would it help if I showed you what a simple website could do for your business? Just reply.",
        ],
    }
    return random.choice(ctas.get(angle, ctas["seo_grow"]))


# ── Subject Lines ────────────────────────────────────────────────────

def _random_subject(name: str, angle: str) -> str:
    """Generate a randomized subject line."""
    subjects = {
        "seo_grow": [
            f"Quick SEO idea for {name}",
            f"{name} — you should be ranking higher",
            f"Hey {name} — thought about your Google ranking",
            f"Idea to get {name} more traffic",
            f"{name} — are you getting enough from Google?",
        ],
        "seo_fix": [
            f"Quick thought about {name}'s online visibility",
            f"Hey {name} — spotted an opportunity",
            f"{name} — idea to get more customers online",
            f"Something I noticed about {name}",
            f"Hey {name} — quick SEO suggestion",
        ],
        "website": [
            f"{name} — do you have a website yet?",
            f"Quick idea for {name}",
            f"Hey {name} — your customers are searching for you",
            f"{name} — getting online could change things",
            f"Thought about {name}'s online presence",
        ],
    }
    return random.choice(subjects.get(angle, subjects["seo_grow"]))


# ── Main Generator ───────────────────────────────────────────────────

def generate_personalized_email(lead_data: dict) -> dict:
    """
    Generate a unique personalized email for a lead.

    Returns:
        {"subject": str, "body": str, "service": str, "angle": str}
    """
    name = lead_data.get("Name", "there")
    angle = get_email_angle(lead_data)

    # Determine service label
    if angle == "website":
        service = "WordPress Website"
    else:
        service = "SEO + Content Automation"

    intro = _random_intro(name, angle)
    hook = _random_hook(angle, lead_data)
    cta = _random_cta(angle)

    # Random sign-offs (avoid repetitive patterns)
    signoff = random.choice(["Best", "Cheers", "Thanks", "Talk soon", "All the best"])
    body = f"{intro}\n\n{hook}\n\n{cta}\n\n{signoff},\nMj\nmjrifat.com"
    subject = _random_subject(name, angle)

    return {"subject": subject, "body": body, "service": service, "angle": angle}


# ── Standalone test ──────────────────────────────────────────────────
if __name__ == "__main__":
    samples = [
        {"Name": "Sunrise Cafe", "Website": "https://sunrisecafe.com", "Rating": "4.7",
         "Reviews": "230", "Email": "hello@sunrisecafe.com"},
        {"Name": "Joe's Diner", "Website": "https://joesdiner.com", "Rating": "3.1",
         "Reviews": "12", "Email": "joe@diner.com"},
        {"Name": "Beach Bites", "Website": "", "Rating": "4.2",
         "Reviews": "5", "Phone": "555-9999", "Email": "beachbites@gmail.com"},
    ]

    for i, lead in enumerate(samples, 1):
        result = generate_personalized_email(lead)
        print(f"\n{'='*60}")
        print(f"  Sample {i}: {lead['Name']}")
        print(f"  Angle: {result['angle']} | Service: {result['service']}")
        print(f"{'='*60}")
        print(f"  Subject: {result['subject']}")
        print(f"\n{result['body']}")
