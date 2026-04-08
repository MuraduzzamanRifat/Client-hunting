"""Website email collector — searches for freelancer & agency websites.

Searches DuckDuckGo for Bangladeshi freelancer personal sites and agencies,
visits each site, extracts emails from contact/about pages and footers.
No login needed. Fully automated.
"""

import re
import random
import asyncio
import logging
from urllib.parse import quote, urljoin, urlparse
from playwright.async_api import async_playwright

from config import (
    SCROLL_PAUSE_MIN, SCROLL_PAUSE_MAX,
    SCROLL_DISTANCE_MIN, SCROLL_DISTANCE_MAX,
    REQUEST_DELAY_MIN, REQUEST_DELAY_MAX, DAILY_COLLECT_LIMIT,
)
from database import add_email, is_url_visited, mark_url_visited
from browser import get_browser_context

log = logging.getLogger("outreach.web")

EMAIL_REGEX = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')
SKIP_DOMAINS = {
    'facebook.com', 'fb.com', 'instagram.com', 'example.com',
    'email.com', 'sentry.io', 'google.com', 'apple.com',
    'mozilla.org', 'w3.org', 'twitter.com', 'github.com',
    'linkedin.com', 'youtube.com', 'pinterest.com', 'tiktok.com',
    'wix.com', 'wordpress.com', 'squarespace.com', 'godaddy.com',
    'duckduckgo.com', 'bing.com', 'yahoo.com',
}

# Skip image/file emails
SKIP_EXTENSIONS = ('.png', '.jpg', '.gif', '.svg', '.jpeg', '.webp', '.css', '.js')

# --- Search queries to find freelancer/agency websites ---
SEARCH_QUERIES = [
    # Freelancer personal websites
    'freelancer Bangladesh portfolio website contact',
    'Upwork freelancer Bangladesh personal website',
    'freelancer Dhaka portfolio site email',
    'web developer Bangladesh freelancer contact',
    'graphic designer Bangladesh freelancer website',
    'Bangladesh freelancer portfolio contact me',
    'hire freelancer Bangladesh website',
    'freelance developer Bangladesh contact email',
    'Bangladeshi freelancer personal site',
    'top freelancer Bangladesh website',
    'SEO expert Bangladesh freelancer contact',
    'digital marketer Bangladesh freelancer website',
    'content writer Bangladesh freelancer email',
    'mobile app developer Bangladesh contact',
    'WordPress developer Bangladesh freelancer',
    'freelancer Bangladesh available for hire',
    'virtual assistant Bangladesh contact email',
    'video editor Bangladesh freelancer website',
    'UI UX designer Bangladesh contact',
    'data entry freelancer Bangladesh email',

    # Agency websites
    'IT agency Bangladesh contact',
    'web development agency Bangladesh',
    'digital marketing agency Dhaka contact',
    'outsourcing company Bangladesh email',
    'software company Bangladesh contact us',
    'freelancing agency Bangladesh',
    'design agency Dhaka email',
    'development agency Bangladesh contact',
    'IT outsourcing Bangladesh agency',
    'creative agency Bangladesh email',
    'marketing agency Chittagong contact',
    'Bangladeshi software house contact',
    'web agency Bangladesh team',
    'remote agency Bangladesh hire',
    'small IT firm Bangladesh contact email',
]

# Pages on a site most likely to have emails
CONTACT_PATHS = [
    '/contact', '/contact-us', '/contact.html', '/contactus',
    '/about', '/about-us', '/about.html', '/aboutus',
    '/team', '/our-team',
    '/hire', '/hire-us', '/hire-me',
    '',  # homepage itself
]


def extract_emails(text):
    """Extract valid emails, skip junk domains."""
    found = EMAIL_REGEX.findall(text)
    cleaned = []
    for email in found:
        email = email.lower().strip()
        domain = email.split('@')[1]
        if (domain not in SKIP_DOMAINS and
                not domain.endswith(SKIP_EXTENSIONS) and
                len(email) < 80):
            cleaned.append(email)
    return list(set(cleaned))


async def safe_goto(page, url, timeout=20000):
    """Navigate with error handling."""
    try:
        await page.goto(url, wait_until='domcontentloaded', timeout=timeout)
        await asyncio.sleep(random.uniform(1, 3))
        return True
    except Exception:
        return False


