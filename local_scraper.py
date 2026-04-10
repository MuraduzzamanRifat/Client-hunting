#!/usr/bin/env python3
"""
Local Google Maps Scraper — runs on your PC, pushes leads to Koyeb.

Usage:
    python local_scraper.py "digital marketing agency" --location "New York" --max 50
    python local_scraper.py "restaurant" --location "London" --max 100 --proxy proxies.txt

Features:
    - Direct Google Maps scraping (no API key needed)
    - Outscraper API support (if key set)
    - Proxy rotation from file or env var
    - Auto website crawling for emails
    - Website audit (chatbot/automation detection)
    - Pushes leads to your Koyeb dashboard via API
    - Also saves locally to CSV
"""

import sys
import os
import csv
import json
import time
import argparse

# Add project root to path
sys.path.insert(0, os.path.dirname(__file__))

# Fix Windows encoding
if sys.platform == "win32":
    os.environ.setdefault("PYTHONUTF8", "1")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from dotenv import load_dotenv
load_dotenv()

from scraper.proxy_manager import ProxyManager
from scraper.maps_scraper import search_google_maps, extract_email_from_website
from scraper.direct_maps_scraper import search_maps_direct
from scraper.website_auditor import audit_website

# Koyeb API URL (set in .env or as argument)
KOYEB_URL = os.getenv("KOYEB_URL", "")


