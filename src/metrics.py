"""
Metrics Tracker — Collects and stores all system metrics for the monitoring dashboard.
Uses SQLite for persistence, lightweight and zero-dependency.
"""

import os
import sqlite3
import time
import threading
from datetime import datetime, timedelta
from collections import defaultdict

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "metrics.db")
_lock = threading.Lock()


def _get_db():
    """Get a thread-local DB connection."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """Create metrics tables if they don't exist."""
    conn = _get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS email_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            event_type TEXT NOT NULL,
            recipient TEXT,
            subject TEXT,
            status TEXT,
            details TEXT,
            campaign TEXT DEFAULT '',
            domain TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS metrics_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            metric TEXT NOT NULL,
            value REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            level TEXT NOT NULL,
            message TEXT NOT NULL,
            resolved INTEGER DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_events_ts ON email_events(timestamp);
        CREATE INDEX IF NOT EXISTS idx_events_type ON email_events(event_type);
        CREATE INDEX IF NOT EXISTS idx_metrics_ts ON metrics_log(timestamp);
    """)
    conn.close()


# Initialize on import
init_db()


# ── Event Logging ────────────────────────────────────────────────────

def log_event(event_type: str, recipient: str = "", subject: str = "",
              status: str = "", details: str = "", campaign: str = ""):
    """Log an email event. Prevents duplicate sent/collected events for the same recipient today."""
    domain = recipient.split("@")[-1] if "@" in recipient else ""
    today = datetime.now().strftime("%Y-%m-%d")

    with _lock:
        conn = _get_db()
        # Prevent duplicate sent/collected events for same recipient same day
        if event_type in ("sent", "collected") and recipient:
            existing = conn.execute(
                "SELECT id FROM email_events WHERE event_type = ? AND recipient = ? AND timestamp LIKE ?",
                (event_type, recipient, f"{today}%")
            ).fetchone()
            if existing:
                conn.close()
                return

        conn.execute(
            "INSERT INTO email_events (timestamp, event_type, recipient, subject, status, details, campaign, domain) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (datetime.now().isoformat(), event_type, recipient, subject, status, details, campaign, domain)
        )
        conn.commit()
        conn.close()

    # Check alert thresholds after each event
    _check_alerts()


def log_metric(metric: str, value: float):
    """Log a numeric metric (api_latency, queue_size, etc.)."""
    with _lock:
        conn = _get_db()
        conn.execute(
            "INSERT INTO metrics_log (timestamp, metric, value) VALUES (?, ?, ?)",
            (datetime.now().isoformat(), metric, value)
        )
        conn.commit()
        conn.close()


# ── Alert System ─────────────────────────────────────────────────────

ALERT_THRESHOLDS = {
    "bounce_rate": {"warn": 0.03, "critical": 0.05},
    "fail_rate": {"warn": 0.10, "critical": 0.20},
}


def _check_alerts():
    """Check metrics against thresholds and create alerts."""
    stats = get_today_stats()
    total_sent = stats.get("sent", 0)
    if total_sent < 3:
        return  # not enough data

    bounce_rate = stats.get("bounced", 0) / total_sent
    fail_rate = stats.get("failed", 0) / total_sent

    if bounce_rate >= ALERT_THRESHOLDS["bounce_rate"]["critical"]:
        _create_alert("critical", f"Bounce rate critical: {bounce_rate*100:.1f}% — auto-pause recommended")
    elif bounce_rate >= ALERT_THRESHOLDS["bounce_rate"]["warn"]:
        _create_alert("warning", f"Bounce rate elevated: {bounce_rate*100:.1f}% — monitor closely")

    if fail_rate >= ALERT_THRESHOLDS["fail_rate"]["critical"]:
        _create_alert("critical", f"Failure rate critical: {fail_rate*100:.1f}% — check SMTP connection")


def _create_alert(level: str, message: str):
    """Create an alert if a similar one doesn't already exist in the last hour."""
    with _lock:
        conn = _get_db()
        one_hour_ago = (datetime.now() - timedelta(hours=1)).isoformat()
        existing = conn.execute(
            "SELECT id FROM alerts WHERE message = ? AND timestamp > ? AND resolved = 0",
            (message, one_hour_ago)
        ).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO alerts (timestamp, level, message) VALUES (?, ?, ?)",
                (datetime.now().isoformat(), level, message)
            )
            conn.commit()
        conn.close()


def get_alerts(limit: int = 20) -> list[dict]:
    """Get recent alerts."""
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM alerts ORDER BY timestamp DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def resolve_alert(alert_id: int):
    """Mark an alert as resolved."""
    with _lock:
        conn = _get_db()
        conn.execute("UPDATE alerts SET resolved = 1 WHERE id = ?", (alert_id,))
        conn.commit()
        conn.close()


# ── Query Functions ──────────────────────────────────────────────────

def get_today_stats() -> dict:
    """Get today's email stats."""
    today = datetime.now().strftime("%Y-%m-%d")
    conn = _get_db()
    rows = conn.execute(
        "SELECT event_type, COUNT(*) as cnt FROM email_events "
        "WHERE timestamp LIKE ? GROUP BY event_type",
        (f"{today}%",)
    ).fetchall()
    conn.close()
    return {row["event_type"]: row["cnt"] for row in rows}


