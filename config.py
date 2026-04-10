import os
from dotenv import load_dotenv

load_dotenv()


# SMTP
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")

# Multiple inboxes: "email:pass,email2:pass2"
def get_sender_inboxes():
    raw = os.getenv("SENDER_INBOXES", "")
    if not raw:
        if SMTP_USER and SMTP_PASS:
            return [{"email": SMTP_USER, "password": SMTP_PASS}]
        return []
    inboxes = []
    for pair in raw.split(","):
        pair = pair.strip()
        if ":" in pair:
            email, password = pair.split(":", 1)
            inboxes.append({"email": email.strip(), "password": password.strip()})
    return inboxes


# Search
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")

# AI
AI_PROVIDER = os.getenv("AI_PROVIDER", "anthropic")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# Sending limits
DAILY_LIMIT_PER_INBOX = int(os.getenv("DAILY_LIMIT_PER_INBOX", "20"))
DELAY_BETWEEN_EMAILS = int(os.getenv("DELAY_BETWEEN_EMAILS", "60"))
WARMUP_DAYS = int(os.getenv("WARMUP_DAYS", "7"))

# Search queries for Shopify stores
SEARCH_QUERIES = [
    'site:myshopify.com {niche}',
    'best Shopify stores in {niche}',
    'top Shopify {niche} stores',
    '"powered by Shopify" {niche}',
    '{niche} store shopify',
]

# Email sequences
EMAIL_SEQUENCES = {
    "subject_options": [
        "Quick fix for your store",
        "Reduce support load?",
        "Saw your {store_name} store",
    ],
    "email_1": {
        "delay_days": 0,
        "subject": "Quick question about {store_name}",
        "body": """{first_line}

Most stores lose revenue from slow replies and abandoned carts.

I built a simple AI system that:
- auto-replies to customers
- follows up abandoned carts

Worth showing a quick demo?

Best,
{sender_name}"""
    },
    "follow_up_1": {
        "delay_days": 2,
        "subject": "Re: Quick question about {store_name}",
        "body": """Quick follow-up.

This usually reduces 50-70% support workload and improves response speed.

Want me to send a demo?

{sender_name}"""
    },
    "follow_up_2": {
        "delay_days": 5,
        "subject": "Re: Quick question about {store_name}",
        "body": """Should I close this, or are you open to seeing how this works?

{sender_name}"""
    }
}
