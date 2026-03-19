"""
Step 5 — Lead Scoring & Prioritization Engine.

Scores leads 0-100 based on data quality, assigns priority tiers,
and decides outreach routing (Email / Call Queue / Skip).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


def calculate_score(lead: dict) -> int:
    """
    Calculate a lead quality score (0-100).

    Primary target: businesses WITHOUT a website (WordPress creation).
    Secondary target: businesses WITH a website (SEO + content automation).

    Scoring breakdown:
      - Has email           → +30  (reachable)
      - NO website          → +25  (hot lead — needs a website)
      - Has website         → +10  (secondary — SEO target)
      - Rating >= 4.0       → +15  (established business)
      - Reviews 0-10        → +5
      - Reviews 11-50       → +10
      - Reviews 50+         → +15
    """
    score = 0

    # Email presence (+30) — can we reach them?
    if lead.get("Email"):
        score += config.SCORE_EMAIL

    # Website: no website = primary target, has website = secondary
    if not lead.get("Website"):
        score += config.SCORE_NO_WEBSITE
    else:
        score += config.SCORE_HAS_WEBSITE

    # Rating quality (+15)
    try:
        rating = float(lead.get("Rating", 0) or 0)
        if rating >= 4.0:
            score += config.SCORE_HIGH_RATING
    except (ValueError, TypeError):
        pass

    # Review count (tiered)
    try:
        reviews = int(str(lead.get("Reviews", 0) or 0).replace(",", ""))
        if reviews > 50:
            score += config.SCORE_REVIEWS_HIGH
        elif reviews > 10:
            score += config.SCORE_REVIEWS_MED
        elif reviews > 0:
            score += config.SCORE_REVIEWS_LOW
    except (ValueError, TypeError):
        pass

    return min(score, 100)


def assign_priority(score: int) -> str:
    """Assign priority tier based on score."""
    if score >= config.HIGH_THRESHOLD:
        return "High"
    elif score >= config.MEDIUM_THRESHOLD:
        return "Medium"
    return "Low"


def decide_outreach(lead: dict, score: int) -> str:
    """
    Decide outreach type for a lead.

    - Has email + score >= 60 + not contacted → Email
    - No email + score >= 70 → Call Queue
    - Otherwise → Skip
    """
    contacted = str(lead.get("Contacted", "")).strip().lower()
    if contacted == "yes":
        return "Skip"

    has_email = bool(lead.get("Email"))
    has_phone = bool(lead.get("Phone"))

    if has_email and score >= config.MIN_OUTREACH_SCORE:
        return "Email"
    elif not has_email and has_phone and score >= config.MIN_CALL_SCORE:
        return "Call Queue"

    return "Skip"


def score_all_leads(leads: list[dict]) -> list[dict]:
    """Score a list of leads, adding Lead Score, Priority, and Outreach Type."""
    for lead in leads:
        score = calculate_score(lead)
        lead["Lead Score"] = score
        lead["Priority"] = assign_priority(score)
        lead["Outreach Type"] = decide_outreach(lead, score)
    return leads


def update_sheet_scores(sheets_mgr) -> dict:
    """
    Read all leads from Google Sheets, score them, update the sheet.
    Returns summary stats.
    """
    print(f"\n{'='*50}")
    print(f"  Lead Scoring")
    print(f"{'='*50}\n")

    leads = sheets_mgr.read_leads()
    if not leads:
        print("  [!] No leads in sheet.")
        return {"high": 0, "medium": 0, "low": 0}

    # Score each lead
    scores = []
    priorities = []
    outreach_types = []

    for lead in leads:
        score = calculate_score(lead)
        priority = assign_priority(score)
        outreach = decide_outreach(lead, score)

        scores.append(str(score))
        priorities.append(priority)
        outreach_types.append(outreach)

    # Batch update columns
    sheets_mgr.batch_update_column("Lead Score", scores)
    sheets_mgr.batch_update_column("Priority", priorities)
    sheets_mgr.batch_update_column("Outreach Type", outreach_types)

    # Sort by score
    sheets_mgr.sort_by_score()

    # Stats
    high = priorities.count("High")
    med = priorities.count("Medium")
    low = priorities.count("Low")
    email_count = outreach_types.count("Email")
    call_count = outreach_types.count("Call Queue")
    skip_count = outreach_types.count("Skip")

    print(f"  Scored {len(leads)} leads")
    print(f"  Priority : High={high} | Medium={med} | Low={low}")
    print(f"  Outreach : Email={email_count} | Call Queue={call_count} | Skip={skip_count}")

    return {"high": high, "medium": med, "low": low, "email": email_count, "call": call_count}


# ── Standalone test ──────────────────────────────────────────────────
if __name__ == "__main__":
    # Test with sample data
    test_leads = [
        {"Name": "Best Cafe", "Email": "hello@bestcafe.com", "Website": "",
         "Rating": "4.5", "Reviews": "120", "Phone": "555-1234", "Contacted": "No"},
        {"Name": "OK Diner", "Email": "ok@diner.com", "Website": "https://okdiner.com",
         "Rating": "3.2", "Reviews": "8", "Phone": "555-5678", "Contacted": "No"},
        {"Name": "New Place", "Email": "", "Website": "",
         "Rating": "4.8", "Reviews": "3", "Phone": "555-0000", "Contacted": "No"},
        {"Name": "Big Chain", "Email": "info@bigchain.com", "Website": "https://bigchain.com",
         "Rating": "4.7", "Reviews": "500", "Phone": "555-9999", "Contacted": "No"},
    ]

    scored = score_all_leads(test_leads)
    print("\nSample scoring results:")
    print(f"{'Name':<20} {'Score':<8} {'Priority':<10} {'Outreach':<12}")
    print("-" * 50)
    for lead in scored:
        print(f"{lead['Name']:<20} {lead['Lead Score']:<8} {lead['Priority']:<10} {lead['Outreach Type']:<12}")
