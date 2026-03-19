"""
Step 2 — Email Enrichment Engine.

Reads a CSV of leads, visits each business website (homepage + contact page),
extracts email addresses via regex, and outputs an enriched CSV.
"""

import csv
import os
import re
import random
import sys
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from src.metrics import log_event

# Pre-compiled email regex
EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')


def fetch_website(url: str) -> str | None:
    """Fetch HTML content from a URL. Returns None on failure."""
    if not url or not url.startswith(("http://", "https://")):
        if url and not url.startswith("http"):
            url = "https://" + url
        else:
            return None

    headers = {"User-Agent": config.USER_AGENT}
    try:
        resp = requests.get(url, headers=headers, timeout=config.REQUEST_TIMEOUT, allow_redirects=True)
        resp.raise_for_status()
        # Only process HTML responses
        content_type = resp.headers.get("Content-Type", "")
        if "text/html" not in content_type and "text/plain" not in content_type:
            return None
        return resp.text
    except requests.RequestException:
        return None


def extract_emails(html: str) -> list[str]:
    """
    Extract valid email addresses from HTML content.
    Filters out junk emails (images, noreply, etc.) and deduplicates.
    """
    if not html:
        return []

    raw_emails = EMAIL_RE.findall(html.lower())
    valid = []
    seen = set()

    for email in raw_emails:
        # Skip if already seen
        if email in seen:
            continue
        seen.add(email)

        # Skip junk file extensions
        if any(email.endswith(ext) for ext in config.JUNK_EMAIL_EXTENSIONS):
            continue

        local_part = email.split("@")[0]
        domain = email.split("@")[1]

        # Skip junk prefixes (noreply, support, admin, etc.)
        if local_part in config.JUNK_EMAIL_PREFIXES:
            continue

        # Skip junk domains (wixpress, sentry, example.com, etc.)
        if domain in config.JUNK_EMAIL_DOMAINS:
            continue
        # Also check subdomain matches (e.g. sentry-next.wixpress.com)
        if any(domain.endswith("." + d) for d in config.JUNK_EMAIL_DOMAINS):
            continue

        # Skip auto-generated emails (long hex strings like 605a7baede844d27...)
        if len(local_part) > 20 and all(c in "0123456789abcdef" for c in local_part.replace("-", "")):
            continue

        # Skip emails where local part matches the domain (user@domain.com pattern)
        domain_name = domain.split(".")[0]
        if local_part == domain_name:
            continue

        # Skip very short local parts (a@, ab@)
        if len(local_part) < 3:
            continue

        valid.append(email)

    return valid


def find_contact_page(html: str, base_url: str) -> list[str]:
    """
    Scan page for links to contact/about pages.
    Returns list of absolute URLs found.
    """
    if not html or not base_url:
        return []

    soup = BeautifulSoup(html, "html.parser")
    contact_urls = []
    seen = set()

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip().lower()
        # Check if link text or href contains contact keywords
        link_text = (a_tag.get_text() or "").strip().lower()
        combined = href + " " + link_text

        if any(kw in combined for kw in config.CONTACT_PAGE_KEYWORDS):
            full_url = urljoin(base_url, a_tag["href"].strip())
            # Only follow links on the same domain
            if urlparse(full_url).netloc == urlparse(base_url).netloc:
                if full_url not in seen:
                    seen.add(full_url)
                    contact_urls.append(full_url)

    return contact_urls[:3]  # Limit to 3 contact pages max


def _scrape_emails_from_site(website: str) -> list[str]:
    """Scrape emails from a website's homepage and contact pages."""
    all_emails = []

    # 1. Fetch homepage
    html = fetch_website(website)
    if html:
        all_emails.extend(extract_emails(html))

        # 2. Find and visit contact pages
        contact_pages = find_contact_page(html, website)
        for page_url in contact_pages:
            time.sleep(random.uniform(0.5, 1.0))
            page_html = fetch_website(page_url)
            if page_html:
                all_emails.extend(extract_emails(page_html))

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for email in all_emails:
        if email not in seen:
            seen.add(email)
            unique.append(email)

    return unique


def enrich_leads(csv_path: str) -> list[dict]:
    """
    Read a CSV of leads, visit each website, extract emails.
    Returns enriched list of lead dicts with Email and Email Status columns.
    """
    if not os.path.exists(csv_path):
        print(f"[ERROR] CSV not found: {csv_path}")
        return []

    # Read input CSV
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        leads = list(reader)

    if not leads:
        print("[!] CSV is empty.")
        return []

    print(f"\n{'='*50}")
    print(f"  Email Enrichment")
    print(f"  Input : {csv_path}")
    print(f"  Leads : {len(leads)}")
    print(f"{'='*50}\n")

    enriched = []
    emails_found = 0

    for i, lead in enumerate(leads, 1):
        name = lead.get("Name", "Unknown")
        website = lead.get("Website", "").strip()

        print(f"  [{i}/{len(leads)}] {name}...", end=" ")

        if website:
            emails = _scrape_emails_from_site(website)
            if emails:
                lead["Email"] = emails[0]  # Primary email
                lead["Email Status"] = "Email Found"
                emails_found += 1
                log_event("collected", recipient=emails[0], details=name)
                print(f"found {len(emails)} email(s): {emails[0]}")
            else:
                lead["Email"] = ""
                lead["Email Status"] = "No Email Found"
                print("no email found")
        else:
            lead["Email"] = ""
            lead["Email Status"] = "No Email Found"
            print("no website, skipped")

        enriched.append(lead)

        # Rate limit between sites
        if i < len(leads):
            time.sleep(random.uniform(config.CRAWL_DELAY_MIN, config.CRAWL_DELAY_MAX))

    print(f"\n  Enrichment complete: {emails_found}/{len(leads)} emails found")
    return enriched


def save_enriched_csv(leads: list[dict], output_path: str = "") -> str:
    """Save enriched leads to CSV. Returns file path."""
    if not leads:
        print("[!] No leads to save.")
        return ""

    if not output_path:
        output_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "enriched_leads.csv",
        )

    fieldnames = ["Name", "Address", "Phone", "Website", "Email", "Email Status", "Rating", "Reviews"]
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(leads)

    print(f"  Enriched CSV saved: {output_path}")
    return output_path


# ── Standalone test ──────────────────────────────────────────────────
if __name__ == "__main__":
    path = input("Enter CSV path to enrich: ").strip()
    results = enrich_leads(path)
    if results:
        save_enriched_csv(results)