def main():
    parser = argparse.ArgumentParser(description="Local Google Maps Lead Scraper")
    parser.add_argument("query", help="Search query (e.g. 'digital marketing agency')")
    parser.add_argument("--location", "-l", default="", help="Location (e.g. 'New York')")
    parser.add_argument("--max", "-n", type=int, default=20, help="Max results (default: 20)")
    parser.add_argument("--method", "-m", choices=["direct", "outscraper", "auto"], default="auto",
                        help="Scraping method (default: auto)")
    parser.add_argument("--proxy", "-p", default="", help="Proxy file path (one per line)")
    parser.add_argument("--koyeb", "-k", default="", help="Koyeb app URL (e.g. https://your-app.koyeb.app)")
    parser.add_argument("--output", "-o", default="", help="Output CSV file")
    parser.add_argument("--no-audit", action="store_true", help="Skip website audit (faster)")
    parser.add_argument("--no-email", action="store_true", help="Skip email extraction (faster)")
    args = parser.parse_args()

    koyeb_url = args.koyeb or KOYEB_URL
    output_file = args.output or f"leads_{args.query.replace(' ', '_')[:30]}.csv"

    # Load proxies
    if args.proxy:
        os.environ["PROXY_LIST"] = ""  # Clear env
        # Load from file into env
        if os.path.exists(args.proxy):
            with open(args.proxy) as f:
                proxies = [l.strip() for l in f if l.strip() and not l.startswith("#")]
                os.environ["PROXY_LIST"] = ",".join(proxies)

    proxy_manager = ProxyManager()

    print(f"\n{'='*60}")
    print(f"  Google Maps Lead Scraper")
    print(f"{'='*60}")
    print(f"  Query:    {args.query}")
    print(f"  Location: {args.location or '(worldwide)'}")
    print(f"  Max:      {args.max}")
    print(f"  Method:   {args.method}")
    print(f"  Proxies:  {proxy_manager.count()} loaded")
    print(f"  Output:   {output_file}")
    if koyeb_url:
        print(f"  Koyeb:    {koyeb_url}")
    print(f"{'='*60}\n")

    # Step 1: Search Google Maps
    print("[1/4] Searching Google Maps...")
    businesses = []

    if args.method == "outscraper" or (args.method == "auto" and os.getenv("OUTSCRAPER_API_KEY")):
        print("  Using Outscraper API...")
        try:
            businesses = search_google_maps(args.query, location=args.location, num_results=args.max)
            print(f"  Found {len(businesses)} businesses via Outscraper")
        except Exception as e:
            print(f"  Outscraper failed: {e}")
            print("  Falling back to direct scraping...")

    if not businesses and args.method in ("direct", "auto"):
        print("  Using direct Google scraping...")
        businesses = search_maps_direct(args.query, location=args.location,
                                         num_results=args.max, proxy_manager=proxy_manager)
        print(f"  Found {len(businesses)} businesses via direct scraping")

    if not businesses:
        print("\n  No businesses found. Try a different query or add proxies.")
        return

    # Step 2: Extract emails + audit websites
    leads = []
    print(f"\n[2/4] Processing {len(businesses)} businesses...")

    for i, biz in enumerate(businesses, 1):
        name = biz.get("title", "Unknown")
        website = biz.get("website", "")
        email = biz.get("email", "")

        status_parts = [f"  [{i}/{len(businesses)}] {name}"]

        # Extract email from website if not already provided
        if not email and website and not args.no_email:
            try:
                email = extract_email_from_website(website)
                if email:
                    status_parts.append(f"email: {email}")
            except Exception:
                pass

        # Audit website
        audit = {"score": 50, "has_chatbot": False, "has_automation": False,
                 "load_time": None, "personal_line": ""}

        if website and not args.no_audit:
            try:
                audit = audit_website(website)
            except Exception:
                pass
        elif not website:
            audit["score"] = 5
            audit["personal_line"] = f"Noticed {name} doesn't have a website yet."

        # Build lead
        domain = biz.get("domain", "")
        if not domain:
            domain = name.lower().replace(" ", "-").replace(".", "")[:50]

        lead = {
            "domain": domain,
            "store_name": name,
            "email": email or "",
            "phone": biz.get("phone", ""),
            "website": website,
            "address": biz.get("address", ""),
            "rating": biz.get("rating", ""),
            "niche": args.query,
            "source": "google_maps_local",
            "score": audit.get("score", 50),
            "has_chatbot": 1 if audit.get("has_chatbot") else 0,
            "has_automation": 1 if audit.get("has_automation") else 0,
            "first_line": audit.get("personal_line", ""),
        }
        leads.append(lead)

        # Status output
        tags = []
        if email:
            tags.append(f"email: {email}")
        if biz.get("phone"):
            tags.append(f"phone: {biz['phone']}")
        if audit.get("score", 50) < 20:
            tags.append("HOT")
        elif audit.get("score", 50) < 40:
            tags.append("WARM")
        if not audit.get("has_chatbot") and website:
            tags.append("no chatbot")

        tag_str = f" | {' | '.join(tags)}" if tags else ""
        print(f"  [{i}/{len(businesses)}] {name}{tag_str}")

    # Step 3: Save to CSV
    print(f"\n[3/4] Saving {len(leads)} leads to {output_file}...")
    with open(output_file, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "store_name", "email", "phone", "website", "address",
            "rating", "score", "has_chatbot", "niche", "domain", "first_line"
        ])
        writer.writeheader()
        for lead in leads:
            writer.writerow({k: lead.get(k, "") for k in writer.fieldnames})
    print(f"  Saved to {output_file}")

    # Step 4: Push to Koyeb
    if koyeb_url:
        print(f"\n[4/4] Pushing {len(leads)} leads to Koyeb...")
        pushed = 0
        import requests
        for lead in leads:
            try:
                resp = requests.post(
                    f"{koyeb_url.rstrip('/')}/api/leads",
                    json=lead,
                    timeout=10,
                )
                if resp.status_code == 200:
                    pushed += 1
            except Exception:
                pass
        print(f"  Pushed {pushed}/{len(leads)} leads to Koyeb")
    else:
        print(f"\n[4/4] Skipping Koyeb push (no --koyeb URL set)")

    # Summary
    with_email = sum(1 for l in leads if l["email"])
    hot = sum(1 for l in leads if l["score"] < 20)
    warm = sum(1 for l in leads if 20 <= l["score"] < 40)

    print(f"\n{'='*60}")
    print(f"  SUMMARY")
    print(f"{'='*60}")
    print(f"  Total leads:   {len(leads)}")
    print(f"  With email:    {with_email}")
    print(f"  HOT leads:     {hot}")
    print(f"  WARM leads:    {warm}")
    print(f"  Saved to:      {output_file}")
    if koyeb_url:
        print(f"  Pushed to:     {koyeb_url}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
