#!/usr/bin/env python3
"""
Full Pipeline — runs on your PC with proxies.

One command:
  python local_scraper.py "shopify fashion store" -l "New York" -n 50

Does everything:
  1. Scrapes Google Maps for businesses
  2. Visits each website → extracts email
  3. Audits website (chatbot, speed, automation)
  4. AI writes personalized first line per store
  5. Auto-creates chatbot demo per store
  6. Pushes everything to Koyeb dashboard
  7. Saves CSV locally
  8. Ready to send from Koyeb dashboard
"""

import sys
import os
import csv
import time
import argparse

sys.path.insert(0, os.path.dirname(__file__))

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

KOYEB_URL = os.getenv("KOYEB_URL", "https://tall-earwig-innohedge-20122b35.koyeb.app")
PROXY_FILE = os.path.join(os.path.dirname(__file__), "proxies.txt")


def main():
    parser = argparse.ArgumentParser(description="Full Lead Pipeline — Scrape → Email → Personalize → Demo → Push")
    parser.add_argument("query", help="Search query (e.g. 'shopify fashion store')")
    parser.add_argument("--location", "-l", default="", help="Location (e.g. 'New York')")
    parser.add_argument("--max", "-n", type=int, default=20, help="Max results")
    parser.add_argument("--method", "-m", choices=["direct", "outscraper", "auto"], default="auto")
    parser.add_argument("--proxy", "-p", default="", help="Proxy file (default: proxies.txt)")
    parser.add_argument("--koyeb", "-k", default="", help="Koyeb URL")
    parser.add_argument("--output", "-o", default="", help="Output CSV")
    parser.add_argument("--fast", action="store_true", help="Skip audit + demo creation (just scrape + email)")
    args = parser.parse_args()

    koyeb_url = args.koyeb or KOYEB_URL
    output_file = args.output or f"leads_{args.query.replace(' ', '_')[:30]}.csv"

    # Load proxies
    proxy_file = args.proxy or PROXY_FILE
    if os.path.exists(proxy_file):
        with open(proxy_file) as f:
            proxies = [l.strip() for l in f if l.strip() and not l.startswith("#")]
            os.environ["PROXY_LIST"] = ",".join(proxies)

    proxy_manager = ProxyManager()

    print(f"\n{'='*60}")
    print(f"  FULL PIPELINE")
    print(f"{'='*60}")
    print(f"  Query:    {args.query}")
    print(f"  Location: {args.location or '(worldwide)'}")
    print(f"  Max:      {args.max}")
    print(f"  Proxies:  {proxy_manager.count()} loaded")
    print(f"  Koyeb:    {koyeb_url}")
    print(f"{'='*60}\n")

    # ─── STEP 1: SCRAPE GOOGLE MAPS ───
    print("[1/6] Scraping Google Maps...")
    businesses = _scrape(args, proxy_manager)

    if not businesses:
        print("  No businesses found. Try different query/location.")
        return

    print(f"  Found {len(businesses)} businesses\n")

    # ─── STEP 2: EXTRACT EMAILS + AUDIT ───
    print(f"[2/6] Extracting emails + auditing websites...")
    leads = []
    for i, biz in enumerate(businesses, 1):
        lead = _process_business(i, len(businesses), biz, args)
        if lead:
            leads.append(lead)

    with_email = sum(1 for l in leads if l["email"])
    print(f"\n  Processed: {len(leads)} | With email: {with_email}\n")

    # ─── STEP 3: AI PERSONALIZE ───
    print("[3/6] AI personalizing emails...")
    leads_needing_personalization = [l for l in leads if l["email"] and not l.get("first_line")]
    if leads_needing_personalization:
        try:
            from personalizer.generator import generate_first_lines
            results = generate_first_lines(leads_needing_personalization)
            personalized = 0
            for lead in leads:
                if lead["domain"] in results and results[lead["domain"]]:
                    lead["first_line"] = results[lead["domain"]]
                    personalized += 1
            print(f"  Personalized {personalized} leads with AI\n")
        except Exception as e:
            print(f"  AI personalization failed: {e}")
            print(f"  Using audit-based lines instead\n")
    else:
        print("  All leads already have first lines or no emails\n")

    # ─── STEP 4: AUTO-CREATE DEMOS ───
    if not args.fast:
        print("[4/6] Creating chatbot demos...")
        demos_created = 0
        for lead in leads:
            if lead.get("website"):
                try:
                    from chatbot.auto_demo import auto_create_demo
                    demo_id = auto_create_demo(lead["store_name"], lead["domain"], lead["website"])
                    lead["demo_url"] = f"{koyeb_url}/demo?store={demo_id}"
                    demos_created += 1
                except Exception:
                    pass
        print(f"  Created {demos_created} demos\n")
    else:
        print("[4/6] Skipped (--fast mode)\n")

    # ─── STEP 5: SAVE CSV ───
    print(f"[5/6] Saving to {output_file}...")
    fieldnames = ["store_name", "email", "phone", "website", "address",
                   "rating", "score", "has_chatbot", "niche", "domain",
                   "first_line", "demo_url"]
    with open(output_file, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for lead in leads:
            writer.writerow({k: lead.get(k, "") for k in fieldnames})
    print(f"  Saved {len(leads)} leads\n")

    # ─── STEP 6: PUSH TO KOYEB ───
    print(f"[6/6] Pushing to Koyeb dashboard...")
    pushed = _push_to_koyeb(leads, koyeb_url)
    print(f"  Pushed {pushed}/{len(leads)} leads\n")

    # ─── SUMMARY ───
    hot = sum(1 for l in leads if l.get("score", 50) < 20)
    warm = sum(1 for l in leads if 20 <= l.get("score", 50) < 40)
    with_demo = sum(1 for l in leads if l.get("demo_url"))

    print(f"{'='*60}")
    print(f"  DONE")
    print(f"{'='*60}")
    print(f"  Total leads:    {len(leads)}")
    print(f"  With email:     {with_email}")
    print(f"  HOT leads:      {hot}")
    print(f"  WARM leads:     {warm}")
    print(f"  With demo:      {with_demo}")
    print(f"  Pushed to:      {koyeb_url}")
    print(f"  CSV:            {output_file}")
    print(f"{'='*60}")
    print(f"\n  Next: Go to {koyeb_url}/send to send emails")
    print(f"  Or:   Go to {koyeb_url}/leads to review leads first\n")


def _scrape(args, proxy_manager):
    """Step 1: Scrape Google Maps."""
    businesses = []

    if args.method == "outscraper" or (args.method == "auto" and os.getenv("OUTSCRAPER_API_KEY")):
        print("  Method: Outscraper API")
        try:
            businesses = search_google_maps(args.query, location=args.location, num_results=args.max)
        except Exception as e:
            print(f"  Outscraper failed: {e}")

    if not businesses:
        print("  Method: Direct scraping with proxies")
        businesses = search_maps_direct(args.query, location=args.location,
                                         num_results=args.max, proxy_manager=proxy_manager)

    return businesses


def _process_business(i, total, biz, args):
    """Step 2: Process a single business — email + audit."""
    name = biz.get("title", "Unknown")
    website = biz.get("website", "")
    email = biz.get("email", "")
    domain = biz.get("domain", "")

    if not domain:
        domain = name.lower().replace(" ", "-").replace(".", "").replace("'", "")[:50]

    # Extract email
    if not email and website:
        try:
            email = extract_email_from_website(website)
        except Exception:
            pass

    # Audit
    audit = {"score": 50, "has_chatbot": False, "has_automation": False,
             "load_time": None, "personal_line": ""}

    if website and not args.fast:
        try:
            audit = audit_website(website)
        except Exception:
            pass
    elif not website:
        audit["score"] = 5
        audit["personal_line"] = f"Noticed {name} doesn't have a website yet."

    # Status
    tags = []
    if email:
        tags.append(f"email:{email}")
    if biz.get("phone"):
        tags.append(f"ph:{biz['phone'][:15]}")
    if audit.get("score", 50) < 20:
        tags.append("HOT")
    elif audit.get("score", 50) < 40:
        tags.append("WARM")
    if not audit.get("has_chatbot") and website:
        tags.append("no-chatbot")

    tag_str = f" | {' | '.join(tags)}" if tags else ""
    print(f"  [{i}/{total}] {name[:35]}{tag_str}")

    return {
        "domain": domain,
        "store_name": name,
        "email": email or "",
        "phone": biz.get("phone", ""),
        "website": website,
        "address": biz.get("address", ""),
        "rating": biz.get("rating", ""),
        "niche": "",
        "source": "google_maps_local",
        "score": audit.get("score", 50),
        "has_chatbot": 1 if audit.get("has_chatbot") else 0,
        "has_automation": 1 if audit.get("has_automation") else 0,
        "first_line": audit.get("personal_line", ""),
        "demo_url": "",
    }


def _push_to_koyeb(leads, koyeb_url):
    """Step 6: Push leads to Koyeb dashboard."""
    import requests
    pushed = 0
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
    return pushed


if __name__ == "__main__":
    main()
