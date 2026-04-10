"""
Crawl Shopify store websites to extract:
- Store name
- Contact email
- Basic store info for personalization
"""

import re
import time
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# Pages most likely to contain contact emails
CONTACT_PATHS = ["/", "/pages/contact", "/pages/contact-us", "/pages/about",
                 "/pages/about-us", "/contact", "/about"]

EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')

# Emails to skip
JUNK_EMAILS = {'support@shopify.com', 'help@shopify.com', 'noreply@shopify.com',
               'example@example.com', 'email@example.com', 'your@email.com'}
JUNK_DOMAINS = {'shopify.com', 'example.com', 'sentry.io', 'wixpress.com',
                'cloudflare.com', 'googleapis.com', 'w3.org'}


def extract_store_info(domain):
    """Crawl a domain and extract store name + email."""
    store_name = None
    emails = set()

    for path in CONTACT_PATHS:
        url = f"https://{domain}{path}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10, allow_redirects=True)
            if resp.status_code != 200:
                continue

            soup = BeautifulSoup(resp.text, "html.parser")

            # Extract store name from <title>
            if not store_name:
                store_name = _get_store_name(soup, domain)

            # Extract emails from page
            page_emails = _extract_emails(resp.text)
            emails.update(page_emails)

            # Check mailto: links
            for a in soup.find_all("a", href=True):
                if a["href"].startswith("mailto:"):
                    email = a["href"].replace("mailto:", "").split("?")[0].strip().lower()
                    if _is_valid_email(email):
                        emails.add(email)

            time.sleep(1)

        except Exception:
            continue

    # Also check if Shopify store (look for Shopify indicators)
    is_shopify = _detect_shopify(domain)

    # Pick best email (prefer info@, contact@, hello@ over support@)
    best_email = _pick_best_email(emails)

    return {
        "domain": domain,
        "store_name": store_name or _domain_to_name(domain),
        "email": best_email,
        "all_emails": list(emails),
        "is_shopify": is_shopify,
    }


def _get_store_name(soup, domain):
    """Extract store name from page."""
    # Try <title>
    title = soup.find("title")
    if title and title.string:
        name = title.string.strip()
        # Clean common suffixes
        for suffix in [" – ", " - ", " | ", " — "]:
            if suffix in name:
                name = name.split(suffix)[0].strip()
        if len(name) > 2 and len(name) < 60:
            return name

    # Try og:site_name
    og = soup.find("meta", property="og:site_name")
    if og and og.get("content"):
        return og["content"].strip()

    return None


def _domain_to_name(domain):
    """Convert domain to readable name."""
    name = domain.split(".")[0]
    name = re.sub(r'[-_]', ' ', name)
    return name.title()


def _extract_emails(text):
    """Find emails in page text."""
    found = EMAIL_RE.findall(text)
    return {e.lower() for e in found if _is_valid_email(e.lower())}


def _is_valid_email(email):
    """Filter out junk emails."""
    if email in JUNK_EMAILS:
        return False
    domain = email.split("@")[1] if "@" in email else ""
    if domain in JUNK_DOMAINS:
        return False
    # Skip image file extensions mistaken as emails
    if any(email.endswith(ext) for ext in ['.png', '.jpg', '.gif', '.svg', '.css', '.js']):
        return False
    return True


def _pick_best_email(emails):
    """Pick the best contact email from a set."""
    if not emails:
        return None

    # Priority order for prefixes
    priority = ['info@', 'contact@', 'hello@', 'hi@', 'hey@', 'sales@',
                'admin@', 'owner@', 'founder@', 'team@']

    for prefix in priority:
        for email in emails:
            if email.startswith(prefix):
                return email

    # Avoid support@ and noreply@
    non_support = [e for e in emails if not e.startswith(('support@', 'noreply@', 'no-reply@'))]
    if non_support:
        return non_support[0]

    return list(emails)[0]


def _detect_shopify(domain):
    """Quick check if domain runs on Shopify."""
    try:
        resp = requests.get(f"https://{domain}", headers=HEADERS, timeout=8, allow_redirects=True)
        indicators = ['shopify.com', 'cdn.shopify.com', 'Shopify.theme', 'myshopify.com']
        return any(ind in resp.text for ind in indicators)
    except Exception:
        return False