async def extract_emails_from_site(page, site_url):
    """Visit a site and its contact/about pages, extract all emails."""
    all_emails = set()
    base = f"{urlparse(site_url).scheme}://{urlparse(site_url).netloc}"

    for path in CONTACT_PATHS:
        url = base + path
        if not await safe_goto(page, url):
            continue

        try:
            # Scroll down to load lazy content / reveal footer
            for _ in range(3):
                dist = random.randint(SCROLL_DISTANCE_MIN, SCROLL_DISTANCE_MAX)
                await page.evaluate(f'window.scrollBy(0, {dist})')
                await asyncio.sleep(random.uniform(0.5, 1.5))

            body = await page.inner_text('body')
            emails = extract_emails(body)
            all_emails.update(emails)

            # Also check mailto links
            try:
                mailto_links = await page.evaluate("""
                    () => {
                        const links = document.querySelectorAll('a[href^="mailto:"]');
                        return [...links].map(a => a.href.replace('mailto:', '').split('?')[0]);
                    }
                """)
                for m in mailto_links:
                    m = m.lower().strip()
                    if m and '@' in m:
                        domain = m.split('@')[1]
                        if domain not in SKIP_DOMAINS:
                            all_emails.add(m)
            except:
                pass

        except:
            continue

        # Small delay between pages on same site
        await asyncio.sleep(random.uniform(1, 2))

    return list(all_emails)


async def search_duckduckgo(page, query):
    """Search DuckDuckGo and return result URLs."""
    encoded = quote(query)
    url = f'https://duckduckgo.com/?q={encoded}'

    if not await safe_goto(page, url, timeout=15000):
        return []

    await asyncio.sleep(random.uniform(2, 4))

    # Extract result links
    try:
        links = await page.evaluate("""
            () => {
                const results = document.querySelectorAll('a[data-testid="result-title-a"], article a[href]');
                const urls = [];
                for (const a of results) {
                    const href = a.href;
                    if (href && href.startsWith('http') &&
                        !href.includes('duckduckgo.com') &&
                        !href.includes('facebook.com') &&
                        !href.includes('instagram.com') &&
                        !href.includes('linkedin.com') &&
                        !href.includes('youtube.com') &&
                        !href.includes('twitter.com') &&
                        !href.includes('wikipedia.org') &&
                        !href.includes('reddit.com')) {
                        urls.push(href.split('?')[0]);
                    }
                }
                return [...new Set(urls)].slice(0, 10);
            }
        """)
        return links
    except:
        return []


async def collect_from_websites():
    """Search for freelancer/agency websites and extract emails."""
    total_collected = 0

    async with async_playwright() as p:
        try:
            context = await get_browser_context(p)
        except Exception as e:
            log.error(f"Browser launch failed: {e}")
            return 0

        page = context.pages[0] if context.pages else await context.new_page()

        log.info("Website collector started. Searching for freelancer & agency sites...")

        random.shuffle(SEARCH_QUERIES)

        for query in SEARCH_QUERIES:
            if total_collected >= DAILY_COLLECT_LIMIT:
                break

            log.info(f'Searching: "{query}"')

            try:
                site_urls = await search_duckduckgo(page, query)
                log.info(f"  Found {len(site_urls)} sites")

                for site_url in site_urls:
                    if total_collected >= DAILY_COLLECT_LIMIT:
                        break

                    # Skip already visited
                    domain = urlparse(site_url).netloc
                    if is_url_visited(domain):
                        continue

                    log.info(f"  Visiting: {site_url}")

                    try:
                        emails = await extract_emails_from_site(page, site_url)

                        for email in emails:
                            is_new = add_email(
                                email=email,
                                source='website',
                                source_url=site_url
                            )
                            if is_new:
                                total_collected += 1
                                log.info(f"    [{total_collected}] {email}")

                        mark_url_visited(domain, 'website', len(emails))

                    except Exception as e:
                        log.warning(f"    Error on {site_url}: {e}")

                    await asyncio.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

            except Exception as e:
                log.warning(f"  Search error: {e}")

            # Delay between searches
            await asyncio.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

        await context.close()

    log.info(f"Website collection done: {total_collected} new emails")
    return total_collected


def run_website_collector():
    return asyncio.run(collect_from_websites())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(message)s')
    run_website_collector()
