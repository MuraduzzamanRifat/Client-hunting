"""
Shopify store discovery via search engines.
Uses DuckDuckGo (free, no key) with SerpAPI as optional upgrade.
"""

import re
import time
import requests
from urllib.parse import urlparse

from config import SEARCH_QUERIES, SERPAPI_KEY


def search_shopify_stores(niche, max_results=100):
    """Search for Shopify stores in a niche. Returns list of domains."""
    domains = set()

    for query_template in SEARCH_QUERIES:
        query = query_template.format(niche=niche)
        if SERPAPI_KEY:
            results = _serpapi_search(query, max_results // len(SEARCH_QUERIES))
        else:
            results = _ddg_search(query, max_results // len(SEARCH_QUERIES))

        for url in results:
            domain = _extract_domain(url)
            if domain:
                domains.add(domain)

        time.sleep(2)  # Rate limiting between queries

    return list(domains)


def _ddg_search(query, max_results=20):
    """DuckDuckGo search — no API key needed."""
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = ddgs.text(query, max_results=max_results)
            return [r["href"] for r in results if "href" in r]
    except Exception as e:
        print(f"  DuckDuckGo error: {e}")
        return []


def _serpapi_search(query, max_results=20):
    """SerpAPI search — needs API key."""
    try:
        resp = requests.get("https://serpapi.com/search", params={
            "q": query,
            "api_key": SERPAPI_KEY,
            "num": max_results,
            "engine": "google",
        }, timeout=15)
        data = resp.json()
        return [r["link"] for r in data.get("organic_results", []) if "link" in r]
    except Exception as e:
        print(f"  SerpAPI error: {e}")
        return []


def _extract_domain(url):
    """Extract clean domain from URL."""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        # Remove www.
        domain = re.sub(r'^www\.', '', domain)
        # Skip non-store domains
        skip = ['reddit.com', 'youtube.com', 'facebook.com', 'twitter.com',
                'instagram.com', 'tiktok.com', 'pinterest.com', 'linkedin.com',
                'medium.com', 'quora.com', 'amazon.com', 'ebay.com', 'etsy.com',
                'shopify.com', 'apps.shopify.com', 'themes.shopify.com']
        if domain in skip or not domain:
            return None
        return domain
    except Exception:
        return None
