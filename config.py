"""
Centralized configuration for the lead generation pipeline.
Loads secrets from .env and defines all tuneable settings.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── API Keys & Credentials ──────────────────────────────────────────
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")
GOOGLE_CREDS_FILE = os.getenv("GOOGLE_CREDS_FILE", "credentials.json")

# ── Email / SMTP Credentials ────────────────────────────────────────
# Works with any provider: Gmail, Outlook, Yahoo, Zoho, custom webmail, etc.
EMAIL_USER = os.getenv("EMAIL_USER", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "")  # optional: "from" address if different from login

# ── Scraper Settings ────────────────────────────────────────────────
SERPER_MAPS_URL = "https://google.serper.dev/maps"
RESULTS_PER_PAGE = 20          # Serper.dev returns up to 20 results per page
SCRAPE_DELAY = 1.5             # seconds between API calls
REQUEST_TIMEOUT = 10           # seconds before request times out
MAX_RETRIES = 3                # retry attempts on API failure
RETRY_BACKOFF = 2              # exponential backoff multiplier

# ── Email Finder Settings ───────────────────────────────────────────
CRAWL_DELAY_MIN = 1.0          # min seconds between website visits
CRAWL_DELAY_MAX = 2.0          # max seconds between website visits
CONTACT_PAGE_KEYWORDS = ["contact", "contact-us", "about", "about-us", "get-in-touch"]
JUNK_EMAIL_PREFIXES = [
    "noreply", "no-reply", "support", "info", "admin", "webmaster",
    "sales", "help", "contact", "feedback", "abuse", "postmaster",
]
JUNK_EMAIL_EXTENSIONS = [".png", ".jpg", ".jpeg", ".gif", ".svg", ".css", ".js", ".webp"]
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# ── Google Sheets Settings ──────────────────────────────────────────
SHEET_NAME = "Lead CRM"

# ── Lead Scoring Weights ────────────────────────────────────────────
# Has email       → High priority (send email)
# No email + phone → Medium priority (call queue)
# No email + no phone → Skip (not reachable, avoid wasting resources)
SCORE_EMAIL = 40               # has email = top priority
SCORE_PHONE = 20               # has phone = reachable by call
SCORE_NO_WEBSITE = 25          # NO website = hot lead for WordPress
SCORE_HAS_WEBSITE = 10         # has website = SEO target
SCORE_HIGH_RATING = 15         # rating >= 4.0
SCORE_REVIEWS_LOW = 5          # 0-10 reviews
SCORE_REVIEWS_MED = 10         # 11-50 reviews
SCORE_REVIEWS_HIGH = 15        # 50+ reviews

# ── Priority Thresholds ─────────────────────────────────────────────
HIGH_THRESHOLD = 65
MEDIUM_THRESHOLD = 40

# ── Outreach Thresholds ─────────────────────────────────────────────
MIN_OUTREACH_SCORE = 50        # minimum score to send email
MIN_CALL_SCORE = 40            # minimum score for call queue

# ── Email Sending Settings ──────────────────────────────────────────
MAX_EMAILS_PER_DAY = 50
EMAIL_DELAY_MIN = 30           # min seconds between sends
EMAIL_DELAY_MAX = 90           # max seconds between sends
# SMTP server — set in .env for your provider
# Common examples:
#   Gmail:    smtp.gmail.com     / 587
#   Outlook:  smtp.office365.com / 587
#   Yahoo:    smtp.mail.yahoo.com/ 587
#   Zoho:     smtp.zoho.com      / 587
#   Custom:   mail.yourdomain.com/ 587 or 465
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USE_SSL = os.getenv("SMTP_USE_SSL", "false").lower() == "true"  # True for port 465
