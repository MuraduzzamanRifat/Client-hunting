"""Facebook email collector — fully automated with auto-login.

Searches for Bangladeshi freelancers across posts, groups, and profiles.
Auto-logs in if session expired. Recovers from errors automatically.
"""

import re
import random
import asyncio
import logging
from urllib.parse import quote
from playwright.async_api import async_playwright

from config import (
    SCROLL_PAUSE_MIN, SCROLL_PAUSE_MAX,
    MAX_SCROLLS, SCROLL_DISTANCE_MIN, SCROLL_DISTANCE_MAX,
    REQUEST_DELAY_MIN, REQUEST_DELAY_MAX, DAILY_COLLECT_LIMIT,
)
from database import add_email, is_url_visited, mark_url_visited
from browser import get_browser_context, auto_login_facebook

log = logging.getLogger("outreach.fb")

EMAIL_REGEX = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')
SKIP_DOMAINS = {
    'facebook.com', 'fb.com', 'instagram.com', 'example.com',
    'email.com', 'sentry.io', 'google.com', 'apple.com',
    'mozilla.org', 'w3.org', 'twitter.com', 'github.com',
}

SEARCH_QUERIES = [
    "freelancer Bangladesh Upwork email",
    "Bangladeshi freelancer hire me",
    "Upwork freelancer Bangladesh contact",
    "freelancer BD available for work",
    "Bangladesh Fiverr freelancer email",
    "I am freelancer Bangladesh",
    "hire me Upwork Bangladesh",
    "freelancer looking for work Bangladesh",
    "Bangladeshi web developer freelancer",
    "Bangladesh graphic designer freelancer",
    "Upwork profile review Bangladesh",
    "new freelancer Bangladesh help",
    "freelancer community Bangladesh",
    "available for hire Bangladesh Upwork",
    "Upwork beginner Bangladesh",
    "freelancing Bangladesh contact",
    "marketplace freelancer BD",
]

GROUP_SEARCHES = [
    "Bangladeshi Freelancers",
    "Upwork Bangladesh",
    "Freelancing Bangladesh",
    "Fiverr Bangladesh",
    "Bangladesh Freelancer Community",
    "Digital Marketing Bangladesh Freelancer",
    "Web Developer Bangladesh",
    "Freelancer Income Bangladesh",
    "Outsourcing Bangladesh",
]

PEOPLE_QUERIES = [
    "freelancer Bangladesh",
    "Upwork freelancer Dhaka",
    "freelancer Chittagong",
    "web developer Bangladesh freelance",
]


def extract_emails(text):
    found = EMAIL_REGEX.findall(text)
    cleaned = []
    for email in found:
        domain = email.split('@')[1].lower()
        if domain not in SKIP_DOMAINS and not domain.endswith(('.png', '.jpg', '.gif', '.svg')):
            cleaned.append(email.lower())
    return list(set(cleaned))


async def safe_goto(page, url, retries=2):
    """Navigate to URL with retry on failure."""
    for attempt in range(retries):
        try:
            await page.goto(url, wait_until='domcontentloaded', timeout=30000)
            await asyncio.sleep(random.uniform(2, 4))
            return True
        except Exception as e:
            log.warning(f"Navigation failed (attempt {attempt+1}): {e}")
            if attempt < retries - 1:
                await asyncio.sleep(3)
    return False


async def scroll_and_collect(page, source_url, total_collected, limit):
    """Scroll a page and extract emails from visible content."""
    seen_emails = set()
    collected = 0

    for scroll_num in range(MAX_SCROLLS):
        if total_collected + collected >= limit:
            break

        try:
            body_text = await page.inner_text('body')
        except Exception:
            break

        emails = extract_emails(body_text)
        for email in emails:
            if email not in seen_emails:
                seen_emails.add(email)
                is_new = add_email(email=email, source='facebook', source_url=source_url)
                if is_new:
                    collected += 1
                    log.info(f"[{total_collected + collected}] {email}")

        scroll_dist = random.randint(SCROLL_DISTANCE_MIN, SCROLL_DISTANCE_MAX)
        await page.evaluate(f'window.scrollBy(0, {scroll_dist})')
        await asyncio.sleep(random.uniform(SCROLL_PAUSE_MIN, SCROLL_PAUSE_MAX))

        if scroll_num % 8 == 0 and scroll_num > 0:
            await asyncio.sleep(random.uniform(4, 8))

    return collected


