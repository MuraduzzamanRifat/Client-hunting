"""Instagram email collector — fully automated with auto-login.

Searches hashtags and profiles for Bangladeshi freelancers.
Auto-logs in if session expired. Recovers from errors automatically.
"""

import re
import random
import asyncio
import logging
from playwright.async_api import async_playwright

from config import (
    SCROLL_PAUSE_MIN, SCROLL_PAUSE_MAX,
    MAX_SCROLLS, SCROLL_DISTANCE_MIN, SCROLL_DISTANCE_MAX,
    REQUEST_DELAY_MIN, REQUEST_DELAY_MAX, DAILY_COLLECT_LIMIT,
)
from database import add_email, is_url_visited, mark_url_visited
from browser import get_browser_context, auto_login_instagram

log = logging.getLogger("outreach.ig")

EMAIL_REGEX = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')
SKIP_DOMAINS = {
    'instagram.com', 'facebook.com', 'example.com', 'email.com',
    'sentry.io', 'google.com', 'apple.com', 'mozilla.org', 'w3.org',
    'twitter.com', 'github.com',
}

HASHTAGS = [
    "bangladeshifreelancer",
    "freelancerbangladesh",
    "upworkbangladesh",
    "freelancingbd",
    "bdfreelancer",
    "freelancerbd",
    "upworkfreelancer",
    "fiverrseller",
    "freelancerlife",
    "upworktips",
    "freelancinglife",
    "hireme",
    "availableforwork",
    "freelancewebdeveloper",
    "freelancegraphicdesigner",
    "remoteworker",
    "digitalmarketingbd",
    "outsourcingbd",
    "upworkproposal",
    "freelancersofbangladesh",
    "dhakafreelancer",
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
    """Navigate with retry."""
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


async def visit_profile(page, profile_path, total_collected, visited):
    """Visit a profile, extract email from bio."""
    if profile_path in visited:
        return 0
    visited.add(profile_path)

    collected = 0
    profile_url = f'https://www.instagram.com{profile_path}/'

    # Skip if already visited in a previous session
    if is_url_visited(profile_url):
        return 0

    if not await safe_goto(page, profile_url):
        return 0

    try:
        bio_text = ""

        # Get header/bio
        try:
            header = await page.query_selector('header')
            if header:
                bio_text = await header.inner_text()
        except Exception:
            pass

        # Page text (limited)
        try:
            page_text = await page.inner_text('body')
            bio_text += " " + page_text[:2000]
        except Exception:
            pass

        # Check mailto links (business profiles)
        try:
            email_btn = page.locator('a[href^="mailto:"]')
            if await email_btn.count() > 0:
                mailto = await email_btn.first.get_attribute('href')
                if mailto:
                    bio_text += " " + mailto.replace('mailto:', '')
        except Exception:
            pass

        # Get name
        name = None
        try:
            name_el = await page.query_selector('header h2, header span')
            if name_el:
                name = (await name_el.inner_text()).strip()
        except Exception:
            pass

        emails = extract_emails(bio_text)
        for email in emails:
            is_new = add_email(
                email=email, name=name,
                source='instagram', source_url=profile_url,
                bio=bio_text[:500]
            )
            if is_new:
                collected += 1
                log.info(f"[{total_collected + collected}] {email} ({name or profile_path})")

        mark_url_visited(profile_url, 'instagram', collected)

    except Exception as e:
        log.warning(f"Error on profile {profile_path}: {e}")

    return collected


async def get_profile_links(page):
    """Extract profile links from current page."""
    try:
        return await page.evaluate("""
            () => {
                const anchors = document.querySelectorAll('a[href^="/"]');
                const profiles = new Set();
                for (const a of anchors) {
                    const href = a.getAttribute('href');
                    if (href && href.match(/^\\/[a-zA-Z0-9_.]+\\/?$/) &&
                        !href.includes('/explore') && !href.includes('/p/') &&
                        !href.includes('/reels/') && !href.includes('/stories/') &&
                        !href.includes('/accounts/') && !href.includes('/direct/') &&
                        !href.includes('/tags/')) {
                        profiles.add(href.replace(/\\/$/, ''));
                    }
                }
                return [...profiles];
            }
        """)
    except Exception:
        return []


async def collect_from_instagram():
    """Search Instagram for Bangladeshi freelancers and collect emails."""
    total_collected = 0

    async with async_playwright() as p:
        try:
            context = await get_browser_context(p)
        except Exception as e:
            log.error(f"Browser launch failed: {e}")
            return 0

        page = context.pages[0] if context.pages else await context.new_page()

        # Auto-login
        if not await auto_login_instagram(page):
            log.error("Instagram login failed — skipping")
            await context.close()
            return 0

        log.info("Instagram ready. Starting collection...")

        visited = set()
        random.shuffle(HASHTAGS)

        for hashtag in HASHTAGS:
            if total_collected >= DAILY_COLLECT_LIMIT:
                break

            log.info(f"Exploring #{hashtag}...")

            if not await safe_goto(page, f'https://www.instagram.com/explore/tags/{hashtag}/'):
                continue

            try:
                # Scroll to load posts
                for i in range(min(MAX_SCROLLS, 12)):
                    scroll_dist = random.randint(SCROLL_DISTANCE_MIN, SCROLL_DISTANCE_MAX)
                    await page.evaluate(f'window.scrollBy(0, {scroll_dist})')
                    await asyncio.sleep(random.uniform(SCROLL_PAUSE_MIN, SCROLL_PAUSE_MAX))

                # Get profiles
                profiles = await get_profile_links(page)
                random.shuffle(profiles)
                log.info(f"  Found {len(profiles)} profiles")

                for profile_path in profiles[:20]:
                    if total_collected >= DAILY_COLLECT_LIMIT:
                        break

                    collected = await visit_profile(page, profile_path, total_collected, visited)
                    total_collected += collected

                    await asyncio.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

            except Exception as e:
                log.warning(f"Error on #{hashtag}: {e}")

            await asyncio.sleep(random.uniform(5, 10))

        await context.close()

    log.info(f"Instagram done: {total_collected} new emails")
    return total_collected


def run_instagram_collector():
    return asyncio.run(collect_from_instagram())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(message)s')
    run_instagram_collector()
