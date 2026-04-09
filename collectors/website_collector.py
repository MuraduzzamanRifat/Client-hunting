"""Website email collector — uses Serper API for search, requests for scraping.

Works from anywhere (cloud or local). No browser needed.
Serper API handles search, requests scrapes the actual websites.
"""

import re
import random
import time
import hashlib
import logging
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote, urlparse

from config import (
    REQUEST_DELAY_MIN, REQUEST_DELAY_MAX, DAILY_COLLECT_LIMIT,
    SERPER_API_KEY,
)
from database import add_email, is_url_visited, mark_url_visited

log = logging.getLogger("outreach.web")

EMAIL_REGEX = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')

SKIP_EMAIL_DOMAINS = {
    'facebook.com', 'fb.com', 'instagram.com', 'example.com',
    'email.com', 'sentry.io', 'google.com', 'apple.com',
    'mozilla.org', 'w3.org', 'twitter.com', 'github.com',
    'linkedin.com', 'youtube.com', 'pinterest.com', 'tiktok.com',
    'wix.com', 'wordpress.com', 'squarespace.com', 'godaddy.com',
    'wixpress.com', 'sentry-next.wixpress.com',
    'freelancer.com', 'upwork.com', 'fiverr.com', 'truelancer.com',
    'freelancermap.com', 'guru.com', 'toptal.com', 'contra.com',
    'peopleperhour.com', 'designcrowd.com', '99designs.com',
    'dailyremote.com', 'crossover.com', 'turing.com', 'andela.com',
    'payoneer.com', 'paypal.com', 'stripe.com', 'wise.com',
    'indeed.com', 'glassdoor.com', 'careerjet.com', 'careerjet.com.bd',
    'goodfirms.co', 'clutch.co', 'designrush.com',
    'behance.net', 'dribbble.com', 'deviantart.com',
}

SKIP_EMAIL_PREFIXES = {
    'noreply', 'no-reply', 'support', 'info', 'admin', 'webmaster',
    'sales', 'help', 'contact', 'feedback', 'abuse', 'postmaster',
    'press', 'partner', 'billing', 'accounts', 'hello', 'team',
    'marketing', 'hr', 'careers', 'jobs', 'legal', 'privacy',
    'security', 'newsletter', 'payment', 'notifications',
}

SKIP_SITE_DOMAINS = {
    'facebook.com', 'instagram.com', 'linkedin.com', 'youtube.com',
    'twitter.com', 'reddit.com', 'wikipedia.org', 'pinterest.com',
    'tiktok.com', 'amazon.com', 'bing.com', 'google.com',
    'duckduckgo.com', 'yahoo.com', 'msn.com', 'microsoft.com',
    'upwork.com', 'fiverr.com', 'freelancer.com', 'guru.com',
    'toptal.com', 'truelancer.com', 'freelancermap.com',
    'peopleperhour.com', 'contra.com', 'designcrowd.com',
    'indeed.com', 'glassdoor.com', 'careerjet.com', 'careerjet.com.bd',
    'dailyremote.com', 'crossover.com', 'remoteok.com',
    'goodfirms.co', 'clutch.co', 'designrush.com', 'g2.com',
    'behance.net', 'dribbble.com', 'deviantart.com',
    'payoneer.com', 'paypal.com', 'wise.com',
    'quora.com', 'medium.com', 'about.me',
}

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}

SEARCH_QUERIES = [
    # Portfolio sites with emails
    'site:github.io Bangladesh freelancer email',
    'site:netlify.app Bangladesh developer portfolio',
    'site:vercel.app Bangladesh freelancer',
    'site:wixsite.com Bangladesh freelancer contact',
    'site:carrd.co Bangladesh freelancer',
    # Personal websites
    'Bangladesh freelancer portfolio "contact me" "gmail.com"',
    'Bangladesh web developer portfolio "@gmail.com"',
    'Bangladesh graphic designer portfolio email contact',
    'Bangladesh freelancer "hire me" "@gmail.com"',
    'Dhaka freelancer personal website email',
    'Bangladesh developer "about me" email contact',
    'Bangladesh freelancer website "get in touch"',
    '"freelancer" "Bangladesh" "email" portfolio',
    '"web developer" "Dhaka" portfolio contact email',
    '"graphic designer" "Bangladesh" portfolio email',
    '"WordPress developer" "Bangladesh" hire email',
    '"SEO expert" "Bangladesh" contact email',
    '"digital marketer" "Bangladesh" freelancer email',
    '"content writer" "Bangladesh" portfolio contact',
    '"app developer" "Bangladesh" contact email',
    # Agencies
    'web development company Bangladesh contact email',
    'IT company Dhaka contact email',
    'software house Bangladesh contact email',
    'digital marketing agency Bangladesh email',
    'outsourcing company Bangladesh contact',
    'design agency Dhaka email contact',
    'development agency Bangladesh team email',
    'Bangladesh IT firm contact email',
    # Freelancer directories
    'Bangladesh freelancer directory email list',
    'top freelancers Bangladesh contact website',
    '"available for hire" Bangladesh developer email',
    '"open to work" Bangladesh freelancer email',
    'Bangladesh remote developer contact email',
    '"hire me" developer Bangladesh email',
    'Bangladesh freelancer "reach me at" email',
]

