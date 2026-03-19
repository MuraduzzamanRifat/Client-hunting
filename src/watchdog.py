"""
Autonomous Watchdog — Self-healing system controller.

Runs continuously in the background. Monitors all system metrics,
detects anomalies, takes automatic corrective actions, and reports via Telegram.

Responsibilities:
  1. Real-time metric monitoring (every 60s)
  2. Anomaly detection with auto-actions
  3. Email quality scanning before send
  4. Daily autonomous audit report
  5. Learning-driven optimization
"""

import os
import sys
import time
import threading
import re
import json
import sqlite3
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "metrics.db")
APPROVALS_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "approvals.db")

CHECK_INTERVAL = 60  # seconds between checks
DAILY_REPORT_HOUR = 9  # send daily report at 9 AM
_last_daily_report = None
_system_paused = False
_original_daily_cap = config.MAX_EMAILS_PER_DAY


# ── Thresholds ───────────────────────────────────────────────────────

THRESHOLDS = {
    "bounce_rate_warn": 0.03,
    "bounce_rate_critical": 0.05,
    "spam_complaint_rate": 0.003,
    "min_delivery_rate": 0.90,
    "reply_rate_drop_pct": 50,  # % drop from average
    "max_api_errors_per_hour": 10,
    "max_duplicates_per_day": 3,
}

# ── Spam Trigger Words ───────────────────────────────────────────────

SPAM_TRIGGERS = [
    r"\bfree\b", r"\bact now\b", r"\blimited time\b", r"\burgent\b",
    r"\bcongratulations\b", r"\bwinner\b", r"\b100%\b", r"\bguarantee\b",
    r"\bno obligation\b", r"\brisk.?free\b", r"\bcash\b", r"\b\$\$\$\b",
    r"\bbuy now\b", r"\border now\b", r"\bclick (here|below)\b",
    r"\bdouble your\b", r"\bearn (money|extra|cash)\b", r"\bmake money\b",
    r"\bmlm\b", r"\bpyramid\b", r"\bincrease your\b", r"\blowest price\b",
    r"\bonce in a lifetime\b", r"\bspecial promotion\b", r"\bexclusive deal\b",
    r"!!!", r"\bFREE\b", r"\bURGENT\b", r"\bACT NOW\b",
]


# ── Helpers ──────────────────────────────────────────────────────────

