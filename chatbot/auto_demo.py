"""
Auto-create chatbot demos from real store data.
Scrapes a store's website → extracts products, policies, brand info → creates config.
"""

import re
import requests
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from chatbot.store_configs import STORE_CONFIGS

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
}


def auto_create_demo(store_name, domain, website=None):
    """
    Auto-create a chatbot demo by scraping the store's real website.
    Returns the store_id for the demo link.
    """
    store_id = domain.replace(".", "-").replace("/", "")[:50] if domain else store_name.lower().replace(" ", "-")[:50]

    # Already exists
    if store_id in STORE_CONFIGS:
        return store_id

    url = website or f"https://{domain}"
    if not url.startswith("http"):
        url = "https://" + url

    # Scrape the store
    data = _scrape_store(url)

    # Build config
    STORE_CONFIGS[store_id] = {
        "store_name": store_name or data.get("title", domain),
        "tagline": data.get("description", "")[:100],
        "niche": data.get("niche", "ecommerce"),
        "currency": data.get("currency", "USD"),
        "shipping_countries": data.get("shipping_countries", ["US", "Canada", "UK"]),
        "shipping_time": data.get("shipping_time", "3-7 business days"),
        "free_shipping_over": data.get("free_shipping_over", 50),
        "return_policy": data.get("return_policy", "Contact us for returns and exchanges."),
        "support_email": data.get("support_email", f"support@{domain}"),
        "support_hours": "Mon-Fri 9am-6pm",
        "products": data.get("products", [{"name": "Product", "price": 29.99, "desc": "Quality product", "category": "general"}]),
        "brand_tone": "friendly, helpful, professional",
        "primary_color": data.get("primary_color", "#2D7D46"),
        "greeting": f"Hi! I'm the {store_name or data.get('title', 'store')} assistant. How can I help you today?",
        "cart_recovery_msg": "I noticed you were browsing! Need help with sizing, shipping, or have any questions?",
    }

    return store_id


def _scrape_store(url):
    """Scrape a store website for products, policies, and brand info."""
    data = {
        "title": "",
        "description": "",
        "niche": "ecommerce",
        "products": [],
        "return_policy": "",
        "shipping_time": "3-7 business days",
        "shipping_countries": ["US", "Canada", "UK"],
        "support_email": "",
        "currency": "USD",
        "free_shipping_over": 50,
        "primary_color": "#2D7D46",
    }

    # Scrape homepage
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10, allow_redirects=True)
        if resp.status_code != 200:
            return data
        soup = BeautifulSoup(resp.text, "html.parser")
        html = resp.text
    except Exception:
        return data

    # Title
    title_tag = soup.find("title")
    if title_tag and title_tag.string:
        title = title_tag.string.strip()
        for sep in [" – ", " - ", " | ", " — "]:
            if sep in title:
                title = title.split(sep)[0].strip()
        data["title"] = title[:60]

    # Meta description
    desc = soup.find("meta", attrs={"name": "description"})
    if desc and desc.get("content"):
        data["description"] = desc["content"].strip()[:200]

    # Detect niche from content
    data["niche"] = _detect_niche(html, data["title"], data["description"])

    # Extract products from homepage
    data["products"] = _extract_products(soup, url)

    # Find support email
    email_re = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')
    emails = email_re.findall(html)
    for email in emails:
        email = email.lower()
        if any(email.startswith(p) for p in ['support@', 'info@', 'contact@', 'hello@', 'help@']):
            data["support_email"] = email
            break
    if not data["support_email"] and emails:
        data["support_email"] = emails[0].lower()

    # Extract brand color from CSS
    color_match = re.search(r'--(?:primary|brand|accent)[-\w]*:\s*(#[0-9a-fA-F]{6})', html)
    if color_match:
        data["primary_color"] = color_match.group(1)

    # Currency
    if "$" in html:
        data["currency"] = "USD"
    elif "£" in html:
        data["currency"] = "GBP"
    elif "€" in html:
        data["currency"] = "EUR"

    # Try to scrape policies page
    _scrape_policies(url, data)

    # If no products found from homepage, try collections page
    if not data["products"]:
        _scrape_collections(url, soup, data)

    return data


