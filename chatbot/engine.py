"""
Chatbot engine — wraps Claude API with per-store system prompts.
Uses Haiku for cost efficiency (~$0.001 per conversation).
"""

import os
from chatbot.store_configs import get_store_config


def _build_system_prompt(config):
    """Build the system prompt from store config."""
    products_text = "\n".join(
        f"  - {p['name']}: ${p['price']} — {p['desc']}"
        for p in config["products"]
    )
    shipping_countries = ", ".join(config["shipping_countries"])

    return f"""You are the AI customer support assistant for {config['store_name']}, a {config['niche']} online store.

STORE INFORMATION:
- Store: {config['store_name']}
- Support email: {config['support_email']}
- Support hours: {config['support_hours']}

PRODUCTS:
{products_text}

SHIPPING:
- Ships to: {shipping_countries}
- Delivery time: {config['shipping_time']}
- Free shipping on orders over ${config['free_shipping_over']}

RETURN POLICY:
{config['return_policy']}

RULES:
1. Be {config['brand_tone']}.
2. Keep responses under 3 sentences unless the customer needs detailed steps.
3. For order tracking: Ask for their order number, then say you can see it's being processed and they'll receive a tracking email within 24 hours. Suggest they check spam folder.
4. For returns: Walk through the return policy step by step. Give them the support email to start the process.
5. For shipping questions: Check if their country is in the shipping list. If yes, give the delivery time. If no, say you don't currently ship there but they can email support to request it.
6. For product questions: Recommend relevant products from the catalog WITH prices.
7. If you genuinely cannot help: Direct them to {config['support_email']} during {config['support_hours']}.
8. NEVER invent order numbers, tracking links, or information not in your context.
9. NEVER say "as an AI" or "I'm a language model." You are the store's support assistant.
10. If asked about discounts or coupons you don't have info about, say "Let me connect you with our team at {config['support_email']} — they can check for any current promotions for you.\""""


def chat(store_id, messages, user_message):
    """
    Process a chat message and return the assistant's reply.

    Args:
        store_id: Store config key
        messages: Previous conversation messages [{"role": "user/assistant", "content": "..."}]
        user_message: New user message

    Returns:
        Assistant reply string
    """
    import anthropic

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return "Support chat is currently being set up. Please email us directly for help!"

    config = get_store_config(store_id)
    system_prompt = _build_system_prompt(config)

    # Keep last 6 messages for context (saves tokens)
    recent = messages[-6:] if len(messages) > 6 else list(messages)
    recent.append({"role": "user", "content": user_message})

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            system=system_prompt,
            messages=recent,
        )
        return response.content[0].text
    except anthropic.AuthenticationError:
        return "Support chat is being configured. Please email us at " + config["support_email"]
    except anthropic.RateLimitError:
        return "We're experiencing high volume right now. Please try again in a moment or email " + config["support_email"]
    except Exception:
        return "Something went wrong. Please email us at " + config["support_email"] + " and we'll help you right away."
