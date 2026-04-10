"""
Website audit for lead qualification.
Checks: load speed, chatbot presence, mobile-friendliness, missing features.
Generates a quality score and personalization data.
"""

import time
import re
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
}

# Chatbot / live chat indicators
CHATBOT_INDICATORS = [
    'tawk.to', 'tidio', 'intercom', 'drift', 'crisp.chat', 'livechat',
    'zendesk', 'freshdesk', 'hubspot', 'chatwoot', 'olark', 'smartsupp',
    'livechatinc', 'comm100', 'purechat', 'chatra', 'jivochat',
    'chat-widget', 'chatbot', 'live-chat', 'messenger-widget',
    'fb-customerchat', 'whatsapp-widget',
]

# Automation / CRM indicators
AUTOMATION_INDICATORS = [
    'mailchimp', 'klaviyo', 'activecampaign', 'convertkit', 'drip',
    'autopilot', 'hubspot', 'pardot', 'marketo', 'sendinblue',
]


def audit_website(url, timeout=10):
    """
    Audit a business website. Returns dict with:
    - load_time: seconds to load
    - has_chatbot: bool
    - has_automation: bool
    - has_ssl: bool
    - title: page title
    - description: meta description
    - niche_keywords: extracted keywords
    - issues: list of problems found
    - score: 0-100 quality score (lower = better lead)
    - personal_line: auto-generated personalization line
    """
    if not url:
        return {"score": 0, "issues": ["no_website"], "personal_line": "Noticed you don't have a website yet."}

    if not url.startswith("http"):
        url = "https://" + url

    result = {
        "url": url,
        "load_time": None,
        "has_chatbot": False,
        "has_automation": False,
        "has_ssl": url.startswith("https"),
        "title": "",
        "description": "",
        "niche_keywords": [],
        "issues": [],
        "score": 50,
        "personal_line": "",
    }

    try:
        start = time.time()
        resp = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        result["load_time"] = round(time.time() - start, 2)

        if resp.status_code != 200:
            result["issues"].append("website_down")
            result["score"] = 10
            result["personal_line"] = "Noticed your website seems to be having issues."
            return result

        html = resp.text.lower()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Extract title
        title_tag = soup.find("title")
        if title_tag and title_tag.string:
            result["title"] = title_tag.string.strip()

        # Extract meta description
        desc_tag = soup.find("meta", attrs={"name": "description"})
        if desc_tag and desc_tag.get("content"):
            result["description"] = desc_tag["content"].strip()

        # Check for chatbot
        for indicator in CHATBOT_INDICATORS:
            if indicator in html:
                result["has_chatbot"] = True
                break

        # Check for automation/CRM
        for indicator in AUTOMATION_INDICATORS:
            if indicator in html:
                result["has_automation"] = True
                break

        # Check SSL
        result["has_ssl"] = resp.url.startswith("https")

        # Extract niche keywords from title + description
        text = f"{result['title']} {result['description']}"
        result["niche_keywords"] = _extract_keywords(text)

        # Calculate score (lower = better lead for us)
        result["score"] = _calculate_score(result)

        # Generate personalization line
        result["personal_line"] = _generate_personal_line(result)

    except requests.exceptions.SSLError:
        result["issues"].append("ssl_error")
        result["has_ssl"] = False
        result["score"] = 15
        result["personal_line"] = "Noticed your website has an SSL security issue."
    except requests.exceptions.Timeout:
        result["issues"].append("slow_website")
        result["score"] = 20
        result["personal_line"] = "Noticed your website is loading quite slowly."
    except Exception:
        result["issues"].append("website_error")
        result["score"] = 25
        result["personal_line"] = "Took a look at your online presence."

    return result


def _extract_keywords(text):
    """Pull niche-relevant keywords from text."""
    text = text.lower()
    keywords = []
    niche_words = [
        'restaurant', 'dental', 'law', 'legal', 'attorney', 'real estate',
        'marketing', 'agency', 'consulting', 'fitness', 'gym', 'salon',
        'spa', 'clinic', 'medical', 'plumbing', 'hvac', 'roofing',
        'construction', 'photography', 'design', 'accounting', 'insurance',
        'automotive', 'repair', 'cleaning', 'landscaping', 'catering',
        'ecommerce', 'shopify', 'retail', 'wholesale',
    ]
    for word in niche_words:
        if word in text:
            keywords.append(word)
    return keywords


def _calculate_score(result):
    """
    Score 0-100. LOWER = better lead for outreach.
    Businesses with poor web presence = easiest to sell to.
    """
    score = 50  # baseline

    # No chatbot = great opportunity (+20 points = worse site = better lead)
    if not result["has_chatbot"]:
        score -= 20

    # No automation = they need help
    if not result["has_automation"]:
        score -= 15

    # Slow site = needs improvement
    if result["load_time"] and result["load_time"] > 3:
        score -= 10

    # No SSL = outdated
    if not result["has_ssl"]:
        score -= 10

    # Issues found
    score -= len(result["issues"]) * 5

    return max(0, min(100, score))


def _generate_personal_line(result):
    """Auto-generate a personalization line based on audit findings."""
    issues = result["issues"]
    title = result["title"]
    keywords = result["niche_keywords"]

    # Build a specific, reference-based line
    niche = keywords[0] if keywords else "business"

    if not result["has_chatbot"] and not result["has_automation"]:
        return f"Checked out your {niche} site — noticed no automated reply system in place."

    if not result["has_chatbot"]:
        return f"Saw your {niche} site — looks like customer queries are handled manually right now."

    if result["load_time"] and result["load_time"] > 3:
        return f"Visited your {niche} site — noticed it's loading a bit slow which could be costing you leads."

    if not result["has_ssl"]:
        return f"Checked your {niche} site — noticed it's missing SSL which can hurt trust with visitors."

    if title:
        short_title = title[:40]
        return f"Saw {short_title} — impressive {niche} presence."

    return f"Checked out your {niche} site — some quick wins that could help."
