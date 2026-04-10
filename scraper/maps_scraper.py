"""
Google Maps business lead scraper via Serper.dev API.
Searches for businesses, extracts name, phone, website, address, rating.
Then crawls websites to find contact emails.
"""

import re
import time
import requests
from urllib.parse import urlparse
from bs4 import BeautifulSoup

import config

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
}

EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')
JUNK_DOMAINS = {'example.com', 'sentry.io', 'wixpress.com', 'w3.org',
                'googleapis.com', 'google.com', 'gstatic.com', 'facebook.com',
                'twitter.com', 'instagram.com', 'schema.org'}


def search_google_maps(query, location="", num_results=20):
    """
    Search Google Maps via Serper API.
    Returns list of business dicts with: title, address, phone, website, rating, domain.
    """
    api_key = config.SERPER_API_KEY
    if not api_key:
        raise ValueError("SERPER_API_KEY not set. Add it to environment variables.")

    results = []
    page = 1
    collected = 0

    while collected < num_results:
        payload = {
            "q": query,
            "type": "maps",
            "num": min(20, num_results - collected),
        }
        if location:
            payload["location"] = location
        if page > 1:
            payload["page"] = page

        try:
            resp = requests.post(
                "https://google.serper.dev/maps",
                json=payload,
                headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            raise Exception(f"Serper API error: {e}")

        places = data.get("places", [])
        if not places:
            break

        for place in places:
            biz = {
                "title": place.get("title", ""),
                "address": place.get("address", ""),
                "phone": place.get("phoneNumber", ""),
                "website": place.get("website", ""),
                "rating": str(place.get("rating", "")),
                "reviews": place.get("reviewsCount", 0),
                "category": place.get("category", ""),
                "cid": place.get("cid", ""),
            }

            # Extract domain from website
            if biz["website"]:
                try:
                    parsed = urlparse(biz["website"])
                    biz["domain"] = re.sub(r'^www\.', '', parsed.netloc.lower())
                except Exception:
                    biz["domain"] = ""
            else:
                biz["domain"] = ""

            results.append(biz)
            collected += 1

            if collected >= num_results:
                break

        page += 1
        time.sleep(1)

    return results


def extract_email_from_website(url, timeout=10):
    """Crawl a business website to find contact email."""
    if not url:
        return None

    # Ensure URL has scheme
    if not url.startswith("http"):
        url = "https://" + url

    emails = set()
    pages_to_check = [url]

    # Also check common contact pages
    try:
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        for path in ["/contact", "/contact-us", "/about", "/about-us"]:
            pages_to_check.append(base + path)
    except Exception:
        pass

    for page_url in pages_to_check:
        try:
            resp = requests.get(page_url, headers=HEADERS, timeout=timeout, allow_redirects=True)
            if resp.status_code != 200:
                continue

            # Find emails in page text
            found = EMAIL_RE.findall(resp.text)
            for email in found:
                email = email.lower()
                domain = email.split("@")[1] if "@" in email else ""
                if domain not in JUNK_DOMAINS and not email.endswith(('.png', '.jpg', '.gif', '.svg', '.css', '.js')):
                    emails.add(email)

            # Check mailto links
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.find_all("a", href=True):
                if a["href"].startswith("mailto:"):
                    email = a["href"].replace("mailto:", "").split("?")[0].strip().lower()
                    if "@" in email:
                        domain = email.split("@")[1]
                        if domain not in JUNK_DOMAINS:
                            emails.add(email)

            time.sleep(0.5)

        except Exception:
            continue

    return _pick_best_email(emails)


def _pick_best_email(emails):
    """Pick the best contact email from a set."""
    if not emails:
        return None

    priority = ['info@', 'contact@', 'hello@', 'hi@', 'sales@',
                'admin@', 'owner@', 'founder@', 'team@', 'office@']

    for prefix in priority:
        for email in emails:
            if email.startswith(prefix):
                return email

    non_junk = [e for e in emails if not e.startswith(('support@', 'noreply@', 'no-reply@', 'webmaster@'))]
    if non_junk:
        return non_junk[0]

    return list(emails)[0]
