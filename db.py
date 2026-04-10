import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "cold_email.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT UNIQUE,
            store_name TEXT,
            email TEXT,
            niche TEXT,
            source TEXT,
            first_line TEXT,
            phone TEXT,
            address TEXT,
            rating TEXT,
            website TEXT,
            score INTEGER DEFAULT 0,
            has_chatbot INTEGER DEFAULT 0,
            has_automation INTEGER DEFAULT 0,
            load_time REAL,
            status TEXT DEFAULT 'new',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS sequence_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER REFERENCES leads(id),
            step TEXT,
            sender_inbox TEXT,
            sent_at TEXT,
            message_id TEXT,
            UNIQUE(lead_id, step)
        );

        CREATE TABLE IF NOT EXISTS send_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_inbox TEXT,
            sent_at TEXT DEFAULT (datetime('now')),
            lead_id INTEGER
        );

        CREATE TABLE IF NOT EXISTS replies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER REFERENCES leads(id),
            replied_at TEXT DEFAULT (datetime('now')),
            notes TEXT
        );
    """)
    # Migrate: add new columns if they don't exist
    existing = {row[1] for row in conn.execute("PRAGMA table_info(leads)").fetchall()}
    migrations = {
        "phone": "TEXT",
        "address": "TEXT",
        "rating": "TEXT",
        "website": "TEXT",
        "score": "INTEGER DEFAULT 0",
        "has_chatbot": "INTEGER DEFAULT 0",
        "has_automation": "INTEGER DEFAULT 0",
        "load_time": "REAL",
    }
    for col, col_type in migrations.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE leads ADD COLUMN {col} {col_type}")

    conn.commit()
    conn.close()


# --- Lead operations ---

def add_lead(domain, store_name, email, niche, source, phone=None, address=None,
             rating=None, website=None, score=0, has_chatbot=0, has_automation=0, load_time=None):
    conn = get_conn()
    try:
        conn.execute(
            """INSERT OR IGNORE INTO leads
               (domain, store_name, email, niche, source, phone, address, rating, website,
                score, has_chatbot, has_automation, load_time)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (domain, store_name, email, niche, source, phone, address, rating, website,
             score, has_chatbot, has_automation, load_time)
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def get_leads(status=None, limit=None):
    conn = get_conn()
    query = "SELECT * FROM leads"
    params = []
    if status:
        query += " WHERE status = ?"
        params.append(status)
    query += " ORDER BY created_at DESC"
    if limit:
        query += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_lead(lead_id, **kwargs):
    conn = get_conn()
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [lead_id]
    conn.execute(f"UPDATE leads SET {sets}, updated_at = datetime('now') WHERE id = ?", vals)
    conn.commit()
    conn.close()


# --- Sequence operations ---

def get_sequence_state(lead_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM sequence_state WHERE lead_id = ? ORDER BY sent_at", (lead_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def record_send(lead_id, step, sender_inbox, message_id=None):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO sequence_state (lead_id, step, sender_inbox, sent_at, message_id) VALUES (?, ?, ?, datetime('now'), ?)",
        (lead_id, step, sender_inbox, message_id)
    )
    conn.execute(
        "INSERT INTO send_log (sender_inbox, lead_id) VALUES (?, ?)",
        (sender_inbox, lead_id)
    )
    conn.commit()
    conn.close()


def get_daily_send_count(sender_inbox):
    conn = get_conn()
    today = datetime.now().strftime("%Y-%m-%d")
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM send_log WHERE sender_inbox = ? AND sent_at LIKE ?",
        (sender_inbox, f"{today}%")
    ).fetchone()
    conn.close()
    return row["cnt"]


def get_leads_needing_step(step, delay_days):
    """Get leads that need a specific sequence step sent."""
    conn = get_conn()

    if step == "email_1":
        # New leads that haven't received email_1
        rows = conn.execute("""
            SELECT l.* FROM leads l
            WHERE l.status = 'new' AND l.email IS NOT NULL AND l.email != ''
            AND l.id NOT IN (SELECT lead_id FROM sequence_state WHERE step = 'email_1')
        """).fetchall()
    else:
        # Leads that received the previous step but not this one
        prev_step = "email_1" if step == "follow_up_1" else "follow_up_1"
        rows = conn.execute(f"""
            SELECT l.* FROM leads l
            WHERE l.status IN ('new', 'contacted')
            AND l.email IS NOT NULL AND l.email != ''
            AND l.id IN (
                SELECT lead_id FROM sequence_state
                WHERE step = ? AND julianday('now') - julianday(sent_at) >= ?
            )
            AND l.id NOT IN (SELECT lead_id FROM sequence_state WHERE step = ?)
            AND l.id NOT IN (SELECT lead_id FROM replies)
        """, (prev_step, delay_days, step)).fetchall()

    conn.close()
    return [dict(r) for r in rows]


def mark_replied(lead_id, notes=""):
    conn = get_conn()
    conn.execute("INSERT INTO replies (lead_id, notes) VALUES (?, ?)", (lead_id, notes))
    conn.execute("UPDATE leads SET status = 'replied', updated_at = datetime('now') WHERE id = ?", (lead_id,))
    conn.commit()
    conn.close()


def get_stats():
    conn = get_conn()
    stats = {}
    stats["total_leads"] = conn.execute("SELECT COUNT(*) as c FROM leads").fetchone()["c"]
    stats["with_email"] = conn.execute("SELECT COUNT(*) as c FROM leads WHERE email IS NOT NULL AND email != ''").fetchone()["c"]
    stats["contacted"] = conn.execute("SELECT COUNT(DISTINCT lead_id) as c FROM sequence_state").fetchone()["c"]
    stats["replied"] = conn.execute("SELECT COUNT(*) as c FROM replies").fetchone()["c"]
    stats["today_sent"] = conn.execute(
        "SELECT COUNT(*) as c FROM send_log WHERE sent_at LIKE ?",
        (datetime.now().strftime("%Y-%m-%d") + "%",)
    ).fetchone()["c"]

    # Qualified = has email + score < 40 (no chatbot, no automation = easy sell)
    stats["qualified"] = conn.execute(
        "SELECT COUNT(*) as c FROM leads WHERE email IS NOT NULL AND email != '' AND score < 40"
    ).fetchone()["c"]

    stats["no_chatbot"] = conn.execute(
        "SELECT COUNT(*) as c FROM leads WHERE has_chatbot = 0 AND website IS NOT NULL AND website != ''"
    ).fetchone()["c"]

    stats["no_website"] = conn.execute(
        "SELECT COUNT(*) as c FROM leads WHERE (website IS NULL OR website = '')"
    ).fetchone()["c"]

    # By source
    sources = conn.execute("SELECT source, COUNT(*) as c FROM leads GROUP BY source").fetchall()
    stats["by_source"] = {r["source"]: r["c"] for r in sources}

    steps = conn.execute("""
        SELECT step, COUNT(*) as c FROM sequence_state GROUP BY step
    """).fetchall()
    stats["by_step"] = {r["step"]: r["c"] for r in steps}

    conn.close()
    return stats
