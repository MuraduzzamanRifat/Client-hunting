"""SQLite database for email tracking with follow-up support."""

import sqlite3
from datetime import datetime, date, timedelta
from config import DB_PATH, FOLLOWUP_AFTER_DAYS, MAX_FOLLOWUPS


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            name TEXT,
            source TEXT,
            source_url TEXT,
            bio TEXT,
            collected_at TEXT DEFAULT (datetime('now')),
            status TEXT DEFAULT 'new',
            followup_count INTEGER DEFAULT 0,
            last_sent_at TEXT
        );

        CREATE TABLE IF NOT EXISTS send_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email_id INTEGER REFERENCES emails(id),
            subject TEXT,
            sent_at TEXT DEFAULT (datetime('now')),
            provider TEXT,
            email_type TEXT DEFAULT 'initial',
            status TEXT DEFAULT 'sent'
        );

        CREATE TABLE IF NOT EXISTS visited_urls (
            url TEXT PRIMARY KEY,
            source TEXT,
            visited_at TEXT DEFAULT (datetime('now')),
            emails_found INTEGER DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_emails_status ON emails(status);
        CREATE INDEX IF NOT EXISTS idx_emails_source ON emails(source);
        CREATE INDEX IF NOT EXISTS idx_emails_followup ON emails(followup_count, last_sent_at);
    """)

    # Migration: add columns if missing (for existing DBs)
    try:
        conn.execute("SELECT followup_count FROM emails LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE emails ADD COLUMN followup_count INTEGER DEFAULT 0")
        conn.execute("ALTER TABLE emails ADD COLUMN last_sent_at TEXT")

    try:
        conn.execute("SELECT email_type FROM send_log LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE send_log ADD COLUMN email_type TEXT DEFAULT 'initial'")

    conn.commit()
    conn.close()


def add_email(email, name=None, source=None, source_url=None, bio=None):
    """Add email if not already exists. Returns True if new."""
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO emails (email, name, source, source_url, bio) VALUES (?, ?, ?, ?, ?)",
            (email.lower().strip(), name, source, source_url, bio)
        )
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def is_url_visited(url):
    """Check if a URL (profile, group) was already scraped."""
    conn = get_db()
    row = conn.execute("SELECT 1 FROM visited_urls WHERE url = ?", (url,)).fetchone()
    conn.close()
    return row is not None


def mark_url_visited(url, source, emails_found=0):
    """Mark a URL as visited to avoid re-scraping."""
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO visited_urls (url, source, emails_found) VALUES (?, ?, ?)",
        (url, source, emails_found)
    )
    conn.commit()
    conn.close()


def email_exists(email):
    """Check if an email is already in the database."""
    conn = get_db()
    row = conn.execute("SELECT 1 FROM emails WHERE email = ?", (email.lower().strip(),)).fetchone()
    conn.close()
    return row is not None


def was_sent_today(email_id):
    """Check if this email was already sent today (prevent double-send)."""
    conn = get_db()
    today = date.today().isoformat()
    row = conn.execute(
        "SELECT 1 FROM send_log WHERE email_id = ? AND date(sent_at) = ?",
        (email_id, today)
    ).fetchone()
    conn.close()
    return row is not None


def get_unsent_emails(limit=50):
    """Get emails that haven't been sent yet."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM emails WHERE status = 'new' ORDER BY collected_at ASC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return rows


def get_followup_emails(limit=50):
    """Get emails due for follow-up (sent X days ago, under max followups)."""
    cutoff = (datetime.now() - timedelta(days=FOLLOWUP_AFTER_DAYS)).isoformat()
    conn = get_db()
    rows = conn.execute(
        """SELECT * FROM emails
           WHERE status = 'sent'
             AND followup_count < ?
             AND last_sent_at < ?
           ORDER BY last_sent_at ASC
           LIMIT ?""",
        (MAX_FOLLOWUPS, cutoff, limit)
    ).fetchall()
    conn.close()
    return rows


def mark_sent(email_id, subject, provider, email_type='initial'):
    conn = get_db()
    now = datetime.now().isoformat()
    if email_type == 'followup':
        conn.execute(
            "UPDATE emails SET followup_count = followup_count + 1, last_sent_at = ? WHERE id = ?",
            (now, email_id)
        )
    else:
        conn.execute(
            "UPDATE emails SET status = 'sent', last_sent_at = ? WHERE id = ?",
            (now, email_id)
        )
    conn.execute(
        "INSERT INTO send_log (email_id, subject, provider, email_type) VALUES (?, ?, ?, ?)",
        (email_id, subject, provider, email_type)
    )
    conn.commit()
    conn.close()


def mark_failed(email_id):
    conn = get_db()
    conn.execute("UPDATE emails SET status = 'skipped' WHERE id = ?", (email_id,))
    conn.commit()
    conn.close()


def get_today_send_count():
    conn = get_db()
    today = date.today().isoformat()
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM send_log WHERE date(sent_at) = ?", (today,)
    ).fetchone()
    conn.close()
    return row["cnt"]


def get_stats():
    conn = get_db()
    stats = {}
    stats["total"] = conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
    stats["new"] = conn.execute("SELECT COUNT(*) FROM emails WHERE status='new'").fetchone()[0]
    stats["sent"] = conn.execute("SELECT COUNT(*) FROM emails WHERE status='sent'").fetchone()[0]
    stats["skipped"] = conn.execute("SELECT COUNT(*) FROM emails WHERE status='skipped'").fetchone()[0]
    stats["today_sent"] = get_today_send_count()

    # Follow-up stats
    cutoff = (datetime.now() - timedelta(days=FOLLOWUP_AFTER_DAYS)).isoformat()
    stats["due_followup"] = conn.execute(
        "SELECT COUNT(*) FROM emails WHERE status='sent' AND followup_count < ? AND last_sent_at < ?",
        (MAX_FOLLOWUPS, cutoff)
    ).fetchone()[0]

    conn.close()
    return stats


def get_all_emails_for_sync():
    """Get all email rows for syncing to Google Sheets."""
    conn = get_db()
    rows = conn.execute("SELECT * FROM emails ORDER BY collected_at ASC").fetchall()
    conn.close()
    return rows


# Initialize on import
init_db()
