"""
Smart Lead Scoring Engine — Advanced AI-driven lead scoring.

Scores leads 0-100 based on 6 weighted dimensions:
  1. Buying Intent (0-25)    — Do they need our service?
  2. Business Maturity (0-20) — Can they pay?
  3. Revenue Potential (0-20)  — Is the deal worth pursuing?
  4. Website Quality Gap (0-15) — How big is the opportunity?
  5. Engagement Signals (0-10)  — Have they shown interest?
  6. Risk Factor (0-10 penalty) — Is this lead safe to contact?

Includes a learning loop that adjusts weights based on outcomes.
"""

import os
import sys
import json
import sqlite3
import threading
import re
from datetime import datetime
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "approvals.db")
_lock = threading.Lock()

# ── High-Value Industries ────────────────────────────────────────────
# Industries where businesses spend more on web/SEO services
HIGH_VALUE_INDUSTRIES = [
    "lawyer", "attorney", "legal", "law firm", " law",
    "dentist", "dental", "doctor", "medical", "clinic", "healthcare",
    "real estate", "realtor", "property",
    "plumber", "plumbing", "hvac", "roofing", "contractor",
    "insurance", "financial", "accounting", "cpa",
    "auto repair", "mechanic", "car dealer",
    "salon", "spa", "medspa", "cosmetic",
    "veterinar", "pet",
    "restaurant", "catering",
    "hotel", "resort",
]

MEDIUM_VALUE_INDUSTRIES = [
    "cafe", "coffee", "bakery", "bar", "pub",
    "gym", "fitness", "yoga", "personal trainer",
    "cleaning", "landscaping", "moving",
    "photographer", "wedding", "event",
    "tutoring", "school", "academy",
    "florist", "gift", "boutique",
]

# ── Weak Website Signals ─────────────────────────────────────────────
WEAK_SITE_PLATFORMS = [
    "wix.com", "weebly.com", "squarespace.com", "godaddy.com",
    "wordpress.com", "sites.google.com", "blogspot.com",
    "facebook.com", "yelp.com",
]


