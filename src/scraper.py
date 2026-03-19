"""
Step 1 — Google Maps Lead Scraper via Serper.dev.

Fetches business listings from Google Maps using the Serper.dev Maps API,
handles pagination, deduplicates results, and saves to CSV.

Serper.dev Maps API:
  - POST https://google.serper.dev/maps
  - Header: X-API-KEY
  - Body: {"q": "cafes in Key West", "num": 20, "page": 1}
  - Pagination: increment `page` (1, 2, 3...)
"""

import csv
import json
import os
import re
import sys
import time
from math import ceil

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


def _call_serper(keyword: str, location: str, page: int = 1, num: int = 20) -> dict:
    """Call Serper.dev Maps endpoint for a single page of results."""
    headers = {
        "X-API-KEY": config.SERPER_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "q": f"{keyword} in {location}",
        "num": num,
        "page": page,
    }

    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            resp = requests.post(
                config.SERPER_MAPS_URL,
                headers=headers,
                data=json.dumps(payload),
                timeout=config.REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            wait = config.RETRY_BACKOFF ** attempt
            print(f"  [!] API error (attempt {attempt}/{config.MAX_RETRIES}): {e}")
            if attempt < config.MAX_RETRIES:
                print(f"      Retrying in {wait}s...")
                time.sleep(wait)
            else:
                print("  [!] Max retries reached. Skipping this page.")
                return {}


def _parse_results(raw: dict) -> list[dict]:
    """Extract structured lead data from Serper.dev Maps response."""
    leads = []
    for place in raw.get("places", []):
        lead = {
            "Name": (place.get("title") or "").strip(),
            "Address": (place.get("address") or "").strip(),
            "Phone": (place.get("phoneNumber") or "").strip(),
            "Website": (place.get("website") or "").strip(),
            "Rating": place.get("rating", ""),
            "Reviews": place.get("ratingCount", ""),
        }
        if lead["Rating"] is None:
            lead["Rating"] = ""
        if lead["Reviews"] is None:
            lead["Reviews"] = ""
        leads.append(lead)
    return leads


def fetch_leads(keyword: str, location: str, num_results: int = 20) -> list[dict]:
    """
    Fetch business leads from Google Maps via Serper.dev.

    Handles pagination automatically — Serper.dev Maps uses `page` param (1, 2, 3...).
    Deduplicates on Name + Address.
    """
    if not config.SERPER_API_KEY:
        print("[ERROR] SERPER_API_KEY not set. Add it to your .env file.")
        return []

    total_pages = ceil(num_results / config.RESULTS_PER_PAGE)
    all_leads = []
    seen = set()  # (name_lower, address_lower) for dedup

    print(f"\n{'='*50}")
    print(f"  Scraping Google Maps (Serper.dev)")
    print(f"  Keyword : {keyword}")
    print(f"  Location: {location}")
    print(f"  Target  : {num_results} leads ({total_pages} page(s))")
    print(f"{'='*50}\n")

    for page in range(1, total_pages + 1):
        print(f"  Fetching page {page}/{total_pages}...")

        data = _call_serper(keyword, location, page=page)
        if not data:
            continue

        page_leads = _parse_results(data)
        if not page_leads:
            print("  [!] No results on this page. Stopping pagination.")
            break

        # Deduplicate
        new_count = 0
        for lead in page_leads:
            key = (lead["Name"].lower(), lead["Address"].lower())
            if key not in seen and lead["Name"]:
                seen.add(key)
                all_leads.append(lead)
                new_count += 1

        print(f"  Found {len(page_leads)} results, {new_count} new (total: {len(all_leads)})")

        # Stop if we have enough
        if len(all_leads) >= num_results:
            all_leads = all_leads[:num_results]
            break

        # Rate-limit between pages
        if page < total_pages:
            time.sleep(config.SCRAPE_DELAY)

    print(f"\n  Total unique leads: {len(all_leads)}")
    return all_leads


def save_to_csv(leads: list[dict], keyword: str, location: str) -> str:
    """Save leads to CSV file. Returns the file path."""
    if not leads:
        print("[!] No leads to save.")
        return ""

    # Sanitize filename
    safe_kw = re.sub(r'[^\w\s-]', '', keyword).strip().replace(' ', '_').lower()
    safe_loc = re.sub(r'[^\w\s-]', '', location).strip().replace(' ', '_').lower()
    filename = f"leads_{safe_kw}_{safe_loc}.csv"
    filepath = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), filename)

    fieldnames = ["Name", "Address", "Phone", "Website", "Rating", "Reviews"]
    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(leads)

    print(f"\n  CSV saved: {filepath}")
    print(f"  Total rows: {len(leads)}")
    return filepath


# ── Standalone test ──────────────────────────────────────────────────
if __name__ == "__main__":
    kw = input("Enter keyword (e.g., cafes): ").strip()
    loc = input("Enter location (e.g., Key West, Florida): ").strip()
    count = int(input("Number of leads to fetch: ").strip() or "20")

    results = fetch_leads(kw, loc, count)
    if results:
        save_to_csv(results, kw, loc)
    else:
        print("No results found.")
