"""Website email collector — Bing/Google search via Playwright, site scraping via requests.

Uses Playwright ONLY for search (search engines need JS).
Uses requests for actual website scraping (fast, no browser needed).
No login required for any of this.
"""

import re
import random
import time
import hashlib
import asyncio
import logging
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote, urlparse

try:
    from playwright.async_api import async_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

from config import (
    REQUEST_DELAY_MIN, REQUEST_DELAY_MAX, DAILY_COLLECT_LIMIT,
    BROWSER_DATA_DIR, SLOW_MO,
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
    # Big platforms — these are not freelancers
    'freelancer.com', 'upwork.com', 'fiverr.com', 'truelancer.com',
    'freelancermap.com', 'guru.com', 'toptal.com', 'contra.com',
    'peopleperhour.com', 'designcrowd.com', '99designs.com',
    'dailyremote.com', 'crossover.com', 'turing.com', 'andela.com',
    'payoneer.com', 'paypal.com', 'stripe.com', 'wise.com',
    'indeed.com', 'glassdoor.com', 'careerjet.com', 'careerjet.com.bd',
    'goodfirms.co', 'clutch.co', 'designrush.com',
    'behance.net', 'dribbble.com', 'deviantart.com',
}

# Prefixes that indicate generic/support emails, not personal
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
    # Skip big freelancing platforms (no personal emails there)
    'upwork.com', 'fiverr.com', 'freelancer.com', 'guru.com',
    'toptal.com', 'truelancer.com', 'freelancermap.com',
    'peopleperhour.com', 'contra.com', 'designcrowd.com',
    # Skip job boards
    'indeed.com', 'glassdoor.com', 'careerjet.com', 'careerjet.com.bd',
    'dailyremote.com', 'crossover.com', 'remoteok.com',
    # Skip review sites
    'goodfirms.co', 'clutch.co', 'designrush.com', 'g2.com',
    # Skip portfolio platforms
    'behance.net', 'dribbble.com', 'deviantart.com',
    # Skip payment platforms
    'payoneer.com', 'paypal.com', 'wise.com',
}

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}

SEARCH_QUERIES = [
    # Upwork freelancers with personal sites
    '"Upwork" "top rated" Bangladesh portfolio site',
    '"Upwork" freelancer Bangladesh website contact',
    '"Upwork profile" Bangladesh web developer',
    '"available on Upwork" Bangladesh email',
    '"hire me on Upwork" Bangladesh website',
    '"Upwork" "rising talent" Bangladesh portfolio',
    '"Upwork" freelancer Dhaka personal website',
    '"Upwork" graphic designer Bangladesh portfolio',
    '"Upwork" WordPress developer Bangladesh site',
    '"Upwork" SEO expert Bangladesh contact',
    '"Upwork" content writer Bangladesh website',
    '"Upwork" mobile developer Bangladesh portfolio',
    '"Upwork" virtual assistant Bangladesh contact',
    '"Upwork" video editor Bangladesh website',
    '"Upwork" data entry Bangladesh portfolio',
    '"Upwork" UI UX designer Bangladesh site',
    'Upwork freelancer Bangladesh personal website email',
    'top Upwork freelancer Bangladesh contact',
    'Upwork expert Bangladesh hire website',
    'Upwork Bangladesh freelancer portfolio contact',

    # Agencies that use Upwork for client work
    '"Upwork agency" Bangladesh contact',
    '"Upwork" agency Dhaka website email',
    '"Upwork" outsourcing agency Bangladesh',
    '"Upwork" development team Bangladesh contact',
    '"Upwork" web development agency Bangladesh',
    '"Upwork" digital marketing agency Bangladesh',
    'Upwork agency Bangladesh website contact email',
    'Bangladesh agency Upwork profile website',
    'Upwork certified agency Bangladesh',
    'software agency Bangladesh Upwork contact',

    # Freelancers who mention Upwork on their sites
    'site:github.io Upwork Bangladesh',
    'site:netlify.app Upwork Bangladesh freelancer',
    '"I am on Upwork" Bangladesh',
    '"find me on Upwork" Bangladesh',
    '"Upwork profile" "contact me" Bangladesh',
]

CONTACT_PATHS = ['', '/contact', '/contact-us', '/about', '/about-us', '/team', '/hire-me', '/hire-us']


def extract_emails(text):
    found = EMAIL_REGEX.findall(text)
    cleaned = []
    for email in found:
        email = email.lower().strip()
        domain = email.split('@')[1]
        prefix = email.split('@')[0].split('+')[0]  # strip +tags
        if (domain not in SKIP_EMAIL_DOMAINS and
                not domain.endswith(('.png', '.jpg', '.gif', '.svg', '.css', '.js')) and
                len(email) < 80 and
                prefix not in SKIP_EMAIL_PREFIXES):
            cleaned.append(email)
    return list(set(cleaned))


def scrape_site_emails(base_url):
    """Visit a site's key pages via requests and extract emails."""
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

            # mailto links
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