def _get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _init_learning_db():
    """Create learning table for weight adjustments."""
    conn = _get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS scoring_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            original_score INTEGER,
            outcome TEXT DEFAULT '',
            timestamp TEXT NOT NULL,
            factors TEXT DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS scoring_weights (
            id INTEGER PRIMARY KEY,
            buying_intent REAL DEFAULT 1.0,
            business_maturity REAL DEFAULT 1.0,
            revenue_potential REAL DEFAULT 1.0,
            website_gap REAL DEFAULT 1.0,
            engagement REAL DEFAULT 1.0,
            updated_at TEXT DEFAULT ''
        );
    """)
    # Ensure default weights exist
    existing = conn.execute("SELECT id FROM scoring_weights WHERE id = 1").fetchone()
    if not existing:
        conn.execute(
            "INSERT INTO scoring_weights (id, buying_intent, business_maturity, revenue_potential, website_gap, engagement, updated_at) "
            "VALUES (1, 1.0, 1.0, 1.0, 1.0, 1.0, ?)",
            (datetime.now().isoformat(),)
        )
        conn.commit()
    conn.close()


_init_learning_db()


def _get_weights() -> dict:
    """Get current scoring weights (adjusted by learning)."""
    conn = _get_db()
    row = conn.execute("SELECT * FROM scoring_weights WHERE id = 1").fetchone()
    conn.close()
    if row:
        return {
            "buying_intent": row["buying_intent"],
            "business_maturity": row["business_maturity"],
            "revenue_potential": row["revenue_potential"],
            "website_gap": row["website_gap"],
            "engagement": row["engagement"],
        }
    return {"buying_intent": 1.0, "business_maturity": 1.0, "revenue_potential": 1.0,
            "website_gap": 1.0, "engagement": 1.0}


# ── Scoring Dimensions ───────────────────────────────────────────────

def _score_buying_intent(lead: dict) -> tuple[int, list[str]]:
    """
    Score 0-25: Does this lead need our service?
    - No website → HIGH (need WordPress)
    - Has website but weak platform → HIGH (need upgrade)
    - Has website but poor SEO signals → HIGH (need SEO)
    - Strong online presence → LOW
    """
    score = 0
    reasons = []
    website = str(lead.get("Website", "")).strip()

    if not website:
        score = 22
        reasons.append("No website — strong WordPress opportunity")
    else:
        domain = urlparse(website).netloc.lower()
        # Check if on a weak platform
        if any(p in domain for p in WEAK_SITE_PLATFORMS):
            score = 18
            reasons.append(f"Website on weak platform ({domain}) — upgrade opportunity")
        elif "facebook.com" in website.lower() or "yelp.com" in website.lower():
            score = 20
            reasons.append("Using social page as website — needs real site")
        else:
            # Has a real website — SEO opportunity
            score = 12
            reasons.append("Has website — SEO/content automation target")

    return min(score, 25), reasons


def _score_business_maturity(lead: dict) -> tuple[int, list[str]]:
    """
    Score 0-20: Is this a real, active business that can pay?
    - Many reviews = established
    - High rating = quality business
    - Phone number = legitimate
    """
    score = 0
    reasons = []

    try:
        reviews = int(str(lead.get("Reviews", 0) or 0).replace(",", ""))
    except (ValueError, TypeError):
        reviews = 0

    try:
        rating = float(lead.get("Rating", 0) or 0)
    except (ValueError, TypeError):
        rating = 0

    # Reviews indicate business activity
    if reviews >= 100:
        score += 10
        reasons.append(f"{reviews} reviews — established business")
    elif reviews >= 30:
        score += 7
        reasons.append(f"{reviews} reviews — active business")
    elif reviews >= 5:
        score += 4
        reasons.append(f"{reviews} reviews — growing business")
    else:
        score += 1

    # Rating quality
    if rating >= 4.5:
        score += 7
        reasons.append(f"{rating}-star rating — premium business")
    elif rating >= 4.0:
        score += 5
        reasons.append(f"{rating}-star rating — solid reputation")
    elif rating >= 3.0:
        score += 3
    else:
        score += 1

    # Has phone = real business
    if lead.get("Phone"):
        score += 3

    return min(score, 20), reasons


def _score_revenue_potential(lead: dict) -> tuple[int, list[str]]:
    """
    Score 0-20: Is this a high-value industry?
    """
    score = 5  # base
    reasons = []
    name = str(lead.get("Name", "")).lower()
    address = str(lead.get("Address", "")).lower()
    website = str(lead.get("Website", "")).lower()
    combined = f"{name} {address} {website}"

    for keyword in HIGH_VALUE_INDUSTRIES:
        if keyword in combined:
            score = 18
            reasons.append(f"High-value industry detected: {keyword}")
            break

    if score < 10:
        for keyword in MEDIUM_VALUE_INDUSTRIES:
            if keyword in combined:
                score = 12
                reasons.append(f"Medium-value industry: {keyword}")
                break

    if not reasons:
        reasons.append("Standard industry")

    return min(score, 20), reasons


def _score_website_gap(lead: dict) -> tuple[int, list[str]]:
    """
    Score 0-15: How big is the opportunity gap?
    - No website = max gap
    - Has website but weak = medium gap
    - Strong website = small gap
    """
    score = 0
    reasons = []
    website = str(lead.get("Website", "")).strip()

    if not website:
        score = 15
        reasons.append("No website at all — maximum opportunity gap")
    else:
        domain = urlparse(website).netloc.lower()
        if any(p in domain for p in WEAK_SITE_PLATFORMS):
            score = 11
            reasons.append("Weak website platform — needs professional upgrade")
        else:
            score = 6
            reasons.append("Has website — content/SEO gap likely")

    return min(score, 15), reasons


def _score_engagement(lead: dict) -> tuple[int, list[str]]:
    """
    Score 0-10: Has this lead shown interest?
    Check interaction history from recipient_tracker.
    """
    score = 0
    reasons = []
    email = str(lead.get("Email", "")).lower().strip()

    if not email:
        return 0, ["No email — cannot track engagement"]

    # Check if they've replied or been contacted
    try:
        conn = _get_db()
        row = conn.execute(
            "SELECT * FROM recipient_tracker WHERE email = ?", (email,)
        ).fetchone()
        conn.close()

        if row:
            if row["reply_detected"]:
                score = 10
                reasons.append("Replied to previous email — hot lead")
            elif row["emails_sent"] > 0 and row["status"] == "active":
                score = 3
                reasons.append(f"Contacted {row['emails_sent']}x, no reply yet")
            elif row["status"] == "stopped":
                score = -5
                reasons.append("No response after max follow-ups")
            elif row["bounced"]:
                score = -10
                reasons.append("Previous email bounced")
        else:
            score = 5
            reasons.append("Fresh lead — never contacted")
    except Exception:
        score = 5
        reasons.append("Fresh lead")

    return max(min(score, 10), -10), reasons


def _score_risk(lead: dict) -> tuple[int, list[str]]:
    """
    Score 0-10 (NEGATIVE penalty): Risk factors that reduce score.
    """
    penalty = 0
    reasons = []
    email = str(lead.get("Email", "")).strip()

    if not email:
        penalty += 5
        reasons.append("No email — not directly reachable")

    if not lead.get("Phone") and not email:
        penalty += 5
        reasons.append("No contact info — unreachable")

    # Check for generic/risky email patterns
    if email:
        local = email.split("@")[0].lower()
        if local in ("info", "admin", "contact", "support", "noreply", "sales"):
            penalty += 3
            reasons.append(f"Role-based email ({local}@) — lower response rate")

    if not reasons:
        reasons.append("No risk factors detected")

    return min(penalty, 10), reasons


# ── Main Scoring Function ────────────────────────────────────────────

def smart_score(lead: dict) -> dict:
    """
    Score a lead 0-100 with detailed breakdown.

    Returns:
        {
            "score": int,
            "category": "High-Value" | "Medium" | "Low Priority",
            "reasons": [top 3 reasons],
            "action": str,
            "breakdown": {dimension: score},
            "all_reasons": [all reasons]
        }
    """
    weights = _get_weights()

    # Calculate each dimension
    intent_score, intent_reasons = _score_buying_intent(lead)
    maturity_score, maturity_reasons = _score_business_maturity(lead)
    revenue_score, revenue_reasons = _score_revenue_potential(lead)
    gap_score, gap_reasons = _score_website_gap(lead)
    engage_score, engage_reasons = _score_engagement(lead)
    risk_penalty, risk_reasons = _score_risk(lead)

    # Apply learned weights
    weighted_score = (
        intent_score * weights["buying_intent"] +
        maturity_score * weights["business_maturity"] +
        revenue_score * weights["revenue_potential"] +
        gap_score * weights["website_gap"] +
        engage_score * weights["engagement"] -
        risk_penalty
    )

    final_score = max(0, min(100, int(weighted_score)))

    # Category
    if final_score >= 80:
        category = "High-Value"
        action = "Prioritize immediately — send personalized message, notify Telegram"
    elif final_score >= 50:
        category = "Medium"
        action = "Standard personalized outreach, include in follow-up sequence"
    else:
        category = "Low Priority"
        action = "Delay or skip — lower sending priority"

    # Collect all reasons, pick top 3
    all_reasons = intent_reasons + maturity_reasons + revenue_reasons + gap_reasons + engage_reasons
    # Filter out generic ones
    strong_reasons = [r for r in all_reasons if "standard" not in r.lower() and "no risk" not in r.lower()]
    top_reasons = (strong_reasons or all_reasons)[:3]

    return {
        "score": final_score,
        "category": category,
        "reasons": top_reasons,
        "action": action,
        "breakdown": {
            "buying_intent": int(intent_score * weights["buying_intent"]),
            "business_maturity": int(maturity_score * weights["business_maturity"]),
            "revenue_potential": int(revenue_score * weights["revenue_potential"]),
            "website_gap": int(gap_score * weights["website_gap"]),
            "engagement": int(engage_score * weights["engagement"]),
            "risk_penalty": -risk_penalty,
        },
        "all_reasons": all_reasons + risk_reasons,
    }


def smart_score_leads(leads: list[dict]) -> list[dict]:
    """Score all leads and attach results."""
    for lead in leads:
        result = smart_score(lead)
        lead["Lead Score"] = result["score"]
        lead["Priority"] = result["category"]
        lead["Score Reasons"] = " | ".join(result["reasons"])
        lead["Outreach Type"] = _decide_outreach(lead, result)
    return leads


def _decide_outreach(lead: dict, score_result: dict) -> str:
    """Decide outreach type based on smart score."""
    contacted = str(lead.get("Contacted", "")).strip().lower()
    if contacted == "yes":
        return "Skip"

    has_email = bool(lead.get("Email"))
    has_phone = bool(lead.get("Phone"))
    score = score_result["score"]

    if has_email and score >= 50:
        return "Email"
    elif not has_email and has_phone and score >= 40:
        return "Call Queue"
    return "Skip"


# ── Learning Loop ────────────────────────────────────────────────────

def record_outcome(email: str, outcome: str, original_score: int = 0, factors: dict = None):
    """
    Record an outcome for a lead to improve future scoring.

    outcome: "replied" | "converted" | "ignored" | "bounced" | "unsubscribed"
    """
    with _lock:
        conn = _get_db()
        conn.execute(
            "INSERT INTO scoring_outcomes (email, original_score, outcome, timestamp, factors) "
            "VALUES (?, ?, ?, ?, ?)",
            (email.lower(), original_score, outcome, datetime.now().isoformat(),
             json.dumps(factors or {}))
        )
        conn.commit()
        conn.close()


def run_learning_cycle():
    """
    Analyze outcomes and adjust scoring weights.
    Run daily to improve accuracy.

    Logic:
    - If high-scored leads reply more → weights are good
    - If low-scored leads reply → boost the dimensions where they scored high
    - If high-scored leads never reply → reduce the dimensions where they scored high
    """
    conn = _get_db()
    outcomes = conn.execute(
        "SELECT outcome, original_score, factors FROM scoring_outcomes"
    ).fetchall()
    conn.close()

    if len(outcomes) < 10:
        return {"status": "not_enough_data", "count": len(outcomes)}

    # Calculate reply rate by score range
    high_outcomes = [o for o in outcomes if o["original_score"] >= 80]
    mid_outcomes = [o for o in outcomes if 50 <= o["original_score"] < 80]

    high_reply_rate = (
        sum(1 for o in high_outcomes if o["outcome"] in ("replied", "converted")) / max(len(high_outcomes), 1)
    )
    mid_reply_rate = (
        sum(1 for o in mid_outcomes if o["outcome"] in ("replied", "converted")) / max(len(mid_outcomes), 1)
    )

    # Adjust weights — if high-scored leads aren't converting, reduce weight slightly
    weights = _get_weights()

    if high_reply_rate < 0.05 and len(high_outcomes) >= 5:
        # High-scored leads not converting — reduce buying_intent weight
        weights["buying_intent"] = max(0.7, weights["buying_intent"] - 0.05)
        weights["engagement"] = min(1.5, weights["engagement"] + 0.05)

    if mid_reply_rate > high_reply_rate and len(mid_outcomes) >= 5:
        # Medium leads converting better — boost maturity and revenue
        weights["business_maturity"] = min(1.5, weights["business_maturity"] + 0.05)
        weights["revenue_potential"] = min(1.5, weights["revenue_potential"] + 0.05)

    # Save updated weights
    with _lock:
        conn = _get_db()
        conn.execute(
            "UPDATE scoring_weights SET buying_intent=?, business_maturity=?, "
            "revenue_potential=?, website_gap=?, engagement=?, updated_at=? WHERE id=1",
            (weights["buying_intent"], weights["business_maturity"],
             weights["revenue_potential"], weights["website_gap"],
             weights["engagement"], datetime.now().isoformat())
        )
        conn.commit()
        conn.close()

    return {
        "status": "updated",
        "high_reply_rate": round(high_reply_rate * 100, 1),
        "mid_reply_rate": round(mid_reply_rate * 100, 1),
        "weights": weights,
    }
