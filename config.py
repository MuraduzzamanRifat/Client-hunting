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
