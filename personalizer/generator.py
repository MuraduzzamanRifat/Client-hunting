"""
AI-powered first-line generator for cold emails.
Generates a personalized opening line based on the store's domain/name/niche.
"""

import config


def generate_first_lines(leads, batch_size=10):
    """Generate personalized first lines for a batch of leads.

    Args:
        leads: list of dicts with 'store_name', 'domain', 'niche'
        batch_size: how many to process per API call

    Returns:
        dict mapping domain -> first_line
    """
    if config.AI_PROVIDER == "anthropic" and config.ANTHROPIC_API_KEY:
        return _generate_anthropic(leads, batch_size)
    elif config.AI_PROVIDER == "openai" and config.OPENAI_API_KEY:
        return _generate_openai(leads, batch_size)
    else:
        # Fallback: template-based
        return _generate_template(leads)


def _build_prompt(leads_batch):
    """Build the prompt for AI generation."""
    stores = "\n".join(
        f"- {l['store_name']} ({l['domain']}) — niche: {l.get('niche', 'ecommerce')}"
        for l in leads_batch
    )
    return f"""Generate a personalized cold email opening line for each Shopify store below.

Rules:
- One line per store, max 15 words
- Reference something specific about their store/niche
- Sound natural and human, not salesy
- Format: domain | opening line

Stores:
{stores}

Output each line as: domain | first line"""


def _parse_response(text, leads_batch):
    """Parse AI response into domain -> first_line mapping."""
    result = {}
    lines = text.strip().split("\n")
    for line in lines:
        if "|" in line:
            parts = line.split("|", 1)
            domain = parts[0].strip().lower()
            first_line = parts[1].strip().strip('"')
            result[domain] = first_line

    # Fill missing with template
    for lead in leads_batch:
        if lead["domain"] not in result:
            result[lead["domain"]] = f"Saw your {lead['store_name']} store — looks great."

    return result


def _generate_anthropic(leads, batch_size):
    """Use Claude API for generation."""
    import anthropic
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    all_results = {}

    for i in range(0, len(leads), batch_size):
        batch = leads[i:i + batch_size]
        prompt = _build_prompt(batch)

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}]
        )
        text = response.content[0].text
        all_results.update(_parse_response(text, batch))

    return all_results


def _generate_openai(leads, batch_size):
    """Use OpenAI API for generation."""
    from openai import OpenAI
    client = OpenAI(api_key=config.OPENAI_API_KEY)
    all_results = {}

    for i in range(0, len(leads), batch_size):
        batch = leads[i:i + batch_size]
        prompt = _build_prompt(batch)

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1024,
        )
        text = response.choices[0].message.content
        all_results.update(_parse_response(text, batch))

    return all_results


def _generate_template(leads):
    """Template-based fallback (no API key needed)."""
    templates = [
        "Saw your {store_name} store — noticed you're doing well in {niche}.",
        "Checked out {store_name} — impressive {niche} selection.",
        "Found {store_name} while researching {niche} stores — great catalog.",
        "Noticed {store_name} is growing fast in the {niche} space.",
    ]
    result = {}
    for i, lead in enumerate(leads):
        template = templates[i % len(templates)]
        result[lead["domain"]] = template.format(
            store_name=lead.get("store_name", lead["domain"]),
            niche=lead.get("niche", "ecommerce"),
        )
    return result
