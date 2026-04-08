"""Configuration for email outreach system.

Copy this file to config.py and fill in your credentials.
"""

import os

# --- Browser ---
BROWSER_HEADLESS = False
SLOW_MO = 100
BROWSER_DATA_DIR = os.path.join(os.path.dirname(__file__), "browser_data")

# --- Facebook Auto-Login ---
FB_EMAIL = ""
FB_PASSWORD = ""

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
SMTP_HOST = ""
SMTP_PORT = 465
SMTP_EMAIL = ""
SMTP_PASSWORD = ""

# --- Sending ---
DAILY_SEND_LIMIT = 50
SEND_DELAY_MIN = 60
SEND_DELAY_MAX = 180

# --- Follow-up ---
FOLLOWUP_AFTER_DAYS = 3
MAX_FOLLOWUPS = 2

# --- Logging ---
LOG_FILE = os.path.join(os.path.dirname(__file__), "outreach.log")

# --- Database ---
DB_PATH = os.path.join(os.path.dirname(__file__), "outreach.db")

# --- Email Template ---
SUBJECT_LINES = [
    "Stop wasting connects — this changes everything",
    "You're applying wrong on Upwork (fix this now)",
    "This AI tells you which jobs will reply",
    "Why you're getting 0 replies on Upwork",
    "Apply to fewer jobs, get more clients",
    "The 5-second mistake killing your proposals",
    "Top freelancers are using this (you're not)",
    "This tool writes proposals that get replies",
    "You're one good client away — don't miss it",
    "Most freelancers ignore this (big mistake)",
    "Turn 5 proposals into 2 clients",
    "This changes how you use Upwork forever",
    "Still writing proposals manually? Read this",
    "From ignored to hired — fix this today",
    "Your Upwork strategy is broken (here's why)",
]

FOLLOWUP_SUBJECT_LINES = [
    "Just following up — did you see this?",
    "Quick reminder about your Upwork profile",
    "Still interested? The offer stands",
    "Don't miss this — freelancers are loving it",
]

SENDER_NAME = "ProWorkspace"
EXTENSION_URL = "https://proworkspace.online/"
PURCHASE_EXTENSION_URL = "https://proworkspace.online/purchase"