def get_hourly_stats(hours: int = 24) -> list[dict]:
    """Get hourly breakdown of events for the last N hours."""
    since = (datetime.now() - timedelta(hours=hours)).isoformat()
    conn = _get_db()
    rows = conn.execute(
        "SELECT strftime('%Y-%m-%d %H:00', timestamp) as hour, event_type, COUNT(*) as cnt "
        "FROM email_events WHERE timestamp > ? "
        "GROUP BY hour, event_type ORDER BY hour",
        (since,)
    ).fetchall()
    conn.close()

    hourly = defaultdict(lambda: {"sent": 0, "delivered": 0, "bounced": 0, "failed": 0, "collected": 0, "opened": 0, "replied": 0})
    for row in rows:
        hourly[row["hour"]][row["event_type"]] = row["cnt"]

    return [{"hour": h, **v} for h, v in sorted(hourly.items())]


def get_daily_stats(days: int = 30) -> list[dict]:
    """Get daily breakdown for the last N days."""
    since = (datetime.now() - timedelta(days=days)).isoformat()
    conn = _get_db()
    rows = conn.execute(
        "SELECT strftime('%Y-%m-%d', timestamp) as day, event_type, COUNT(*) as cnt "
        "FROM email_events WHERE timestamp > ? "
        "GROUP BY day, event_type ORDER BY day",
        (since,)
    ).fetchall()
    conn.close()

    daily = defaultdict(lambda: {"sent": 0, "delivered": 0, "bounced": 0, "failed": 0, "collected": 0})
    for row in rows:
        daily[row["day"]][row["event_type"]] = row["cnt"]

    return [{"day": d, **v} for d, v in sorted(daily.items())]


def get_domain_stats() -> list[dict]:
    """Get stats broken down by recipient domain."""
    conn = _get_db()
    rows = conn.execute(
        "SELECT domain, event_type, COUNT(*) as cnt "
        "FROM email_events WHERE domain != '' "
        "GROUP BY domain, event_type ORDER BY domain"
    ).fetchall()
    conn.close()

    domains = defaultdict(lambda: {"sent": 0, "bounced": 0, "failed": 0, "delivered": 0})
    for row in rows:
        domains[row["domain"]][row["event_type"]] = row["cnt"]

    result = []
    for domain, stats in sorted(domains.items(), key=lambda x: sum(x[1].values()), reverse=True):
        total = stats["sent"] + stats["delivered"]
        bounce_rate = stats["bounced"] / total * 100 if total > 0 else 0
        result.append({"domain": domain, "bounce_rate": round(bounce_rate, 1), **stats})

    return result[:20]


def get_event_log(limit: int = 50, event_type: str = "") -> list[dict]:
    """Get recent events for the log view."""
    conn = _get_db()
    if event_type:
        rows = conn.execute(
            "SELECT * FROM email_events WHERE event_type = ? ORDER BY timestamp DESC LIMIT ?",
            (event_type, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM email_events ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_funnel() -> dict:
    """Get the email funnel: collected → sent → delivered → opened → replied."""
    conn = _get_db()
    rows = conn.execute(
        "SELECT event_type, COUNT(*) as cnt FROM email_events GROUP BY event_type"
    ).fetchall()
    conn.close()
    stats = {row["event_type"]: row["cnt"] for row in rows}
    return {
        "collected": stats.get("collected", 0),
        "sent": stats.get("sent", 0),
        "delivered": stats.get("delivered", 0) + stats.get("sent", 0),  # assume delivered if not bounced
        "bounced": stats.get("bounced", 0),
        "failed": stats.get("failed", 0),
        "opened": stats.get("opened", 0),
        "replied": stats.get("replied", 0),
    }


def get_sending_heatmap() -> list[dict]:
    """Get sending volume by day-of-week and hour for heatmap."""
    conn = _get_db()
    rows = conn.execute(
        "SELECT strftime('%w', timestamp) as dow, strftime('%H', timestamp) as hour, COUNT(*) as cnt "
        "FROM email_events WHERE event_type = 'sent' "
        "GROUP BY dow, hour"
    ).fetchall()
    conn.close()
    return [{"dow": int(r["dow"]), "hour": int(r["hour"]), "count": r["cnt"]} for r in rows]


def get_summary() -> dict:
    """Get complete dashboard summary."""
    today = get_today_stats()
    funnel = get_funnel()
    total_sent = funnel.get("sent", 0)

    return {
        "today": today,
        "funnel": funnel,
        "rates": {
            "delivery": round((1 - funnel["bounced"] / total_sent) * 100, 1) if total_sent > 0 else 0,
            "bounce": round(funnel["bounced"] / total_sent * 100, 1) if total_sent > 0 else 0,
            "reply": round(funnel["replied"] / total_sent * 100, 1) if total_sent > 0 else 0,
        },
        "total_sent": total_sent,
        "total_collected": funnel["collected"],
    }
