"""Configuration for email outreach system."""

import os

# --- Browser ---
BROWSER_HEADLESS = False
SLOW_MO = 100
BROWSER_DATA_DIR = os.path.join(os.path.dirname(__file__), "browser_data")

# --- Facebook Auto-Login ---
FB_EMAIL = "mahadih.jk@gmail.com"
FB_PASSWORD = "#@#Mj91#@#"

# --- Instagram Auto-Login ---
IG_USERNAME = ""
IG_PASSWORD = ""

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

# --- SMTP (for sending) ---
SMTP_HOST = "mail.brandivibe.com"
SMTP_PORT = 465
SMTP_EMAIL = "knock@brandivibe.com"
SMTP_PASSWORD = "Vk+#awH_&]Y3MF]."

# --- Sending ---
DAILY_SEND_LIMIT = 25  # Warming phase — increase weekly: 25 > 35 > 50
SEND_DELAY_MIN = 90    # 1.5 min minimum between emails
SEND_DELAY_MAX = 240   # 4 min max — more human-like

# --- Follow-up ---
FOLLOWUP_AFTER_DAYS = 3
MAX_FOLLOWUPS = 2

# --- Telegram Notifications ---
TELEGRAM_BOT_TOKEN = "8755400487:AAEyzM3X0fBOWtp2CMXvHJNZqTGGIjVn8lc"
TELEGRAM_CHAT_ID = "7120141572"

# --- Google Sheets ---
GOOGLE_CREDS_FILE = os.path.join(os.path.dirname(__file__), "credentials.json")
SHEET_NAME = "Lead CRM"  # Existing sheet — adds "Outreach" tab

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
EXTENSION_URL = "https://proworkspace.online/?utm_source=email&utm_medium=outreach&utm_campaign=freelancer_bd"
PURCHASE_EXTENSION_URL = "https://proworkspace.online/purchase?utm_source=email&utm_medium=outreach&utm_campaign=freelancer_bd"

# --- IMAP (for reply/bounce tracking) ---
IMAP_HOST = "mail.brandivibe.com"
IMAP_PORT = 993
IMAP_EMAIL = "knock@brandivibe.com"
IMAP_PASSWORD = "Vk+#awH_&]Y3MF]."  # Same as SMTP
