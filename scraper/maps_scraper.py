"""
Google Maps business lead scraper.
Primary: Outscraper API (high volume, includes emails)
Fallback: Serper API
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
    Search Google Maps. Uses Outscraper if available, falls back to Serper.
    Returns list of business dicts.
    """
    if config.OUTSCRAPER_API_KEY:
        return _outscraper_search(query, location, num_results)
    elif config.SERPER_API_KEY:
        return _serper_search(query, location, num_results)
    else:
        raise ValueError("No Maps API key set. Add OUTSCRAPER_API_KEY to environment variables.")


# ──────────────────────────────────────────────
# OUTSCRAPER API
# ──────────────────────────────────────────────
def _outscraper_search(query, location="", num_results=20):
    """
    Outscraper Google Maps API.
    Docs: https://app.outscraper.com/api-docs#tag/Google-Maps
    Returns up to 500 results per query. Includes emails directly.
    """
    api_key = config.OUTSCRAPER_API_KEY
    search_query = f"{query}, {location}" if location else query

    try:
        resp = requests.get(
            "https://api.app.outscraper.com/maps/search-v3",
            params={
                "query": search_query,
                "limit": min(num_results, 500),
                "async": "false",
                "language": "en",
                "region": "us",
            },
            headers={"X-API-KEY": api_key},
            timeout=120,  # Outscraper can take time for large queries
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        raise Exception(f"Outscraper API error: {e}")

    results = []
    # Outscraper returns nested array: data[0] = list of places
    places = []
    if isinstance(data.get("data"), list) and len(data["data"]) > 0:
        places = data["data"][0] if isinstance(data["data"][0], list) else data["data"]

    for place in places:
        if not isinstance(place, dict):
            continue

        biz = {
            "title": place.get("name", ""),
            "address": place.get("full_address", "") or place.get("address", ""),
            "phone": place.get("phone", "") or place.get("international_phone", ""),
            "website": place.get("site", "") or place.get("website", ""),
            "rating": str(place.get("rating", "")),
            "reviews": place.get("reviews", 0),
            "category": place.get("category", "") or place.get("type", ""),
            "email": place.get("email", ""),  # Outscraper can return emails directly!
            "description": place.get("description", ""),
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

        # Handle email from Outscraper (can be string or list)
        email_raw = place.get("email")
        if isinstance(email_raw, list) and email_raw:
            biz["email"] = email_raw[0]
        elif isinstance(email_raw, str):
            biz["email"] = email_raw

        results.append(biz)

    return results


# ──────────────────────────────────────────────
# SERPER API (fallback)
# ──────────────────────────────────────────────
def _serper_search(query, location="", num_results=20):
    """Serper.dev Google Maps API (fallback)."""
    api_key = config.SERPER_API_KEY
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
                "email": "",
            }

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


# ──────────────────────────────────────────────
# EMAIL EXTRACTION (website crawling)
# ──────────────────────────────────────────────
def extract_email_from_website(url, timeout=10):
    """Crawl a business website to find contact email."""
    if not url:
        return None

    if not url.startswith("http"):
        url = "https://" + url

    emails = set()
    pages_to_check = [url]

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

            found = EMAIL_RE.findall(resp.text)
            for email in found:
                email = email.lower()
                domain = email.split("@")[1] if "@" in email else ""
                if domain not in JUNK_DOMAINS and not email.endswith(('.png', '.jpg', '.gif', '.svg', '.css', '.js')):
                    emails.add(email)

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