async def search_bing(page, query):
    """Search Bing via Playwright and return result URLs."""
    try:
        await page.goto(f'https://www.bing.com/search?q={quote(query)}&count=20',
                       wait_until='domcontentloaded', timeout=15000)
        await asyncio.sleep(random.uniform(2, 4))

        urls = await page.evaluate("""
            () => {
                const results = document.querySelectorAll('#b_results li.b_algo h2 a, #b_results .b_algo a');
                const urls = new Set();
                for (const a of results) {
                    const href = a.href;
                    if (href && href.startsWith('http') &&
                        !href.includes('bing.com') && !href.includes('microsoft.com') &&
                        !href.includes('msn.com')) {
                        urls.add(href.split('?')[0]);
                    }
                }
                return [...urls].slice(0, 15);
            }
        """)
        return urls
    except Exception as e:
        log.warning(f"Bing search error: {e}")
        return []


async def search_google(page, query):
    """Search Google via Playwright and return result URLs."""
    try:
        await page.goto(f'https://www.google.com/search?q={quote(query)}&num=15',
                       wait_until='domcontentloaded', timeout=15000)
        await asyncio.sleep(random.uniform(2, 4))

        urls = await page.evaluate("""
            () => {
                const results = document.querySelectorAll('#search a[href^="http"], .g a[href^="http"]');
                const urls = new Set();
                const skip = ['google.com', 'youtube.com', 'facebook.com', 'instagram.com',
                              'linkedin.com', 'twitter.com', 'reddit.com', 'wikipedia.org',
                              'amazon.com', 'pinterest.com', 'tiktok.com'];
                for (const a of results) {
                    const href = a.href;
                    if (href && !skip.some(s => href.includes(s))) {
                        urls.add(href.split('?')[0]);
                    }
                }
                return [...urls].slice(0, 15);
            }
        """)
        return urls
    except Exception as e:
        log.warning(f"Google search error: {e}")
        return []


def search_requests(query):
    """Search via requests (no browser). Tries DuckDuckGo lite + Bing."""
    headers = HEADERS.copy()
    urls = []

    # Try DuckDuckGo lite (HTML, no JS needed)
    try:
        resp = requests.get(f'https://lite.duckduckgo.com/lite/?q={quote(query)}',
                           headers=headers, timeout=15)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'html.parser')
            for a in soup.select('a.result-link, td a[href^="http"]'):
                href = a.get('href', '')
                if href.startswith('http') and not any(s in href for s in SKIP_SITE_DOMAINS):
                    urls.append(href.split('?')[0])
    except Exception:
        pass

    if len(urls) < 3:
        # Fallback: Bing with session
        try:
            session = requests.Session()
            session.get('https://www.bing.com/', headers=headers, timeout=10)
            resp = session.get(f'https://www.bing.com/search?q={quote(query)}&count=20',
                              headers=headers, timeout=15)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, 'html.parser')
                for a in soup.find_all('a', href=True):
                    href = a['href']
                    if (href.startswith('http') and
                            not any(s in href for s in SKIP_SITE_DOMAINS) and
                            'bing.com' not in href and 'microsoft.com' not in href):
                        urls.append(href.split('?')[0])
        except Exception:
            pass

    return list(dict.fromkeys(urls))[:15]


async def collect_playwright(progress_cb=None):
    """Collect using Playwright for search (local PC with browser)."""
    total_collected = 0
    all_found_urls = set()

    async with async_playwright() as p:
        import os
        os.makedirs(BROWSER_DATA_DIR, exist_ok=True)

        context = await p.chromium.launch_persistent_context(
            user_data_dir=BROWSER_DATA_DIR,
            headless=True, slow_mo=50,
            viewport={'width': 1366, 'height': 900},
            user_agent=HEADERS['User-Agent'],
            args=['--disable-blink-features=AutomationControlled'],
            ignore_default_args=['--enable-automation'],
        )
        page = context.pages[0] if context.pages else await context.new_page()

        total_collected = await _run_queries(page, all_found_urls, progress_cb,
                                             use_playwright=True)
        await context.close()

    return total_collected


async def _run_queries(page, all_found_urls, progress_cb, use_playwright=False):
    """Core query loop — works with Playwright page or requests."""
    total_collected = 0

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

        # Search
        if use_playwright and page:
            if random.random() < 0.5:
                urls = await search_bing(page, query)
                if not urls:
                    urls = await search_google(page, query)
            else:
                urls = await search_google(page, query)
                if not urls:
                    urls = await search_bing(page, query)
        else:
            urls = search_requests(query)

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
        if use_playwright:
            await asyncio.sleep(random.uniform(3, 6))
        else:
            time.sleep(random.uniform(2, 4))

    log.info(f"Website collection done: {total_collected} new emails")
    return total_collected


def collect_requests_only(progress_cb=None):
    """Collect using requests only (no browser — for cloud/Koyeb)."""
    all_found_urls = set()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(
        _run_queries(None, all_found_urls, progress_cb, use_playwright=False)
    )
    loop.close()
    return result


def run_website_collector(progress_cb=None):
    if HAS_PLAYWRIGHT:
        try:
            return asyncio.run(collect_playwright(progress_cb))
        except Exception as e:
            log.warning(f"Playwright failed, falling back to requests: {e}")
    return collect_requests_only(progress_cb)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(message)s')
    run_website_collector()
