"""
Per-store chatbot configurations.
Each store has its own name, products, policies, and brand tone.
To add a new client: duplicate 'demo' and fill in their real data.
"""

STORE_CONFIGS = {
    "demo": {
        "store_name": "CozyPaws Pet Supplies",
        "tagline": "Premium supplies for happy pets",
        "niche": "pet supplies",
        "currency": "USD",
        "shipping_countries": ["US", "Canada", "UK", "Australia", "Germany", "France"],
        "shipping_time": "3-7 business days (US), 7-14 international",
        "free_shipping_over": 50,
        "return_policy": "30-day no-questions-asked returns. Email us at support@cozypaws.com, ship the item back in original packaging, and we'll refund within 5 business days of receiving it.",
        "support_email": "support@cozypaws.com",
        "support_hours": "Mon-Fri 9am-6pm EST",
        "products": [
            {"name": "Orthopedic Dog Bed - Large", "price": 79.99, "category": "beds", "desc": "Memory foam, washable cover, fits dogs up to 90lbs"},
            {"name": "Orthopedic Dog Bed - Medium", "price": 59.99, "category": "beds", "desc": "Memory foam, washable cover, fits dogs up to 50lbs"},
            {"name": "Self-Warming Cat Bed", "price": 34.99, "category": "beds", "desc": "Thermal insert, ultra-soft fleece, machine washable"},
            {"name": "Catnip Mouse Toy 3-Pack", "price": 12.99, "category": "toys", "desc": "Organic catnip, durable fabric, rattles inside"},
            {"name": "Indestructible Chew Bone", "price": 18.99, "category": "toys", "desc": "Non-toxic rubber, bacon-flavored, cleans teeth"},
            {"name": "Automatic Pet Feeder", "price": 89.99, "category": "feeding", "desc": "Programmable 4-meal timer, 6L capacity, voice recording"},
            {"name": "Stainless Steel Water Fountain", "price": 42.99, "category": "feeding", "desc": "2L capacity, triple filtration, ultra-quiet pump"},
            {"name": "Premium Dog Harness - No Pull", "price": 29.99, "category": "walking", "desc": "Reflective, padded, front clip, sizes S-XL"},
            {"name": "Retractable Leash - 16ft", "price": 24.99, "category": "walking", "desc": "One-button lock, ergonomic handle, up to 80lbs"},
        ],
        "brand_tone": "friendly, warm, genuinely helpful, uses light pet-related humor when natural",
        "primary_color": "#2D7D46",
        "greeting": "Hey there! I'm the CozyPaws assistant. How can I help you today?",
        "cart_recovery_msg": "I noticed you were checking out some items! Need help with sizing, shipping, or anything else before you complete your order?",
    },
}


def get_store_config(store_id):
    """Get store config by ID, fallback to demo."""
    return STORE_CONFIGS.get(store_id, STORE_CONFIGS["demo"])
