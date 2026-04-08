"""Persistent browser + auto-login for FB/IG.

First run: auto-logs in using credentials from config.py, saves session.
Every run after: reuses saved session. Re-logins only if session expires.
"""

import os
import asyncio
from config import (
    BROWSER_DATA_DIR, BROWSER_HEADLESS, SLOW_MO,
    FB_EMAIL, FB_PASSWORD, IG_USERNAME, IG_PASSWORD,
)


async def get_browser_context(playwright):
    """Launch browser with persistent context (saved cookies/login)."""
    os.makedirs(BROWSER_DATA_DIR, exist_ok=True)

    context = await playwright.chromium.launch_persistent_context(
        user_data_dir=BROWSER_DATA_DIR,
        headless=BROWSER_HEADLESS,
        slow_mo=SLOW_MO,
        viewport={'width': 1366, 'height': 900},
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        args=[
            '--disable-blink-features=AutomationControlled',
            '--no-first-run',
            '--no-default-browser-check',
        ],
        ignore_default_args=['--enable-automation'],
    )

    return context


async def auto_login_facebook(page):
    """Auto-login to Facebook. Returns True if logged in."""
    await page.goto('https://www.facebook.com', wait_until='domcontentloaded')
    await asyncio.sleep(3)

    # Already logged in?
    if 'login' not in page.url.lower():
        return True

    # No credentials configured
    if not FB_EMAIL or not FB_PASSWORD:
        print("[FB] No credentials in config.py — cannot auto-login")
        return False

    print("[FB] Session expired. Auto-logging in...")
    try:
        # Fill email
        email_field = page.locator('input#email, input[name="email"]').first
        await email_field.click()
        await email_field.fill(FB_EMAIL)
        await asyncio.sleep(0.5)

        # Fill password
        pass_field = page.locator('input#pass, input[name="pass"]').first
        await pass_field.click()
        await pass_field.fill(FB_PASSWORD)
        await asyncio.sleep(0.5)

        # Click login
        login_btn = page.locator('button[name="login"], button[data-testid="royal_login_button"], input[type="submit"]').first
        await login_btn.click()
        await asyncio.sleep(5)

        # Check if login succeeded
        if 'login' in page.url.lower() or 'checkpoint' in page.url.lower():
            # Might be 2FA or security checkpoint
            if 'checkpoint' in page.url.lower():
                print("[FB] Security checkpoint detected. Waiting 60s for manual resolution...")
                await asyncio.sleep(60)
                if 'checkpoint' not in page.url.lower():
                    print("[FB] Checkpoint resolved!")
                    return True
            print("[FB] Login failed — check credentials or resolve checkpoint manually")
            return False

        print("[FB] Logged in successfully!")
        return True

    except Exception as e:
        print(f"[FB] Login error: {e}")
        return False


async def auto_login_instagram(page):
    """Auto-login to Instagram. Returns True if logged in."""
    await page.goto('https://www.instagram.com', wait_until='domcontentloaded')
    await asyncio.sleep(4)

    # Already logged in?
    has_login_form = await page.query_selector('input[name="username"]')
    if 'login' not in page.url.lower() and not has_login_form:
        # Dismiss popups
        try:
            not_now = page.locator('button:has-text("Not Now")')
            if await not_now.count() > 0:
                await not_now.first.click()
                await asyncio.sleep(1)
        except Exception:
            pass
        return True

    # No credentials configured
    if not IG_USERNAME or not IG_PASSWORD:
        print("[IG] No credentials in config.py — cannot auto-login")
        return False

    print("[IG] Session expired. Auto-logging in...")
    try:
        # Fill username
        user_field = page.locator('input[name="username"]').first
        await user_field.click()
        await user_field.fill(IG_USERNAME)
        await asyncio.sleep(0.5)

        # Fill password
        pass_field = page.locator('input[name="password"]').first
        await pass_field.click()
        await pass_field.fill(IG_PASSWORD)
        await asyncio.sleep(0.5)

        # Click login
        login_btn = page.locator('button[type="submit"]').first
        await login_btn.click()
        await asyncio.sleep(5)

        # Check for suspicious login / 2FA
        current_url = page.url
        if 'challenge' in current_url or 'two_factor' in current_url:
            print("[IG] 2FA/challenge detected. Waiting 60s for manual resolution...")
            await asyncio.sleep(60)
            if 'challenge' not in page.url and 'two_factor' not in page.url:
                print("[IG] Challenge resolved!")
            else:
                print("[IG] Challenge not resolved — skipping")
                return False

        # Dismiss "Save Login Info" popup
        await asyncio.sleep(2)
        try:
            save_btn = page.locator('button:has-text("Save Info"), button:has-text("Save info")')
            if await save_btn.count() > 0:
                await save_btn.first.click()
                await asyncio.sleep(1)
        except Exception:
            pass

        # Dismiss notifications popup
        try:
            not_now = page.locator('button:has-text("Not Now")')
            if await not_now.count() > 0:
                await not_now.first.click()
                await asyncio.sleep(1)
        except Exception:
            pass

        # Verify login
        await page.goto('https://www.instagram.com', wait_until='domcontentloaded')
        await asyncio.sleep(3)
        if await page.query_selector('input[name="username"]'):
            print("[IG] Login failed — check credentials")
            return False

        print("[IG] Logged in successfully!")
        return True

    except Exception as e:
        print(f"[IG] Login error: {e}")
        return False
