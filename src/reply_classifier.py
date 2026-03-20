"""
Reply Intent Classifier — Classifies incoming replies by intent.

Categories:
  - interested    → Lead wants to talk (schedule call, learn more)
  - not_interested → Polite decline or unsubscribe
  - question      → Asking for more info before deciding
  - out_of_office → Auto-reply, OOO
  - bounce        → Delivery failure notification

Uses keyword matching (no external AI API needed, zero cost).
"""

import re

# ── Keyword patterns per intent ──────────────────────────────────────

INTERESTED_PATTERNS = [
    r"\byes\b", r"\byeah\b", r"\byep\b", r"\bsure\b", r"\bsounds good\b",
    r"\binterested\b", r"\btell me more\b", r"\blet'?s (talk|chat|connect|discuss)\b",
    r"\bset up a (call|meeting|time)\b", r"\bschedule\b", r"\bsend (me|over)\b",
    r"\bi'?d (love|like) to\b", r"\bshow me\b", r"\bwhat (would|does) it cost\b",
    r"\bhow much\b", r"\bpric(e|ing)\b", r"\bquote\b", r"\bportfolio\b",
    r"\bexamples?\b", r"\bgo ahead\b", r"\blet'?s do it\b",
    # User-specified interest signals
    r"\bdetails?\b", r"\bdetails please\b", r"\bhow can you help\b",
    r"\bprice\?", r"\bwhat'?s (the )?cost\b", r"\bwhat do you charge\b",
    r"\btell me\b", r"\bmore info\b", r"\bmore information\b",
    r"\bwould love\b", r"\bsounds interesting\b", r"\bopen to\b",
]

NOT_INTERESTED_PATTERNS = [
    r"\bno thanks?\b", r"\bnot interested\b", r"\bstop\b", r"\bunsubscribe\b",
    r"\bremove me\b", r"\bdon'?t (contact|email|message)\b", r"\bleave me alone\b",
    r"\bnot (right now|at this time|for us)\b", r"\bwe'?re (good|fine|all set)\b",
    r"\balready have\b", r"\bno need\b", r"\bpass\b", r"\bdecline\b",
    r"\bnot looking\b", r"\bplease don'?t\b",
]

QUESTION_PATTERNS = [
    r"\bhow (does|do|would|can)\b", r"\bwhat (is|are|do|does|would)\b",
    r"\bcan you (explain|tell|share|send)\b", r"\bmore (info|details|information)\b",
    r"\bwhat'?s (your|the)\b", r"\bcurious\b", r"\bquestion\b",
    r"\bhow long\b", r"\bwhat (kind|type)\b", r"\bdo you (offer|provide|have)\b",
    r"\btimeline\b", r"\bguarantee\b",
]

OOO_PATTERNS = [
    r"\bout of (the )?office\b", r"\b(on |on my )vacation\b", r"\baway from\b",
    r"\bauto(-| )?reply\b", r"\bautomatic(ally)? (response|reply)\b",
    r"\blimited access\b", r"\breturn(ing)? on\b", r"\bback (on|in)\b",
    r"\bcurrently (out|away|unavailable)\b",
]

BOUNCE_PATTERNS = [
    r"\bundeliverable\b", r"\bdelivery (failed|failure|status)\b",
    r"\bmailer-daemon\b", r"\bpostmaster\b", r"\b550\b", r"\b554\b",
    r"\bmailbox (full|not found|unavailable)\b", r"\buser (unknown|not found)\b",
    r"\bpermanent(ly)? (failure|failed|error)\b", r"\bdoes not exist\b",
    r"\brejected\b", r"\bbounce\b",
]


def classify_reply(text: str, sender: str = "") -> dict:
    """
    Classify a reply email by intent.

    Returns:
        {
            "intent": "interested" | "not_interested" | "question" | "out_of_office" | "bounce" | "unclear",
            "confidence": float (0-1),
            "action": "send_conversion" | "stop_followups" | "mark_bounced" | "wait" | "ask_telegram"
        }
    """
    text_lower = text.lower().strip()
    sender_lower = sender.lower()

    # Check bounce first (often from system addresses)
    if _match_patterns(text_lower, BOUNCE_PATTERNS) or "mailer-daemon" in sender_lower:
        return {
            "intent": "bounce",
            "confidence": 0.95,
            "action": "mark_bounced",
            "reason": "Delivery failure detected"
        }

    # Check OOO
    if _match_patterns(text_lower, OOO_PATTERNS):
        return {
            "intent": "out_of_office",
            "confidence": 0.90,
            "action": "wait",
            "reason": "Auto-reply / out of office"
        }

    # Score each intent
    interested_score = _score_patterns(text_lower, INTERESTED_PATTERNS)
    not_interested_score = _score_patterns(text_lower, NOT_INTERESTED_PATTERNS)
    question_score = _score_patterns(text_lower, QUESTION_PATTERNS)

    # Determine winner
    scores = {
        "interested": interested_score,
        "not_interested": not_interested_score,
        "question": question_score,
    }
    best = max(scores, key=scores.get)
    best_score = scores[best]

    if best_score == 0:
        # No clear patterns — intent unknown, send to Telegram for manual decision
        return {
            "intent": "unclear",
            "confidence": 0.30,
            "action": "ask_telegram",
            "reason": "Reply received but intent unclear — forwarding to Telegram for review"
        }

    # Normalize confidence
    total = sum(scores.values()) or 1
    confidence = round(best_score / total, 2)

    actions = {
        "interested": "send_conversion",
        "not_interested": "stop_followups",
        "question": "send_conversion",
    }

    # Price/cost questions are strong interest signals
    if question_score > 0 and interested_score > 0:
        best = "interested"
        confidence = 0.85

    reasons = {
        "interested": "Positive signals detected — sending conversion email",
        "not_interested": "Decline or unsubscribe request — stopping outreach",
        "question": "Asking questions — engaged lead, sending conversion email",
    }

    return {
        "intent": best,
        "confidence": confidence,
        "action": actions[best],
        "reason": reasons[best],
    }


def _match_patterns(text: str, patterns: list) -> bool:
    """Check if any pattern matches."""
    for pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def _score_patterns(text: str, patterns: list) -> int:
    """Count how many patterns match."""
    score = 0
    for pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            score += 1
    return score
