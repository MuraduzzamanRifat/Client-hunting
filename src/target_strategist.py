"""
AI Target Strategist — Dynamically generates and rotates scraping targets.

Uses Claude to analyse past run performance and generate high-value
keyword + location combinations, then writes them to the Targets sheet.

Rules enforced:
  - No repeat of same keyword+location within 48 hours
  - Penalise targets that returned low emails or errors
  - Expand top-performing niches into new locations
  - Explore new niches in proven locations
  - 5-10 targets per generation call

Entry points:
  generate_targets(sheets_mgr)  → writes to Targets sheet, returns list of dicts
  format_targets_report(targets) → Telegram-ready text summary
"""

import os
import json
import re
from datetime import datetime, timedelta

import anthropic

import config
from src.metrics import get_run_log

# ─── Settings ────────────────────────────────────────────────────────────────
TARGETS_TAB   = "Targets"
COL_KEYWORD   = "Keyword"
COL_LOCATION  = "Location"
COL_COUNT     = "Count"
COL_ACTIVE    = "Active"
COL_REASON    = "Reason"
COL_ADDED_BY  = "Added By"

RECENT_HOURS  = 48      # avoid targets used in this window
NEW_TARGETS   = 7       # number of targets to generate
DEFAULT_COUNT = 20      # leads per target


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _get_recent_runs(hours: int = 48) -> list[dict]:
    """Return pipeline runs from the last `hours` hours."""
    cutoff = datetime.now() - timedelta(hours=hours)
    runs = get_run_log(limit=200)
    return [
        r for r in runs
        if datetime.fromisoformat(r["timestamp"]) >= cutoff
    ]


def _get_all_existing_targets(sheets_mgr) -> list[dict]:
    """Read all rows (active + inactive) from the Targets tab."""
    try:
        ws = sheets_mgr.sheet.worksheet(TARGETS_TAB)
        return ws.get_all_records()
    except Exception:
        return []


def _existing_target_set(existing_rows: list[dict]) -> set[str]:
    """Build a set of 'keyword|location' already in the sheet."""
    result = set()
    for row in existing_rows:
        k = str(row.get(COL_KEYWORD, "")).strip().lower()
        l = str(row.get(COL_LOCATION, "")).strip().lower()
        if k and l:
            result.add(f"{k}|{l}")
    return result


def _build_performance_context(recent_runs: list[dict]) -> str:
    """Summarise recent run history for the AI prompt."""
    if not recent_runs:
        return "No run history yet — this is the first generation."

    lines = ["Recent pipeline runs (last 48 hours):"]
    for r in recent_runs[:30]:          # cap to avoid huge prompts
        ts  = r["timestamp"][:16]
        kw  = r.get("keyword", "?")
        loc = r.get("location", "?")
        ls  = r.get("leads_scraped", 0)
        ef  = r.get("emails_found", 0)
        st  = r.get("status", "?")
        err = r.get("error", "")

        if err:
            lines.append(f"  [{ts}] {kw} / {loc} → {st}: {err}")
        else:
            rate = f"{ef}/{ls}" if ls else "0/0"
            lines.append(f"  [{ts}] {kw} / {loc} → leads:{ls}  emails:{rate}  {st}")

    return "\n".join(lines)


# ─── Claude AI call ───────────────────────────────────────────────────────────

def _ask_claude(performance_ctx: str, used_targets: set[str], n: int) -> list[dict]:
    """
    Call Claude to generate n new keyword+location targets.
    Returns list of dicts: {keyword, location, count, reason}
    """
    api_key = getattr(config, "ANTHROPIC_API_KEY", "") or os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set. Add it to .env or Koyeb environment.")

    client = anthropic.Anthropic(api_key=api_key)

    used_str = "\n".join(sorted(used_targets)) if used_targets else "(none yet)"

    system_prompt = """You are an expert B2B lead generation strategist specialising in Google Maps scraping.
Your job is to select the most profitable keyword + location targets for scraping local businesses.

The operator sells two services:
1. SEO / Google ranking optimisation — targeted at businesses that HAVE a website but rank poorly.
2. Website creation — targeted at businesses with NO website.

High-value targets:
- Local service businesses (dentists, lawyers, plumbers, salons, restaurants, gyms, etc.)
- Moderate-to-small review counts (under 100 reviews) — not yet dominant
- Locations with many independent businesses (avoid corporate chains)

You must return ONLY a valid JSON array. No prose, no markdown, no code block fences.
Each element must have exactly these keys: keyword, location, count, reason.
count must always be 20.
reason must be one short sentence (max 15 words)."""

    user_prompt = f"""Generate exactly {n} new scraping targets.

{performance_ctx}

Already used / in sheet (DO NOT repeat):
{used_str}

Rules:
- Avoid any keyword|location pair already listed above (case-insensitive)
- Expand top performers into nearby cities
- Try new niches in locations that worked well
- Mix industries: healthcare, legal, trades, food, beauty, fitness, retail
- Mix US + international cities (UK, Canada, Australia, UAE)
- Prefer under-served markets (smaller cities, emerging niches)
- Penalise niches with 0 emails in history

Output format (JSON array, no extra text):
[
  {{"keyword": "dentists", "location": "Tampa, Florida", "count": 20, "reason": "Miami dentists had high email rate — expanding to Tampa"}},
  ...
]"""

    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": user_prompt}],
        system=system_prompt,
    )

    raw = message.content[0].text.strip()

    # Strip accidental markdown fences
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    return json.loads(raw)


