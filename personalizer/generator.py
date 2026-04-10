"""
AI-powered email personalizer.
Reads a store's actual website → finds real problems → writes specific outreach.
Uses OpenAI (priority) or Anthropic.
"""

import os
import re
import requests
from bs4 import BeautifulSoup


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
}


def generate_first_lines(leads, batch_size=10):
    """Generate personalized first lines for a batch of leads.
    Tries AI first, falls back to audit-based templates.
    """
    openai_key = os.getenv("OPENAI_API_KEY", "")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")

    if openai_key:
        return _generate_openai_smart(leads, openai_key, batch_size)
    elif anthropic_key:
        return _generate_anthropic_smart(leads, anthropic_key, batch_size)
    else:
        return _generate_from_audit(leads)


def _scrape_store_context(lead):
    """Visit the store website and extract real context for personalization."""
    website = lead.get("website") or (f"https://{lead['domain']}" if lead.get("domain") else "")
    if not website:
        return {"has_site": False}

    if not website.startswith("http"):
        website = "https://" + website

    context = {
        "has_site": True,
        "title": "",
        "products": [],
        "has_chatbot": False,
        "has_faq": False,
        "has_contact_form": False,
        "platform": "",
        "issues": [],
    }

    try:
        resp = requests.get(website, headers=HEADERS, timeout=10, allow_redirects=True)
        if resp.status_code != 200:
            context["has_site"] = False
            return context

        html = resp.text.lower()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Title
        title = soup.find("title")
        if title and title.string:
            context["title"] = title.string.strip()[:100]

        # Platform detection
        if "shopify" in html or "cdn.shopify" in html:
            context["platform"] = "Shopify"
        elif "woocommerce" in html:
            context["platform"] = "WooCommerce"
        elif "squarespace" in html:
            context["platform"] = "Squarespace"

        # Chatbot detection
        chatbot_indicators = ['tawk.to', 'tidio', 'intercom', 'drift', 'crisp.chat',
                              'livechat', 'zendesk', 'hubspot', 'chatwoot', 'olark',
                              'jivochat', 'freshdesk']
        context["has_chatbot"] = any(ind in html for ind in chatbot_indicators)

        # FAQ page detection
        context["has_faq"] = any(x in html for x in ['/faq', '/frequently-asked', 'faq-page'])

        # Contact form
        context["has_contact_form"] = bool(soup.find("form", attrs={"action": re.compile(r"contact|message|support", re.I)}))

        # Products (grab a few names)
        for tag in soup.find_all(["h2", "h3", "h4"], limit=10):
            text = tag.get_text(strip=True)
            if 5 < len(text) < 80:
                context["products"].append(text)

        # Issues
        if not context["has_chatbot"]:
            context["issues"].append("no_chatbot")
        if not context["has_faq"]:
            context["issues"].append("no_faq")

    except Exception:
        context["has_site"] = False

    return context


def _build_smart_prompt(leads_with_context):
    """Build AI prompt with real store data."""
    stores = []
    for lead, ctx in leads_with_context:
        name = lead.get("store_name", lead.get("domain", ""))
        domain = lead.get("domain", "")
        parts = [f"Store: {name} ({domain})"]

        if ctx.get("platform"):
            parts.append(f"Platform: {ctx['platform']}")
        if ctx.get("title"):
            parts.append(f"Site title: {ctx['title']}")
        if ctx.get("products"):
            parts.append(f"Products: {', '.join(ctx['products'][:3])}")
        if ctx.get("has_chatbot"):
            parts.append("Has chatbot: YES")
        else:
            parts.append("Has chatbot: NO — handles support manually")
        if not ctx.get("has_faq"):
            parts.append("No FAQ page — customers likely ask repetitive questions")
        if not ctx.get("has_site"):
            parts.append("No website found")

        stores.append("\n".join(parts))

    stores_text = "\n\n".join(stores)

    return f"""You are writing cold email opening lines for a Shopify automation agency.

For each store below, write ONE personalized opening line (max 20 words) that:
1. References something SPECIFIC you found on their actual site (a product, missing feature, platform)
2. Ties to a problem they likely have (slow support, no chatbot, manual work)
3. Sounds like a real human wrote it, not a template
4. NEVER uses generic phrases like "I noticed your store" or "great store"

BAD examples:
- "Saw your store — looks great." (too generic)
- "I build AI chatbots for Shopify." (about you, not them)

GOOD examples:
- "Your skincare line looks solid — but no instant replies on sizing questions could be costing you sales."
- "Noticed customers can't get quick answers about shipping on your site — easy fix."

Stores:
{stores_text}

Output format — one per line:
domain | personalized first line"""


def _parse_response(text, leads_batch):
    """Parse AI response into domain -> first_line mapping."""
    result = {}
    for line in text.strip().split("\n"):
        if "|" in line:
            parts = line.split("|", 1)
            domain = parts[0].strip().lower()
            first_line = parts[1].strip().strip('"')
            if first_line:
                result[domain] = first_line

    # Fill missing from audit data
    for lead in leads_batch:
        if lead["domain"] not in result:
            result[lead["domain"]] = _audit_line(lead)

    return result


def _audit_line(lead):
    """Generate a line from audit data without AI."""
    name = lead.get("store_name", lead.get("domain", "your store"))
    if lead.get("has_chatbot") == 0 and lead.get("website"):
        return f"Checked {name} — noticed customer questions aren't getting instant replies right now."
    if not lead.get("website"):
        return f"{name} doesn't have an online presence yet — big missed opportunity."
    return f"Took a look at {name} — a few quick automation wins that could save you hours weekly."


def _generate_openai_smart(leads, api_key, batch_size):
    """AI personalization via OpenAI with real store context."""
    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    all_results = {}

    for i in range(0, len(leads), batch_size):
        batch = leads[i:i + batch_size]

        # Scrape each store for real context
        leads_with_context = []
        for lead in batch:
            ctx = _scrape_store_context(lead)
            leads_with_context.append((lead, ctx))

        prompt = _build_smart_prompt(leads_with_context)

        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1024,
            )
            text = response.choices[0].message.content
            all_results.update(_parse_response(text, batch))
        except Exception:
            # Fallback to audit-based lines
            for lead in batch:
                all_results[lead["domain"]] = _audit_line(lead)

    return all_results


def _generate_anthropic_smart(leads, api_key, batch_size):
    """AI personalization via Anthropic with real store context."""
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    all_results = {}

    for i in range(0, len(leads), batch_size):
        batch = leads[i:i + batch_size]

        leads_with_context = []
        for lead in batch:
            ctx = _scrape_store_context(lead)
            leads_with_context.append((lead, ctx))

        prompt = _build_smart_prompt(leads_with_context)

        try:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}]
            )
            text = response.content[0].text
            all_results.update(_parse_response(text, batch))
        except Exception:
            for lead in batch:
                all_results[lead["domain"]] = _audit_line(lead)

    return all_results


def _generate_from_audit(leads):
    """No AI available — use audit data to generate lines."""
    result = {}
    for lead in leads:
        result[lead["domain"]] = _audit_line(lead)
    return result