CONTACT_PATHS = ['', '/contact', '/contact-us', '/about', '/about-us', '/team', '/hire-me', '/hire-us']


def extract_emails(text):
    found = EMAIL_REGEX.findall(text)
    cleaned = []
    for email in found:
        email = email.lower().strip()
        domain = email.split('@')[1]
        prefix = email.split('@')[0].split('+')[0]
        if (domain not in SKIP_EMAIL_DOMAINS and
                not domain.endswith(('.png', '.jpg', '.gif', '.svg', '.css', '.js')) and
                len(email) < 80 and
                prefix not in SKIP_EMAIL_PREFIXES):
            cleaned.append(email)
    return list(set(cleaned))


def search_serper(query):
    """Search via Serper.dev API — works from any server."""
    if not SERPER_API_KEY:
        log.warning("No SERPER_API_KEY — cannot search")
        return []

    try:
        resp = requests.post(
            'https://google.serper.dev/search',
            json={'q': query, 'num': 15},
            headers={'X-API-KEY': SERPER_API_KEY, 'Content-Type': 'application/json'},
            timeout=15
        )
        if resp.status_code != 200:
            log.warning(f"Serper error: {resp.status_code}")
            return []

        data = resp.json()
        urls = []
        for result in data.get('organic', []):
            url = result.get('link', '')
            if url:
                domain = urlparse(url).netloc.lower()
                if not any(skip in domain for skip in SKIP_SITE_DOMAINS):
                    urls.append(url.split('?')[0])
        return list(dict.fromkeys(urls))[:15]

    except Exception as e:
        log.warning(f"Serper error: {e}")
        return []


def scrape_site_emails(base_url):
    """Visit a site's key pages and extract emails."""
    all_emails = set()
    parsed = urlparse(base_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    session = requests.Session()

    for path in CONTACT_PATHS:
        url = base + path
        try:
            resp = session.get(url, headers=HEADERS, timeout=10, allow_redirects=True)
            if resp.status_code != 200:
                continue

            soup = BeautifulSoup(resp.text, 'html.parser')
            page_text = soup.get_text(separator=' ')
            all_emails.update(extract_emails(page_text))

            for a in soup.select('a[href^="mailto:"]'):
                href = a.get('href', '').replace('mailto:', '').split('?')[0].strip().lower()
                if href and '@' in href:
                    domain = href.split('@')[1]
                    if domain not in SKIP_EMAIL_DOMAINS:
                        all_emails.add(href)

        except Exception:
            continue

        time.sleep(random.uniform(0.3, 1.0))

    return list(all_emails)


def run_website_collector(progress_cb=None):
    """Collect emails using Serper API + requests. Works from anywhere."""
    total_collected = 0
    all_found_urls = set()

    if not SERPER_API_KEY:
        log.error("SERPER_API_KEY not set — cannot collect")
        return 0

    log.info("Website collector started (Serper API + requests)")

    random.shuffle(SEARCH_QUERIES)

    for idx, query in enumerate(SEARCH_QUERIES):
        if total_collected >= DAILY_COLLECT_LIMIT:
            break

        query_key = f"query:{hashlib.md5(query.encode()).hexdigest()}"
        if is_url_visited(query_key):
            continue

        log.info(f'Searching: "{query[:50]}"')
        if progress_cb:
            progress_cb(idx + 1, len(SEARCH_QUERIES), total_collected)

        urls = search_serper(query)

        new_urls = [u for u in urls if u not in all_found_urls]
        all_found_urls.update(new_urls)
        log.info(f"  Found {len(new_urls)} new sites")

        for site_url in new_urls:
            if total_collected >= DAILY_COLLECT_LIMIT:
                break

            domain = urlparse(site_url).netloc
            if is_url_visited(domain):
                continue
            if any(skip in domain for skip in SKIP_SITE_DOMAINS):
                continue

            log.info(f"  Scraping: {domain}")
            try:
                emails = scrape_site_emails(site_url)
                found = 0
                for email in emails:
                    if add_email(email=email, source='website', source_url=site_url):
                        found += 1
                        total_collected += 1
                        log.info(f"    [{total_collected}] {email}")
                mark_url_visited(domain, 'website', found)
            except Exception as e:
                log.warning(f"    Error: {e}")

            time.sleep(random.uniform(1, 3))

        mark_url_visited(query_key, 'search_query', 0)
        time.sleep(random.uniform(1, 2))

    log.info(f"Website collection done: {total_collected} new emails")
    return total_collected


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(message)s')
    run_website_collector()