# ─── Sheet writer ─────────────────────────────────────────────────────────────

def _ensure_targets_tab_headers(ws) -> list[str]:
    """Make sure the Targets tab has the full header row. Returns header list."""
    expected = [COL_KEYWORD, COL_LOCATION, COL_COUNT, COL_ACTIVE, COL_REASON, COL_ADDED_BY]
    header = ws.row_values(1)
    if not header:
        ws.update("A1", [expected])
        try:
            ws.format("A1:F1", {"textFormat": {"bold": True}})
        except Exception:
            pass
        return expected
    # Back-fill missing columns
    for col in expected:
        if col not in header:
            header.append(col)
    if header != ws.row_values(1):
        ws.update("A1", [header])
    return header


def _append_targets_to_sheet(sheets_mgr, new_targets: list[dict]) -> None:
    """Append new target rows to the Targets tab."""
    try:
        ws = sheets_mgr.sheet.worksheet(TARGETS_TAB)
    except Exception:
        ws = sheets_mgr.sheet.add_worksheet(title=TARGETS_TAB, rows=200, cols=6)

    headers = _ensure_targets_tab_headers(ws)
    col_idx = {h: i for i, h in enumerate(headers)}

    rows = []
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    for t in new_targets:
        row = [""] * len(headers)
        row[col_idx[COL_KEYWORD]]  = t.get("keyword", "")
        row[col_idx[COL_LOCATION]] = t.get("location", "")
        row[col_idx[COL_COUNT]]    = str(t.get("count", DEFAULT_COUNT))
        row[col_idx[COL_ACTIVE]]   = "Yes"
        row[col_idx[COL_REASON]]   = t.get("reason", "")
        row[col_idx[COL_ADDED_BY]] = f"AI ({now_str})"
        rows.append(row)

    ws.append_rows(rows, value_input_option="USER_ENTERED")


# ─── Public API ───────────────────────────────────────────────────────────────

def generate_targets(sheets_mgr, n: int = NEW_TARGETS) -> list[dict]:
    """
    Full pipeline:
      1. Read run history + existing sheet targets
      2. Ask Claude to generate n new targets
      3. Write them to the Targets sheet
      4. Return the generated list
    """
    print(f"[TARGET STRATEGIST] Analysing performance and generating {n} new targets...")

    recent_runs    = _get_recent_runs(RECENT_HOURS)
    existing_rows  = _get_all_existing_targets(sheets_mgr)
    used_set       = _existing_target_set(existing_rows)

    # Also add recent runs to the exclusion set
    for r in recent_runs:
        k = r.get("keyword", "").strip().lower()
        l = r.get("location", "").strip().lower()
        if k and l:
            used_set.add(f"{k}|{l}")

    perf_ctx = _build_performance_context(recent_runs)

    new_targets = _ask_claude(perf_ctx, used_set, n)

    # Validate and deduplicate against existing set (in case Claude repeated one)
    clean = []
    for t in new_targets:
        key_str = f"{t.get('keyword','').strip().lower()}|{t.get('location','').strip().lower()}"
        if key_str not in used_set and t.get("keyword") and t.get("location"):
            clean.append(t)
            used_set.add(key_str)

    if not clean:
        print("[TARGET STRATEGIST] No new unique targets generated.")
        return []

    _append_targets_to_sheet(sheets_mgr, clean)
    print(f"[TARGET STRATEGIST] Wrote {len(clean)} new targets to Targets sheet.")

    return clean


def format_targets_report(targets: list[dict]) -> str:
    """Format a Telegram-ready summary of generated targets."""
    if not targets:
        return "🤖 <b>Target Strategist</b>: No new targets generated."

    lines = [f"🤖 <b>Target Strategist</b> — {len(targets)} new targets added\n"]
    for i, t in enumerate(targets, 1):
        kw  = t.get("keyword", "?")
        loc = t.get("location", "?")
        why = t.get("reason", "")
        lines.append(f"{i}. <b>{kw}</b> / {loc}")
        if why:
            lines.append(f"   <i>{why}</i>")

    lines.append("\nAll targets set to <code>Active = Yes</code> and ready for the next run.")
    return "\n".join(lines)
