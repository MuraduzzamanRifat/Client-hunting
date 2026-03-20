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

# Seconds to wait between collecting an email and sending it (human-like pacing)
SEND_DELAY_AFTER_COLLECT = 8

# ── Email Finder Settings ───────────────────────────────────────────
CRAWL_DELAY_MIN = 1.0          # min seconds between website visits
CRAWL_DELAY_MAX = 2.0          # max seconds between website visits
CONTACT_PAGE_KEYWORDS = ["contact", "contact-us", "about", "about-us", "get-in-touch"]
JUNK_EMAIL_PREFIXES = [
    "noreply", "no-reply", "support", "info", "admin", "webmaster",
    "sales", "help", "contact", "feedback", "abuse", "postmaster",
    "mailer-daemon", "daemon", "root", "nobody", "hostmaster",
    "security", "newsletter", "unsubscribe", "billing", "accounts",
    "do-not-reply", "donotreply", "auto", "automated", "system",
    "notification", "notifications", "alert", "alerts", "bot",
]
JUNK_EMAIL_DOMAINS = [
    # Website builders / platforms (not real users)
    "wixpress.com", "wix.com", "squarespace.com", "shopify.com",
    "weebly.com", "godaddy.com", "wordpress.com", "netlify.com",
    "webflow.com", "carrd.co", "strikingly.com", "jimdo.com",
    # Tracking / analytics / dev
    "sentry.io", "sentry-next.wixpress.com", "cloudflare.com",
    "googlemail.com", "mailgun.org", "sendgrid.net", "mailchimp.com",
    "amazonaws.com", "herokuapp.com", "vercel.app",
    # Fake / placeholder
    "example.com", "test.com", "localhost", "domain.com",
    "email.com", "website.com", "yoursite.com", "yourdomain.com",
    "sample.com", "placeholder.com",
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
# Start low during domain warming, increase gradually
# Week 1: 5, Week 2: 15, Week 3: 30, Week 4+: 50
MAX_EMAILS_PER_DAY = 5         # WARMING PHASE — increase weekly
EMAIL_DELAY_MIN = 45           # min seconds between sends
EMAIL_DELAY_MAX = 120          # max seconds between sends
MAX_BOUNCE_RATE = 0.03         # stop sending if bounces exceed 3%
MAX_EMAILS_PER_HOUR = 15       # never exceed this per hour
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

# ── IMAP / Inbox Settings ──────────────────────────────────────────
IMAP_SERVER = os.getenv("IMAP_SERVER", "mail.mjrifat.com")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
IMAP_USER = os.getenv("IMAP_USER", os.getenv("EMAIL_USER", ""))
IMAP_PASSWORD = os.getenv("IMAP_PASSWORD", os.getenv("EMAIL_PASSWORD", ""))
INBOX_FETCH_LIMIT = 50  # max emails to fetch per request

# ── Telegram Bot / Approval Settings ───────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
# Approval mode: "telegram" = require approval, "auto" = send immediately
APPROVAL_MODE = os.getenv("APPROVAL_MODE", "telegram")
APPROVAL_TIMEOUT_HOURS = int(os.getenv("APPROVAL_TIMEOUT_HOURS", "24"))
