"""
Revenue Optimization Engine — Central AI decision maker.

Controls: lead prioritization, offer strategy, outreach intensity,
resource allocation, and continuous learning.

This module doesn't send emails — it DECIDES what to do.
Other modules execute its decisions.
"""

import os
import sys
import json
import sqlite3
from datetime import datetime, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "approvals.db")
METRICS_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "metrics.db")


def _get_db(path=DB_PATH):
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


# ── Database ─────────────────────────────────────────────────────────

def _init_revenue_db():
    conn = _get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS conversions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            industry TEXT DEFAULT '',
            service TEXT DEFAULT '',
            offer TEXT DEFAULT '',
            lead_score INTEGER DEFAULT 0,
            converted INTEGER DEFAULT 0,
            revenue REAL DEFAULT 0,
            timestamp TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS strategy_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            decision TEXT NOT NULL,
            reasoning TEXT DEFAULT '',
            metrics TEXT DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS industry_performance (
            industry TEXT PRIMARY KEY,
            leads_contacted INTEGER DEFAULT 0,
            replies INTEGER DEFAULT 0,
            conversions INTEGER DEFAULT 0,
            total_revenue REAL DEFAULT 0,
            avg_score REAL DEFAULT 0,
            reply_rate REAL DEFAULT 0,
            conversion_rate REAL DEFAULT 0,
            updated_at TEXT DEFAULT ''
        );
    """)
    conn.close()


_init_revenue_db()


# ── Industry Detection ───────────────────────────────────────────────

INDUSTRY_MAP = {
    "legal": ["lawyer", "attorney", "legal", "law firm", " law"],
    "dental": ["dentist", "dental", "orthodont"],
    "medical": ["doctor", "medical", "clinic", "healthcare", "physician", "surgery"],
    "real_estate": ["real estate", "realtor", "property", "realty"],
    "home_services": ["plumber", "plumbing", "hvac", "roofing", "contractor", "electric"],
    "auto": ["auto repair", "mechanic", "car dealer", "auto body", "tire"],
    "beauty": ["salon", "spa", "medspa", "cosmetic", "barber", "hair"],
    "restaurant": ["restaurant", "catering", "grill", "bistro", "diner"],
    "cafe": ["cafe", "coffee", "bakery", "tea"],
    "fitness": ["gym", "fitness", "yoga", "crossfit", "personal trainer"],
    "finance": ["insurance", "financial", "accounting", "cpa", "tax"],
    "pet": ["veterinar", "pet", "animal", "grooming"],
    "education": ["tutoring", "school", "academy", "learning"],
    "hotel": ["hotel", "resort", "motel", "inn", "lodge"],
}

# Revenue tier per industry (estimated deal size for our services)
INDUSTRY_REVENUE_TIER = {
    "legal": 3, "dental": 3, "medical": 3, "real_estate": 3,
    "finance": 3, "home_services": 2, "auto": 2, "beauty": 2,
    "hotel": 2, "restaurant": 2, "pet": 2, "fitness": 1,
    "cafe": 1, "education": 1,
}


def detect_industry(lead: dict) -> str:
    """Detect industry from lead data."""
    name = str(lead.get("Name", "")).lower()
    address = str(lead.get("Address", "")).lower()
    website = str(lead.get("Website", "")).lower()
    combined = f"{name} {address} {website}"

    for industry, keywords in INDUSTRY_MAP.items():
        for kw in keywords:
            if kw in combined:
                return industry
    return "other"


# ── Offer Strategy ───────────────────────────────────────────────────

def determine_offer(lead: dict) -> dict:
    """
    Decide what service to offer based on lead data.

    Returns:
        {
            "primary_offer": str,
            "secondary_offer": str or None,
            "pitch_angle": str,
            "estimated_value": str,
            "reasoning": str
        }
    """
    website = str(lead.get("Website", "")).strip()
    has_website = bool(website)

    try:
        rating = float(lead.get("Rating", 0) or 0)
    except (ValueError, TypeError):
        rating = 0

    industry = detect_industry(lead)
    tier = INDUSTRY_REVENUE_TIER.get(industry, 1)

    # Weak platform detection
    weak_platforms = ["wix", "weebly", "squarespace", "godaddy", "wordpress.com",
                      "sites.google", "blogspot", "facebook.com", "yelp.com"]
    is_weak_site = has_website and any(p in website.lower() for p in weak_platforms)

    if not has_website:
        # No website — primary target for web design
        primary = "WordPress Website Design"
        secondary = "Google Business Optimization" if rating > 0 else None
        angle = "visibility"
        value = "$800-2,500" if tier >= 2 else "$500-1,200"
        reasoning = "No website — maximum opportunity. Business is invisible online."

    elif is_weak_site:
        # Weak website — offer redesign + SEO combo
        primary = "Website Redesign + SEO Package"
        secondary = "Content Automation (n8n blog)"
        angle = "upgrade"
        value = "$1,500-4,000" if tier >= 2 else "$800-2,000"
        reasoning = f"Website on weak platform — needs professional upgrade. SEO upside is high."

    elif rating > 0 and rating < 4.0:
        # Has website but struggling — SEO improvement
        primary = "Local SEO Optimization"
        secondary = "Content Automation (n8n blog)"
        angle = "improvement"
        value = "$600-2,000" if tier >= 2 else "$400-1,000"
        reasoning = f"Rating {rating} indicates room for improvement. SEO can drive more positive reviews."

    else:
        # Has website, doing well — growth/scaling
        primary = "SEO + Content Automation"
        secondary = "Local + Global SEO Package"
        angle = "growth"
        value = "$1,000-3,000" if tier >= 2 else "$500-1,500"
        reasoning = "Strong business with room to scale. Content automation offers recurring value."

    return {
        "primary_offer": primary,
        "secondary_offer": secondary,
        "pitch_angle": angle,
        "estimated_value": value,
        "industry": industry,
        "revenue_tier": tier,
        "reasoning": reasoning,
    }


# ── Outreach Strategy ────────────────────────────────────────────────

def determine_outreach_strategy(lead: dict, score: int) -> dict:
    """
    Decide outreach intensity and tone based on lead quality.

    Returns:
        {
            "tone": str,
            "follow_up_intensity": str,
            "personalization_depth": str,
            "priority_level": int (1-5),
            "send_immediately": bool,
            "reasoning": str
        }
    """
    industry = detect_industry(lead)
    tier = INDUSTRY_REVENUE_TIER.get(industry, 1)
    offer = determine_offer(lead)

    # Check past performance for this industry
    perf = get_industry_performance(industry)
    industry_reply_rate = perf.get("reply_rate", 0) if perf else 0

    if score >= 80:
        return {
            "tone": "direct_confident",
            "follow_up_intensity": "high",
            "personalization_depth": "deep",
            "priority_level": 1,
            "send_immediately": True,
            "reasoning": f"High-value lead (score {score}). Direct, confident tone. "
                         f"Deep personalization with specific business references.",
        }
    elif score >= 65 and tier >= 2:
        return {
            "tone": "professional_helpful",
            "follow_up_intensity": "high",
            "personalization_depth": "deep",
            "priority_level": 2,
            "send_immediately": True,
            "reasoning": f"High-value industry ({industry}) with good score ({score}). "
                         f"Worth premium effort.",
        }
    elif score >= 50:
        return {
            "tone": "friendly_casual",
            "follow_up_intensity": "standard",
            "personalization_depth": "moderate",
            "priority_level": 3,
            "send_immediately": False,
            "reasoning": f"Medium-value lead (score {score}). Standard outreach. "
                         f"Follow up if no response.",
        }
    elif score >= 35:
        return {
            "tone": "soft_exploratory",
            "follow_up_intensity": "low",
            "personalization_depth": "basic",
            "priority_level": 4,
            "send_immediately": False,
            "reasoning": f"Lower-value lead (score {score}). Minimal effort. "
                         f"One follow-up max.",
        }
    else:
        return {
            "tone": "skip",
            "follow_up_intensity": "none",
            "personalization_depth": "none",
            "priority_level": 5,
            "send_immediately": False,
            "reasoning": f"Low-value lead (score {score}). Skip to save resources.",
        }


# ── Full Lead Analysis ───────────────────────────────────────────────

def analyze_lead(lead: dict) -> dict:
    """
    Complete revenue-optimized analysis of a lead.

    Returns everything other modules need to take action.
    """
    from src.smart_scorer import smart_score

    score_result = smart_score(lead)
    offer = determine_offer(lead)
    strategy = determine_outreach_strategy(lead, score_result["score"])

    # Estimate conversion probability
    industry = detect_industry(lead)
    perf = get_industry_performance(industry)
    base_prob = 0.02  # 2% base conversion rate for cold outreach

    # Adjust based on signals
    prob = base_prob
    if score_result["score"] >= 80:
        prob *= 3
    elif score_result["score"] >= 60:
        prob *= 2
    if not lead.get("Website"):
        prob *= 1.5  # no website = higher need
    if perf and perf.get("conversion_rate", 0) > 0:
        prob = max(prob, perf["conversion_rate"] * 0.8)

    prob = min(prob, 0.25)  # cap at 25%

    return {
        "lead_name": lead.get("Name", "Unknown"),
        "score": score_result["score"],
        "category": score_result["category"],
        "score_breakdown": score_result["breakdown"],
        "score_reasons": score_result["reasons"],
        "offer": offer,
        "strategy": strategy,
        "industry": industry,
        "conversion_probability": round(prob * 100, 1),
        "expected_value": offer["estimated_value"],
        "priority_rank": strategy["priority_level"],
        "action": "send" if strategy["priority_level"] <= 3 else "skip",
    }


# ── Resource Allocation ──────────────────────────────────────────────

def allocate_daily_budget(leads: list[dict]) -> dict:
    """
    Decide how to allocate today's email budget across lead segments.

    Returns allocation plan.
    """
    cap = config.MAX_EMAILS_PER_DAY
    if cap <= 0:
        return {"plan": [], "total": 0, "reason": "System paused"}

    analyses = []
    for lead in leads:
        analysis = analyze_lead(lead)
        if analysis["action"] == "send" and lead.get("Email"):
            analyses.append(analysis)

    # Sort by priority (lower number = higher priority)
    analyses.sort(key=lambda x: (x["priority_rank"], -x["score"]))

    # Allocate budget
    plan = []
    allocated = 0
    for a in analyses:
        if allocated >= cap:
            break
        plan.append({
            "name": a["lead_name"],
            "score": a["score"],
            "offer": a["offer"]["primary_offer"],
            "priority": a["priority_rank"],
            "conversion_prob": a["conversion_probability"],
        })
        allocated += 1

    # Segment breakdown
    high_value = sum(1 for p in plan if p["priority"] <= 2)
    medium = sum(1 for p in plan if p["priority"] == 3)

    return {
        "plan": plan,
        "total": allocated,
        "budget": cap,
        "high_value_leads": high_value,
        "medium_leads": medium,
        "reason": f"Allocated {allocated}/{cap} daily budget. "
                  f"{high_value} high-value, {medium} medium.",
    }


# ── Industry Performance Tracking ────────────────────────────────────

def record_conversion(email: str, industry: str, service: str, revenue: float = 0):
    """Record a successful conversion."""
    conn = _get_db()
    conn.execute(
        "INSERT INTO conversions (email, industry, service, revenue, converted, timestamp) "
        "VALUES (?, ?, ?, ?, 1, ?)",
        (email, industry, service, revenue, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()
    _refresh_industry_stats()


def record_contact(email: str, industry: str, replied: bool = False):
    """Record a contact attempt for industry stats."""
    conn = _get_db()
    row = conn.execute(
        "SELECT * FROM industry_performance WHERE industry = ?", (industry,)
    ).fetchone()

    if row:
        new_contacts = row["leads_contacted"] + 1
        new_replies = row["replies"] + (1 if replied else 0)
        reply_rate = new_replies / max(new_contacts, 1)
        conn.execute(
            "UPDATE industry_performance SET leads_contacted=?, replies=?, reply_rate=?, updated_at=? WHERE industry=?",
            (new_contacts, new_replies, reply_rate, datetime.now().isoformat(), industry)
        )
    else:
        conn.execute(
            "INSERT INTO industry_performance (industry, leads_contacted, replies, reply_rate, updated_at) "
            "VALUES (?, 1, ?, ?, ?)",
            (industry, 1 if replied else 0, 1.0 if replied else 0.0, datetime.now().isoformat())
        )

    conn.commit()
    conn.close()


def get_industry_performance(industry: str = "") -> dict:
    """Get performance stats for an industry."""
    conn = _get_db()
    if industry:
        row = conn.execute(
            "SELECT * FROM industry_performance WHERE industry = ?", (industry,)
        ).fetchone()
        conn.close()
        return dict(row) if row else {}
    else:
        rows = conn.execute(
            "SELECT * FROM industry_performance ORDER BY reply_rate DESC"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]


def _refresh_industry_stats():
    """Recalculate all industry stats from conversions table."""
    conn = _get_db()
    industries = conn.execute(
        "SELECT DISTINCT industry FROM conversions WHERE industry != ''"
    ).fetchall()
    for row in industries:
        ind = row["industry"]
        total = conn.execute(
            "SELECT COUNT(*) as cnt FROM conversions WHERE industry = ?", (ind,)
        ).fetchone()["cnt"]
        converted = conn.execute(
            "SELECT COUNT(*) as cnt FROM conversions WHERE industry = ? AND converted = 1", (ind,)
        ).fetchone()["cnt"]
        revenue = conn.execute(
            "SELECT SUM(revenue) as total FROM conversions WHERE industry = ? AND converted = 1", (ind,)
        ).fetchone()["total"] or 0

        rate = converted / max(total, 1)
        conn.execute(
            "UPDATE industry_performance SET conversions=?, total_revenue=?, conversion_rate=?, updated_at=? WHERE industry=?",
            (converted, revenue, rate, datetime.now().isoformat(), ind)
        )
    conn.commit()
    conn.close()


# ── Strategic Recommendations ────────────────────────────────────────

def get_strategic_recommendations() -> list[dict]:
    """Generate strategic recommendations based on all available data."""
    recs = []

    # Analyze industry performance
    perf = get_industry_performance()
    if isinstance(perf, list) and perf:
        best = perf[0]
        if best["reply_rate"] > 0.05:
            recs.append({
                "type": "focus",
                "priority": "high",
                "recommendation": f"Focus on {best['industry']} industry — "
                                  f"{best['reply_rate']*100:.1f}% reply rate "
                                  f"({best['leads_contacted']} contacted, {best['replies']} replies)",
                "action": f"Increase {best['industry']} lead volume",
            })

        # Find underperformers
        for p in perf:
            if p["leads_contacted"] >= 10 and p["reply_rate"] < 0.01:
                recs.append({
                    "type": "reduce",
                    "priority": "medium",
                    "recommendation": f"Reduce {p['industry']} outreach — "
                                      f"{p['reply_rate']*100:.1f}% reply rate from {p['leads_contacted']} contacts",
                    "action": f"Deprioritize {p['industry']} leads",
                })

    # Check overall metrics
    try:
        conn = _get_db(METRICS_DB)
        week_ago = (datetime.now() - timedelta(days=7)).isoformat()
        sent = conn.execute(
            "SELECT COUNT(*) as cnt FROM email_events WHERE event_type='sent' AND timestamp > ?",
            (week_ago,)
        ).fetchone()["cnt"]
        conn.close()

        if sent < 20:
            recs.append({
                "type": "scale",
                "priority": "medium",
                "recommendation": f"Only {sent} emails sent this week. "
                                  f"Consider increasing daily cap if deliverability is stable.",
                "action": "Increase MAX_EMAILS_PER_DAY",
            })
        elif sent > 200:
            recs.append({
                "type": "optimize",
                "priority": "low",
                "recommendation": f"{sent} emails sent this week. Focus on quality over quantity.",
                "action": "Review conversion rates before scaling further",
            })
    except Exception:
        pass

    # Service mix recommendation
    try:
        conn = _get_db()
        web_design = conn.execute(
            "SELECT COUNT(*) as cnt FROM conversions WHERE service LIKE '%website%' AND converted=1"
        ).fetchone()["cnt"]
        seo = conn.execute(
            "SELECT COUNT(*) as cnt FROM conversions WHERE service LIKE '%seo%' AND converted=1"
        ).fetchone()["cnt"]
        conn.close()

        if web_design > seo * 2 and seo > 0:
            recs.append({
                "type": "shift",
                "priority": "medium",
                "recommendation": f"Web design converts {web_design}x vs SEO {seo}x. "
                                  f"Shift focus toward businesses without websites.",
                "action": "Target more no-website leads",
            })
        elif seo > web_design * 2 and web_design > 0:
            recs.append({
                "type": "shift",
                "priority": "medium",
                "recommendation": f"SEO converts {seo}x vs Web Design {web_design}x. "
                                  f"Focus on businesses with poor websites.",
                "action": "Target more weak-website leads",
            })
    except Exception:
        pass

    if not recs:
        recs.append({
            "type": "info",
            "priority": "low",
            "recommendation": "Not enough data for strategic recommendations yet. "
                              "Keep sending and tracking outcomes.",
            "action": "Continue current strategy",
        })

    return recs