def _get_metrics_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _get_approvals_db():
    conn = sqlite3.connect(APPROVALS_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _notify(level: str, issue: str, metric: str, action: str, status: str):
    """Send alert to Telegram."""
    emoji = {"critical": "🚨", "warning": "⚠️", "info": "ℹ️"}.get(level, "📢")

    text = (
        f"{emoji} <b>{level.upper()} Alert</b>\n\n"
        f"<b>Issue:</b> {issue}\n"
        f"<b>Metric:</b> {metric}\n"
        f"<b>Action Taken:</b> {action}\n"
        f"<b>Status:</b> {status}"
    )

    if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID:
        try:
            import requests
            requests.post(
                f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": config.TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
                timeout=10
            )
        except Exception as e:
            print(f"[WATCHDOG] Telegram notify failed: {e}")

    print(f"[WATCHDOG] {emoji} {level}: {issue} | Action: {action}")


def _log_action(action: str, details: str = ""):
    """Log watchdog actions to metrics DB."""
    try:
        conn = _get_metrics_db()
        conn.execute(
            "INSERT INTO metrics_log (timestamp, metric, value) VALUES (?, ?, ?)",
            (datetime.now().isoformat(), f"watchdog_{action}", 1)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ── Metric Collection ────────────────────────────────────────────────

def _get_today_events() -> dict:
    """Get today's event counts."""
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        conn = _get_metrics_db()
        rows = conn.execute(
            "SELECT event_type, COUNT(*) as cnt FROM email_events WHERE timestamp LIKE ? GROUP BY event_type",
            (f"{today}%",)
        ).fetchall()
        conn.close()
        return {r["event_type"]: r["cnt"] for r in rows}
    except Exception:
        return {}


def _get_hourly_errors() -> int:
    """Count API/system errors in the last hour."""
    one_hour_ago = (datetime.now() - timedelta(hours=1)).isoformat()
    try:
        conn = _get_metrics_db()
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM email_events WHERE event_type IN ('failed', 'bounced') AND timestamp > ?",
            (one_hour_ago,)
        ).fetchone()
        conn.close()
        return row["cnt"] if row else 0
    except Exception:
        return 0


def _get_duplicate_count_today() -> int:
    """Check for duplicate sends today."""
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        conn = _get_metrics_db()
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM ("
            "  SELECT recipient, COUNT(*) as c FROM email_events "
            "  WHERE event_type = 'sent' AND timestamp LIKE ? "
            "  GROUP BY recipient HAVING c > 1"
            ")",
            (f"{today}%",)
        ).fetchone()
        conn.close()
        return row["cnt"] if row else 0
    except Exception:
        return 0


def _get_average_reply_rate(days: int = 7) -> float:
    """Get average reply rate over past N days."""
    since = (datetime.now() - timedelta(days=days)).isoformat()
    try:
        conn = _get_metrics_db()
        sent = conn.execute(
            "SELECT COUNT(*) as cnt FROM email_events WHERE event_type = 'sent' AND timestamp > ?",
            (since,)
        ).fetchone()["cnt"]
        replied = conn.execute(
            "SELECT COUNT(*) as cnt FROM email_events WHERE event_type = 'replied' AND timestamp > ?",
            (since,)
        ).fetchone()["cnt"]
        conn.close()
        return replied / max(sent, 1)
    except Exception:
        return 0


# ── Auto-Actions ─────────────────────────────────────────────────────

def _pause_system(reason: str):
    """Pause all email sending."""
    global _system_paused
    if _system_paused:
        return  # already paused
    _system_paused = True
    config.MAX_EMAILS_PER_DAY = 0
    _log_action("paused", reason)
    _notify("critical", reason,
            "System paused", "All sending stopped automatically",
            "PAUSED — reply /resume to Telegram bot to restart")


def _resume_system():
    """Resume email sending."""
    global _system_paused
    _system_paused = False
    config.MAX_EMAILS_PER_DAY = _original_daily_cap
    _log_action("resumed", "")
    _notify("info", "System resumed",
            f"Daily cap restored to {_original_daily_cap}",
            "Sending enabled", "ACTIVE")


def _reduce_volume(pct: int = 50):
    """Reduce daily sending volume by percentage."""
    new_cap = max(1, int(_original_daily_cap * (1 - pct / 100)))
    config.MAX_EMAILS_PER_DAY = new_cap
    _log_action("volume_reduced", f"Reduced to {new_cap}")
    _notify("warning", f"Sending volume reduced by {pct}%",
            f"New daily cap: {new_cap} (was {_original_daily_cap})",
            "Auto-reduced to protect reputation",
            "Volume reduced — monitoring")


def is_system_paused() -> bool:
    """Check if watchdog has paused the system."""
    return _system_paused


# ── Email Quality Scanner ────────────────────────────────────────────

def scan_email_quality(subject: str, body: str) -> dict:
    """
    Scan an email for quality issues before sending.

    Returns:
        {"safe": bool, "issues": [str], "score": int (0-100)}
    """
    issues = []
    score = 100

    text = f"{subject} {body}".lower()

    # Check spam triggers
    spam_found = []
    for pattern in SPAM_TRIGGERS:
        if re.search(pattern, text, re.IGNORECASE):
            spam_found.append(pattern.replace(r"\b", "").replace("\\b", ""))

    if spam_found:
        issues.append(f"Spam triggers found: {', '.join(spam_found[:3])}")
        score -= min(len(spam_found) * 10, 40)

    # Check for ALL CAPS words (more than 2)
    caps_words = re.findall(r"\b[A-Z]{3,}\b", f"{subject} {body}")
    if len(caps_words) > 2:
        issues.append(f"Too many ALL CAPS words: {', '.join(caps_words[:3])}")
        score -= 15

    # Check for excessive exclamation marks
    excl_count = text.count("!")
    if excl_count > 2:
        issues.append(f"{excl_count} exclamation marks — looks spammy")
        score -= 10

    # Check subject length
    if len(subject) > 60:
        issues.append("Subject too long (>60 chars) — may get truncated")
        score -= 5
    if len(subject) < 5:
        issues.append("Subject too short")
        score -= 10

    # Check body length
    body_words = len(body.split())
    if body_words > 200:
        issues.append(f"Body too long ({body_words} words) — keep under 120")
        score -= 10
    if body_words < 10:
        issues.append("Body too short — may look automated")
        score -= 15

    # Check personalization
    if "{{" in body or "[Name]" in body or "[Your Name]" in body:
        issues.append("Unresolved template variables detected")
        score -= 20

    # Check for unsubscribe option (added by email_sender at send time, so relax this check)
    body_lower = body.lower()
    has_unsub = any(w in body_lower for w in ["unsubscribe", "stop", "opt out", "remove me", "opt-out"])
    if not has_unsub:
        issues.append("No unsubscribe option -- will be added at send time")
        # Don't penalize since email_sender adds it automatically

    return {
        "safe": score >= 60 and not any("spam" in i.lower() for i in issues),
        "score": max(0, score),
        "issues": issues,
    }


# ── Monitoring Checks ────────────────────────────────────────────────

def _check_bounce_rate(events: dict):
    """Check bounce rate and take action."""
    sent = events.get("sent", 0)
    bounced = events.get("bounced", 0)
    if sent < 3:
        return

    rate = bounced / sent
    if rate >= THRESHOLDS["bounce_rate_critical"]:
        _pause_system(f"Bounce rate critical: {rate*100:.1f}% (threshold: {THRESHOLDS['bounce_rate_critical']*100}%)")
    elif rate >= THRESHOLDS["bounce_rate_warn"]:
        _reduce_volume(50)


def _check_error_rate():
    """Check hourly error rate."""
    errors = _get_hourly_errors()
    if errors >= THRESHOLDS["max_api_errors_per_hour"]:
        _reduce_volume(70)
        _notify("warning", f"{errors} errors in the last hour",
                f"Error count: {errors}", "Volume reduced by 70%",
                "Monitoring — will resume if errors stop")


def _check_duplicates():
    """Check for duplicate sends."""
    dupes = _get_duplicate_count_today()
    if dupes >= THRESHOLDS["max_duplicates_per_day"]:
        _notify("warning", f"{dupes} duplicate sends detected today",
                f"Duplicates: {dupes}", "Review dedup logic",
                "Monitoring")


def _check_followup_limits():
    """Ensure no recipients exceed follow-up limits."""
    try:
        conn = _get_approvals_db()
        violations = conn.execute(
            "SELECT email, emails_sent FROM recipient_tracker WHERE emails_sent > 3 AND status = 'active'"
        ).fetchall()
        conn.close()

        for v in violations:
            conn = _get_approvals_db()
            conn.execute(
                "UPDATE recipient_tracker SET status = 'stopped', next_followup_at = '' WHERE email = ?",
                (v["email"],)
            )
            conn.commit()
            conn.close()
            _log_action("followup_stopped", f"{v['email']} exceeded limit ({v['emails_sent']} sent)")

        if violations:
            _notify("warning", f"{len(violations)} recipients exceeded follow-up limit",
                    f"Max allowed: 3", "Auto-stopped",
                    f"Stopped: {', '.join(v['email'] for v in violations[:3])}")
    except Exception:
        pass


# ── Main Check Cycle ─────────────────────────────────────────────────

def run_check():
    """Run one monitoring cycle."""
    events = _get_today_events()

    _check_bounce_rate(events)
    _check_error_rate()
    _check_duplicates()
    _check_followup_limits()

    # Auto-resume if conditions improved and system was paused
    if _system_paused:
        sent = events.get("sent", 0)
        bounced = events.get("bounced", 0)
        if sent >= 5:
            rate = bounced / sent
            if rate < THRESHOLDS["bounce_rate_warn"]:
                _resume_system()


# ── Daily Audit Report ───────────────────────────────────────────────

def generate_daily_report() -> str:
    """Generate comprehensive daily audit report."""
    events = _get_today_events()
    sent = events.get("sent", 0)
    bounced = events.get("bounced", 0)
    collected = events.get("collected", 0)
    failed = events.get("failed", 0)

    delivery_rate = ((sent - bounced) / max(sent, 1)) * 100
    bounce_rate = (bounced / max(sent, 1)) * 100

    # Follow-up stats
    try:
        conn = _get_approvals_db()
        fu_stats = {}
        rows = conn.execute("SELECT status, COUNT(*) as cnt FROM recipient_tracker GROUP BY status").fetchall()
        for r in rows:
            fu_stats[r["status"]] = r["cnt"]

        pending_approvals = conn.execute(
            "SELECT COUNT(*) as cnt FROM approval_queue WHERE status = 'pending'"
        ).fetchone()["cnt"]
        conn.close()
    except Exception:
        fu_stats = {}
        pending_approvals = 0

    # Determine health status
    if _system_paused:
        health = "PAUSED"
        health_emoji = "🔴"
    elif bounce_rate > 3:
        health = "AT RISK"
        health_emoji = "🟡"
    else:
        health = "HEALTHY"
        health_emoji = "🟢"

    # Build report
    report = (
        f"📊 <b>Daily System Audit</b>\n"
        f"📅 {datetime.now().strftime('%Y-%m-%d')}\n"
        f"{'─' * 28}\n\n"

        f"{health_emoji} <b>System Status:</b> {health}\n\n"

        f"<b>Sending Metrics</b>\n"
        f"  Sent: {sent}\n"
        f"  Bounced: {bounced}\n"
        f"  Failed: {failed}\n"
        f"  Collected: {collected}\n"
        f"  Delivery rate: {delivery_rate:.1f}%\n"
        f"  Bounce rate: {bounce_rate:.1f}%\n\n"

        f"<b>Follow-Up Status</b>\n"
        f"  Active: {fu_stats.get('active', 0)}\n"
        f"  Replied: {fu_stats.get('replied', 0)}\n"
        f"  Stopped: {fu_stats.get('stopped', 0)}\n"
        f"  Pending approvals: {pending_approvals}\n\n"

        f"<b>Daily Cap:</b> {config.MAX_EMAILS_PER_DAY}\n"
        f"<b>Approval Mode:</b> {config.APPROVAL_MODE}\n"
    )

    # Add issues if any
    issues = []
    if bounce_rate > 5:
        issues.append("High bounce rate — clean your lead list")
    if failed > 3:
        issues.append(f"{failed} failed sends — check SMTP connection")
    if pending_approvals > 10:
        issues.append(f"{pending_approvals} pending approvals — check Telegram")

    if issues:
        report += f"\n<b>Issues</b>\n"
        for i in issues:
            report += f"  ⚠️ {i}\n"
    else:
        report += f"\n✅ No issues detected\n"

    # Recommendations
    recs = []
    if sent < 5 and not _system_paused:
        recs.append("Sending volume is low — consider increasing daily cap")
    if bounce_rate == 0 and sent >= 5:
        recs.append("Perfect delivery — safe to increase volume")
    if fu_stats.get("replied", 0) > 0:
        recs.append(f"{fu_stats['replied']} replies received — check inbox and respond")

    if recs:
        report += f"\n<b>Recommendations</b>\n"
        for r in recs:
            report += f"  💡 {r}\n"

    return report


def send_daily_report():
    """Send daily report via Telegram."""
    report = generate_daily_report()
    if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID:
        try:
            import requests
            requests.post(
                f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": config.TELEGRAM_CHAT_ID, "text": report, "parse_mode": "HTML"},
                timeout=10
            )
        except Exception as e:
            print(f"[WATCHDOG] Daily report send failed: {e}")
    print(f"[WATCHDOG] Daily report sent")


# ── Background Loop ──────────────────────────────────────────────────

def _watchdog_loop():
    """Main watchdog loop — runs continuously."""
    global _last_daily_report
    print(f"[WATCHDOG] Started — checking every {CHECK_INTERVAL}s")
    print(f"[WATCHDOG] Daily report at {DAILY_REPORT_HOUR}:00")

    while True:
        try:
            run_check()

            # Daily report at configured hour
            now = datetime.now()
            if now.hour == DAILY_REPORT_HOUR:
                today = now.strftime("%Y-%m-%d")
                if _last_daily_report != today:
                    send_daily_report()
                    _last_daily_report = today

                    # Run learning cycle daily
                    try:
                        from src.smart_scorer import run_learning_cycle
                        result = run_learning_cycle()
                        if result.get("status") == "updated":
                            print(f"[WATCHDOG] Learning cycle: weights updated")
                    except Exception as e:
                        print(f"[WATCHDOG] Learning cycle error: {e}")

        except Exception as e:
            print(f"[WATCHDOG] Check cycle error: {e}")

        time.sleep(CHECK_INTERVAL)


def start_watchdog():
    """Start watchdog as background thread."""
    thread = threading.Thread(target=_watchdog_loop, daemon=True)
    thread.start()
    print("[WATCHDOG] Background thread started.")


# ── Telegram Commands ────────────────────────────────────────────────

def handle_watchdog_command(command: str) -> str:
    """Handle watchdog-related Telegram commands."""
    if command == "/status":
        status = "PAUSED" if _system_paused else "ACTIVE"
        return (
            f"<b>Watchdog Status</b>\n\n"
            f"System: {status}\n"
            f"Daily cap: {config.MAX_EMAILS_PER_DAY}\n"
            f"Approval mode: {config.APPROVAL_MODE}\n"
            f"Check interval: {CHECK_INTERVAL}s"
        )
    elif command == "/resume":
        if _system_paused:
            _resume_system()
            return "System resumed. Sending enabled."
        return "System is already active."
    elif command == "/pause":
        _pause_system("Manual pause via Telegram")
        return "System paused. All sending stopped."
    elif command == "/report":
        send_daily_report()
        return "Daily report sent."
    return ""