async def collect_from_facebook():
    """Search Facebook for Bangladeshi freelancers and collect emails."""
    total_collected = 0

    async with async_playwright() as p:
        try:
            context = await get_browser_context(p)
        except Exception as e:
            log.error(f"Browser launch failed: {e}")
            return 0

        page = context.pages[0] if context.pages else await context.new_page()

        # Auto-login
        if not await auto_login_facebook(page):
            log.error("Facebook login failed — skipping")
            await context.close()
            return 0

        log.info("Facebook ready. Starting collection...")

        # --- Phase 1: Search posts ---
        random.shuffle(SEARCH_QUERIES)
        for query in SEARCH_QUERIES:
            if total_collected >= DAILY_COLLECT_LIMIT:
                break

            log.info(f'Searching posts: "{query}"')
            encoded = quote(query)
            url = f'https://www.facebook.com/search/posts/?q={encoded}'

            if not await safe_goto(page, url):
                continue

            try:
                collected = await scroll_and_collect(page, url, total_collected, DAILY_COLLECT_LIMIT)
                total_collected += collected
            except Exception as e:
                log.warning(f"Error collecting from posts: {e}")

            await asyncio.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

        # --- Phase 2: Groups ---
        random.shuffle(GROUP_SEARCHES)
        for group_query in GROUP_SEARCHES:
            if total_collected >= DAILY_COLLECT_LIMIT:
                break

            log.info(f'Searching groups: "{group_query}"')
            encoded = quote(group_query)

            if not await safe_goto(page, f'https://www.facebook.com/search/groups/?q={encoded}'):
                continue

            try:
                group_links = await page.evaluate("""
                    () => {
                        const links = document.querySelectorAll('a[href*="/groups/"]');
                        const urls = new Set();
                        for (const a of links) {
                            const href = a.href;
                            if (href.includes('/groups/') && !href.includes('/search/') &&
                                !href.includes('?') && !href.includes('/groups/feed')) {
                                urls.add(href.split('?')[0]);
                            }
                        }
                        return [...urls].slice(0, 5);
                    }
                """)

                for group_url in group_links:
                    if total_collected >= DAILY_COLLECT_LIMIT:
                        break

                    if is_url_visited(group_url):
                        log.info(f"  Skipping (already visited): {group_url}")
                        continue

                    log.info(f"  Entering: {group_url}")
                    if not await safe_goto(page, group_url):
                        continue

                    try:
                        collected = await scroll_and_collect(
                            page, group_url, total_collected, DAILY_COLLECT_LIMIT
                        )
                        total_collected += collected
                        mark_url_visited(group_url, 'facebook', collected)
                    except Exception as e:
                        log.warning(f"Error in group: {e}")

                    await asyncio.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

            except Exception as e:
                log.warning(f"Error searching groups: {e}")

        # --- Phase 3: People profiles ---
        random.shuffle(PEOPLE_QUERIES)
        for query in PEOPLE_QUERIES:
            if total_collected >= DAILY_COLLECT_LIMIT:
                break

            log.info(f'Searching people: "{query}"')
            encoded = quote(query)

            if not await safe_goto(page, f'https://www.facebook.com/search/people/?q={encoded}'):
                continue

            try:
                profile_links = await page.evaluate("""
                    () => {
                        const links = document.querySelectorAll('a[href*="facebook.com/"]');
                        const urls = new Set();
                        for (const a of links) {
                            const href = a.href;
                            if (href.match(/facebook\\.com\\/[a-zA-Z0-9.]+\\/?$/) &&
                                !href.includes('/search/') && !href.includes('/groups/') &&
                                !href.includes('/pages/')) {
                                urls.add(href.split('?')[0]);
                            }
                        }
                        return [...urls].slice(0, 15);
                    }
                """)

                for profile_url in profile_links:
                    if total_collected >= DAILY_COLLECT_LIMIT:
                        break

                    if is_url_visited(profile_url):
                        continue

                    about_url = profile_url.rstrip('/') + '/about'
                    if not await safe_goto(page, about_url):
                        continue

                    try:
                        body_text = await page.inner_text('body')
                        emails = extract_emails(body_text)

                        name = None
                        try:
                            h1 = await page.query_selector('h1')
                            if h1:
                                name = await h1.inner_text()
                        except Exception:
                            pass

                        found = 0
                        for email in emails:
                            is_new = add_email(
                                email=email, name=name,
                                source='facebook', source_url=profile_url
                            )
                            if is_new:
                                found += 1
                                total_collected += 1
                                log.info(f"[{total_collected}] {email} ({name or 'N/A'})")
                        mark_url_visited(profile_url, 'facebook', found)
                    except Exception:
                        continue

                    await asyncio.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

            except Exception as e:
                log.warning(f"Error searching people: {e}")

        await context.close()

    log.info(f"Facebook done: {total_collected} new emails")
    return total_collected


def run_facebook_collector():
    return asyncio.run(collect_from_facebook())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(message)s')
    run_facebook_collector()
