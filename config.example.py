"""Configuration — uses environment variables (for cloud deploy like Koyeb).

Local: copy to config.py and hardcode values.
Cloud: set env vars in Koyeb dashboard.
"""

import os

# --- Browser ---
BROWSER_HEADLESS = True
SLOW_MO = 50
BROWSER_DATA_DIR = os.path.join(os.path.dirname(__file__), "browser_data")

# --- Scroll Settings ---
SCROLL_PAUSE_MIN = 2.0
SCROLL_PAUSE_MAX = 5.0
MAX_SCROLLS = 50
SCROLL_DISTANCE_MIN = 300
SCROLL_DISTANCE_MAX = 700

# --- Collection ---
DAILY_COLLECT_LIMIT = 200
REQUEST_DELAY_MIN = 3
REQUEST_DELAY_MAX = 8

# --- Target ---
TARGET_COUNTRY = "Bangladesh"
TARGET_AUDIENCE = "freelancers"

# --- SMTP ---
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_EMAIL = os.getenv("SMTP_EMAIL", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")

# --- Sending ---
DAILY_SEND_LIMIT = int(os.getenv("DAILY_SEND_LIMIT", "25"))
SEND_DELAY_MIN = 90
SEND_DELAY_MAX = 240

# --- Follow-up ---
FOLLOWUP_AFTER_DAYS = 3
MAX_FOLLOWUPS = 2

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# --- Google Sheets ---
GOOGLE_CREDS_FILE = os.getenv("GOOGLE_CREDS_FILE", os.path.join(os.path.dirname(__file__), "credentials.json"))
SHEET_NAME = "Lead CRM"

# --- Logging ---
LOG_FILE = os.path.join(os.path.dirname(__file__), "outreach.log")

# --- Database ---
DB_PATH = os.path.join(os.path.dirname(__file__), "outreach.db")

# --- Email Template ---
SUBJECT_LINES = [
    "quick question about your Upwork",
    "noticed something about your profile",
    "thought of you",
    "curious about something",
    "re: Upwork proposals",
    "idea for you",
    "saw your work",
    "this might help",
    "honest question",
    "2 minute read",
]

FOLLOWUP_SUBJECT_LINES = [
    "re: my last note",
    "bumping this",
    "any thoughts?",
    "last one from me",
]

SENDER_NAME = "ProWorkspace"
EXTENSION_URL = "https://proworkspace.online/"
PURCHASE_EXTENSION_URL = "https://proworkspace.online/purchase"

# --- IMAP ---
IMAP_HOST = os.getenv("IMAP_HOST", SMTP_HOST)
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
IMAP_EMAIL = SMTP_EMAIL
IMAP_PASSWORD = SMTP_PASSWORD
