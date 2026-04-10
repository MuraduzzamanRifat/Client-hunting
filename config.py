import os
from dotenv import load_dotenv

load_dotenv()


# SMTP defaults (used when inbox doesn't specify its own)
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USE_SSL = os.getenv("SMTP_USE_SSL", "false").lower() == "true"

# Multiple inboxes with per-inbox SMTP support
# Format: email|password|host|port|ssl,email2|password2|host2|port2|ssl
# Short format also works: email|password (uses default SMTP_HOST/PORT)
def get_sender_inboxes():
    raw = os.getenv("SENDER_INBOXES", "")
    if not raw:
        return []
    inboxes = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split("|")
        if len(parts) >= 2:
            inbox = {
                "email": parts[0].strip(),
                "password": parts[1].strip(),
                "host": parts[2].strip() if len(parts) > 2 else SMTP_HOST,
                "port": int(parts[3].strip()) if len(parts) > 3 else SMTP_PORT,
                "ssl": parts[4].strip().lower() == "true" if len(parts) > 4 else SMTP_USE_SSL,
            }
            inboxes.append(inbox)
    return inboxes


# Search APIs
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")
OUTSCRAPER_API_KEY = os.getenv("OUTSCRAPER_API_KEY", "")

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
        "Quick fix for {store_name}",
        "Idea for {store_name}",
        "You're likely losing sales from this",
    ],
    "email_1": {
        "delay_days": 0,
        "subject": "Idea for {store_name}",
        "body": """{first_line}

Most stores lose conversions when customers don't get fast answers to pre-sale questions (shipping, sizing, returns).

I help Shopify brands automate these interactions so customers get answers instantly and buy faster.

Happy to show a quick example tailored to your store — no setup needed on your side.

{sender_name}"""
    },
    "follow_up_1": {
        "delay_days": 2,
        "subject": "Re: Idea for {store_name}",
        "body": """Quick follow-up.

I mapped out how {store_name} could automatically handle 60-80% of incoming support questions (order status, returns, FAQs).

This usually reduces workload and speeds up replies immediately.

Want me to send it over?

{sender_name}"""
    },
    "follow_up_2": {
        "delay_days": 5,
        "subject": "Re: Idea for {store_name}",
        "body": """Should I close this out, or are you open to seeing how this would work for {store_name}?

{sender_name}"""
    }
}
