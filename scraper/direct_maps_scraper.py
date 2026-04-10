"""
Direct Google Maps scraper — no API key needed.
Scrapes Google Maps search results by parsing the actual Google Maps responses.
Uses proxy rotation and rate limiting to avoid blocks.
"""

import re
import json
import time
import random
import urllib.parse
from bs4 import BeautifulSoup

from scraper.proxy_manager import ProxyManager, make_request

# Rate limiter
_last_request_time = 0
MIN_DELAY = 2  # seconds between requests
MAX_DELAY = 5


def _rate_limit():
    """Smart delay between requests."""
    global _last_request_time
    now = time.time()
    elapsed = now - _last_request_time
    delay = random.uniform(MIN_DELAY, MAX_DELAY)
    if elapsed < delay:
        time.sleep(delay - elapsed)
    _last_request_time = time.time()


def search_maps_direct(query, location="", num_results=20, proxy_manager=None):
    """
    Scrape Google Maps search results directly.
    Returns list of business dicts.
    """
    if proxy_manager is None:
        proxy_manager = ProxyManager()

    search_query = f"{query} {location}".strip()
    results = []

    # Method 1: Google Maps search via regular Google with local pack
    results.extend(_scrape_google_local_pack(search_query, num_results, proxy_manager))

    # Method 2: Google Maps direct URL parsing
    if len(results) < num_results:
        remaining = num_results - len(results)
        results.extend(_scrape_maps_search(search_query, remaining, proxy_manager))

    # Deduplicate by name
    seen = set()
    unique = []
    for biz in results:
        key = biz["title"].lower().strip()
        if key not in seen:
            seen.add(key)
            unique.append(biz)

    return unique[:num_results]


def _scrape_google_local_pack(query, num_results, proxy_manager):
    """
    Scrape Google search results for local business listings.
    Google shows a "local pack" with Maps results for business queries.
    """
    results = []
    encoded_query = urllib.parse.quote_plus(query)

    for start in range(0, min(num_results, 100), 10):
        _rate_limit()

        url = f"https://www.google.com/search?q={encoded_query}&num=10&start={start}&gl=us&hl=en"
        resp = make_request(url, proxy_manager=proxy_manager)

        if not resp:
            break

        soup = BeautifulSoup(resp.text, "html.parser")

        # Parse local pack results (div with data-attrid containing business info)
        # Google wraps local results in specific div structures
        for div in soup.find_all("div", class_="VkpGBb"):
            biz = _parse_local_result(div)
            if biz and biz["title"]:
                results.append(biz)

        # Also try parsing from structured data / JSON-LD
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string)
                if isinstance(data, list):
                    for item in data:
                        biz = _parse_jsonld(item)
                        if biz:
                            results.append(biz)
                elif isinstance(data, dict):
                    biz = _parse_jsonld(data)
                    if biz:
                        results.append(biz)
            except Exception:
                continue

        # Parse regular search results for business info
        for div in soup.find_all("div", class_="g"):
            biz = _parse_search_result(div)
            if biz and biz["title"]:
                results.append(biz)

        if len(results) >= num_results:
            break

    return results


def _scrape_maps_search(query, num_results, proxy_manager):
    """
    Scrape Google Maps search page directly.
    Uses the maps search URL which returns business listings.
    """
    results = []
    encoded_query = urllib.parse.quote_plus(query)

    _rate_limit()

    # Google Maps search URL
    url = f"https://www.google.com/maps/search/{encoded_query}/"
    resp = make_request(url, proxy_manager=proxy_manager, allow_redirects=True)

    if not resp:
        return results

    # Google Maps embeds data in the page as JavaScript
    # Look for business data in the response
    text = resp.text

    # Extract business names and details from Maps HTML
    # Google Maps uses specific patterns in its data
    name_pattern = re.compile(r'"([^"]{2,60})",null,null,null,null,null,null,null,null,null,null,null,null,null,null,null,null,null,null,\["')
    phone_pattern = re.compile(r'"(\+?[\d\s\-\(\)]{7,20})"')
    url_pattern = re.compile(r'"(https?://[^"]+)"')
    rating_pattern = re.compile(r'(\d\.\d),\d+,"')

    # Try to find structured data arrays in the JS
    # Google Maps embeds data like: [null,"Business Name",null,[null,null,lat,lng]...]
    biz_blocks = re.findall(r'\["0x[0-9a-f]+:0x[0-9a-f]+".*?\]', text[:500000])

    # Fallback: extract any visible business-like data
    soup = BeautifulSoup(text, "html.parser")

    # Look for aria-label attributes which contain business names
    for elem in soup.find_all(attrs={"aria-label": True}):
        label = elem.get("aria-label", "")
        if len(label) > 3 and len(label) < 100:
            # Check if it looks like a business name (not a button label)
            skip_words = ["close", "search", "menu", "back", "zoom", "directions", "share"]
            if not any(w in label.lower() for w in skip_words):
                biz = {
                    "title": label,
                    "address": "",
                    "phone": "",
                    "website": "",
                    "rating": "",
                    "reviews": 0,
                    "category": "",
                    "email": "",
                    "domain": "",
                }
                results.append(biz)

    return results[:num_results]