def _extract_products(soup, base_url):
    """Extract products from page."""
    products = []
    seen = set()

    # Method 1: JSON-LD structured data
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            import json
            ld = json.loads(script.string)
            items = []
            if isinstance(ld, list):
                items = ld
            elif isinstance(ld, dict):
                if ld.get("@type") == "Product":
                    items = [ld]
                elif "itemListElement" in ld:
                    items = [i.get("item", i) for i in ld["itemListElement"]]

            for item in items:
                if isinstance(item, dict) and item.get("name"):
                    name = item["name"][:60]
                    if name.lower() not in seen:
                        seen.add(name.lower())
                        price = 0
                        offers = item.get("offers", {})
                        if isinstance(offers, dict):
                            price = float(offers.get("price", 0) or 0)
                        elif isinstance(offers, list) and offers:
                            price = float(offers[0].get("price", 0) or 0)
                        products.append({
                            "name": name,
                            "price": price,
                            "desc": (item.get("description") or "")[:80],
                            "category": "general",
                        })
        except Exception:
            continue

    # Method 2: Shopify product cards
    if not products:
        for card in soup.find_all("div", class_=re.compile(r"product-card|product-item|grid-product|card--product")):
            title_el = card.find(["h2", "h3", "h4", "a"], class_=re.compile(r"product-title|product-name|card__heading|product__title"))
            if not title_el:
                title_el = card.find(["h2", "h3", "h4"])
            price_el = card.find(class_=re.compile(r"price|money"))

            if title_el:
                name = title_el.get_text(strip=True)[:60]
                if name.lower() not in seen and len(name) > 2:
                    seen.add(name.lower())
                    price = 0
                    if price_el:
                        price_text = price_el.get_text(strip=True)
                        price_match = re.search(r'[\d,.]+', price_text.replace(",", ""))
                        if price_match:
                            try:
                                price = float(price_match.group())
                            except ValueError:
                                pass
                    products.append({
                        "name": name,
                        "price": price,
                        "desc": "",
                        "category": "general",
                    })

    return products[:10]


def _scrape_policies(url, data):
    """Try to find return/shipping policies."""
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    policy_paths = ["/policies/refund-policy", "/policies/shipping-policy",
                    "/pages/shipping", "/pages/returns", "/pages/faq"]

    for path in policy_paths:
        try:
            resp = requests.get(base + path, headers=HEADERS, timeout=8, allow_redirects=True)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "html.parser")
            text = soup.get_text(" ", strip=True)[:2000]

            if "refund" in path or "return" in path:
                # Extract return policy summary
                sentences = [s.strip() for s in text.split(".") if any(w in s.lower() for w in ["return", "refund", "exchange", "days"])]
                if sentences:
                    data["return_policy"] = ". ".join(sentences[:3]) + "."

            if "shipping" in path:
                # Extract shipping info
                sentences = [s.strip() for s in text.split(".") if any(w in s.lower() for w in ["ship", "deliver", "business day", "free shipping"])]
                if sentences:
                    data["shipping_time"] = sentences[0][:100]

                # Free shipping threshold
                free_match = re.search(r'free\s+shipping\s+(?:on\s+orders?\s+)?(?:over|above)\s+\$?([\d,.]+)', text.lower())
                if free_match:
                    try:
                        data["free_shipping_over"] = int(float(free_match.group(1).replace(",", "")))
                    except ValueError:
                        pass
            break  # Got something, stop
        except Exception:
            continue


def _scrape_collections(url, soup, data):
    """Try to find products from collections/catalog page."""
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    # Find collection links
    collection_paths = ["/collections/all", "/collections", "/products", "/shop"]
    for path in collection_paths:
        try:
            resp = requests.get(base + path, headers=HEADERS, timeout=8, allow_redirects=True)
            if resp.status_code != 200:
                continue
            col_soup = BeautifulSoup(resp.text, "html.parser")
            products = _extract_products(col_soup, base)
            if products:
                data["products"] = products
                return
        except Exception:
            continue


def _detect_niche(html, title, description):
    """Detect store niche from page content."""
    text = f"{title} {description} {html[:5000]}".lower()
    niches = {
        "fashion": ["fashion", "clothing", "apparel", "dress", "outfit", "wear"],
        "skincare": ["skincare", "skin care", "serum", "moisturizer", "beauty", "cosmetic"],
        "pet supplies": ["pet", "dog", "cat", "puppy", "kitten", "animal"],
        "fitness": ["fitness", "gym", "workout", "exercise", "athletic"],
        "jewelry": ["jewelry", "necklace", "bracelet", "ring", "earring"],
        "home decor": ["home decor", "furniture", "interior", "candle", "pillow"],
        "food & beverage": ["coffee", "tea", "food", "snack", "organic", "supplement"],
        "electronics": ["tech", "electronic", "gadget", "phone", "laptop"],
        "health": ["health", "wellness", "vitamin", "supplement", "natural"],
    }
    for niche, keywords in niches.items():
        if sum(1 for k in keywords if k in text) >= 2:
            return niche
    return "ecommerce"