def _parse_local_result(div):
    """Parse a Google local pack result div."""
    biz = {
        "title": "",
        "address": "",
        "phone": "",
        "website": "",
        "rating": "",
        "reviews": 0,
        "category": "",
        "email": "",
        "domain": "",
    }

    # Title
    title_elem = div.find("span", class_="OSrXXb") or div.find("div", class_="dbg0pd")
    if title_elem:
        biz["title"] = title_elem.get_text(strip=True)

    # Rating
    rating_elem = div.find("span", class_="yi40Hd")
    if rating_elem:
        biz["rating"] = rating_elem.get_text(strip=True)

    # Address and category from secondary text
    for span in div.find_all("span"):
        text = span.get_text(strip=True)
        if "·" in text:
            parts = text.split("·")
            for part in parts:
                part = part.strip()
                if any(c.isdigit() for c in part) and len(part) > 10:
                    biz["address"] = part
                elif len(part) > 2:
                    biz["category"] = part

    # Website link
    for a in div.find_all("a", href=True):
        href = a["href"]
        if href.startswith("http") and "google" not in href:
            biz["website"] = href
            try:
                parsed = urllib.parse.urlparse(href)
                biz["domain"] = re.sub(r'^www\.', '', parsed.netloc.lower())
            except Exception:
                pass

    # Phone
    phone_match = re.search(r'(\(\d{3}\)\s*\d{3}[-\s]?\d{4}|\+\d[\d\s\-]{8,})', div.get_text())
    if phone_match:
        biz["phone"] = phone_match.group(1).strip()

    return biz if biz["title"] else None


def _parse_search_result(div):
    """Parse a regular Google search result for business info."""
    biz = {
        "title": "",
        "address": "",
        "phone": "",
        "website": "",
        "rating": "",
        "reviews": 0,
        "category": "",
        "email": "",
        "domain": "",
    }

    # Title + link
    h3 = div.find("h3")
    if h3:
        biz["title"] = h3.get_text(strip=True)

    link = div.find("a", href=True)
    if link:
        href = link["href"]
        if href.startswith("http") and "google" not in href:
            biz["website"] = href
            try:
                parsed = urllib.parse.urlparse(href)
                biz["domain"] = re.sub(r'^www\.', '', parsed.netloc.lower())
            except Exception:
                pass

    # Only return if it looks like a business (has both title and website)
    return biz if biz["title"] and biz["website"] else None


def _parse_jsonld(data):
    """Parse JSON-LD structured data for business info."""
    if not isinstance(data, dict):
        return None

    biz_types = ["LocalBusiness", "Organization", "Restaurant", "Store",
                 "MedicalBusiness", "LegalService", "RealEstateAgent"]

    dtype = data.get("@type", "")
    if not any(t in str(dtype) for t in biz_types):
        return None

    biz = {
        "title": data.get("name", ""),
        "address": "",
        "phone": data.get("telephone", ""),
        "website": data.get("url", ""),
        "rating": "",
        "reviews": 0,
        "category": dtype if isinstance(dtype, str) else "",
        "email": data.get("email", ""),
        "domain": "",
    }

    # Address
    addr = data.get("address", {})
    if isinstance(addr, dict):
        parts = [addr.get("streetAddress", ""), addr.get("addressLocality", ""),
                 addr.get("addressRegion", ""), addr.get("postalCode", "")]
        biz["address"] = ", ".join(p for p in parts if p)

    # Rating
    agg = data.get("aggregateRating", {})
    if isinstance(agg, dict):
        biz["rating"] = str(agg.get("ratingValue", ""))
        biz["reviews"] = agg.get("reviewCount", 0)

    # Domain
    if biz["website"]:
        try:
            parsed = urllib.parse.urlparse(biz["website"])
            biz["domain"] = re.sub(r'^www\.', '', parsed.netloc.lower())
        except Exception:
            pass

    return biz if biz["title"] else None
