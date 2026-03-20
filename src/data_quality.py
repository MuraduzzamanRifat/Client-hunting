"""
Data Quality Manager — Validates, cleans, and standardizes the Lead CRM sheet.

Runs against the live Google Sheet. For each row:
  - Detects issues (missing fields, bad format, duplicates)
  - Auto-fixes safe issues (email case, URL prefix, status casing)
  - Marks ambiguous rows as "Needs Review"
  - Marks unfixable rows as "Invalid"
  - Writes results back to a "Validation Status" column

Can be triggered manually (/api/quality/run), or called from main.py.
"""

import re
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

# ── Constants ────────────────────────────────────────────────────────

QUALITY_COL = "Validation Status"

VALID_STATUS_VALUES = {"New", "Contacted", "Email Sent", "Replied", "Closed", "Skip"}
VALID_CONTACTED_VALUES = {"Yes", "No"}
VALID_PRIORITY_VALUES = {"High", "Medium", "Low", ""}
VALID_OUTREACH_VALUES = {"Email", "Call Queue", "Skip", ""}

EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')

JUNK_EMAIL_PREFIXES = [
    "noreply", "no-reply", "donotreply", "do-not-reply",
    "mailer-daemon", "daemon", "root", "postmaster",
    "abuse", "hostmaster",
]

STATUS_ALIASES = {
    "new": "New",
    "contacted": "Contacted",
    "email sent": "Email Sent",
    "emailed": "Email Sent",
    "replied": "Replied",
    "response": "Replied",
    "closed": "Closed",
    "done": "Closed",
    "skip": "Skip",
    "skipped": "Skip",
}

CONTACT_ALIASES = {
    "yes": "Yes", "y": "Yes", "true": "Yes", "1": "Yes",
    "no": "No", "n": "No", "false": "No", "0": "No",
}


# ── Validators ───────────────────────────────────────────────────────

def _validate_email(email: str) -> tuple[str, str, str]:
    """
    Returns (normalized_email, action, issue).
    action: 'fixed' | 'flag' | 'ok' | ''
    """
    if not email:
        return "", "", ""  # missing email is not an error (phone-only lead)

    normalized = email.strip().lower()

    if not EMAIL_RE.match(normalized):
        return email, "flag", "Invalid email format"

    prefix = normalized.split("@")[0]
    if any(prefix == j or prefix.startswith(j + ".") for j in JUNK_EMAIL_PREFIXES):
        return email, "flag", f"Role/noreply email: {normalized}"

    if normalized != email:
        return normalized, "fixed", "Email normalized to lowercase"

    return normalized, "ok", ""


def _validate_website(url: str) -> tuple[str, str, str]:
    """Returns (normalized_url, action, issue)."""
    if not url:
        return "", "", ""

    url = url.strip()

    # Add https:// if missing scheme
    if url and not url.startswith(("http://", "https://")):
        return "https://" + url, "fixed", "Added https:// prefix to website"

    # Check for obviously broken URLs
    if " " in url or len(url) < 6:
        return url, "flag", "Malformed website URL"

    return url, "ok", ""


def _validate_name(name: str) -> tuple[str, str, str]:
    """Returns (normalized_name, action, issue)."""
    if not name or not name.strip():
        return name, "invalid", "Missing business name (required)"

    name = name.strip()

    # Fix ALL CAPS or all lowercase names
    if name == name.upper() and len(name) > 3:
        return name.title(), "fixed", "Name normalized from ALL CAPS"
    if name == name.lower() and len(name) > 3 and " " in name:
        return name.title(), "fixed", "Name normalized from all lowercase"

    return name, "ok", ""


def _validate_status(status: str) -> tuple[str, str, str]:
    """Returns (normalized_status, action, issue)."""
    if not status:
        return "New", "fixed", "Status missing — defaulted to 'New'"

    if status in VALID_STATUS_VALUES:
        return status, "ok", ""

    alias = STATUS_ALIASES.get(status.strip().lower())
    if alias:
        return alias, "fixed", f"Status '{status}' standardized to '{alias}'"

    return status, "flag", f"Unknown status value: '{status}'"


def _validate_contacted(contacted: str) -> tuple[str, str, str]:
    """Returns (normalized, action, issue)."""
    if not contacted:
        return "No", "fixed", "Contacted missing — defaulted to 'No'"

    if contacted in VALID_CONTACTED_VALUES:
        return contacted, "ok", ""

    alias = CONTACT_ALIASES.get(contacted.strip().lower())
    if alias:
        return alias, "fixed", f"Contacted '{contacted}' standardized to '{alias}'"

    return contacted, "flag", f"Unknown Contacted value: '{contacted}'"


def _validate_rating(rating: str) -> tuple[str, str, str]:
    """Validate rating is a number 0–5."""
    if not rating:
        return rating, "ok", ""
    try:
        r = float(rating)
        if not (0.0 <= r <= 5.0):
            return rating, "flag", f"Rating out of range: {rating}"
        return rating, "ok", ""
    except ValueError:
        return rating, "flag", f"Non-numeric rating: '{rating}'"


# ── Duplicate Detection ──────────────────────────────────────────────

def _build_duplicate_index(records: list[dict]) -> dict:
    """
    Returns a dict mapping (email_lower) → [row_indices] and
    (name_lower, address_lower) → [row_indices].
    """
    email_index = {}
    name_addr_index = {}

    for i, r in enumerate(records):
        email = str(r.get("Email", "")).strip().lower()
        name = str(r.get("Name", "")).strip().lower()
        addr = str(r.get("Address", "")).strip().lower()

        if email:
            email_index.setdefault(email, []).append(i)
        if name:
            name_addr_index.setdefault((name, addr), []).append(i)

    # Only keep keys with duplicates
    dup_emails = {k: v for k, v in email_index.items() if len(v) > 1}
    dup_name_addr = {k: v for k, v in name_addr_index.items() if len(v) > 1}
    return dup_emails, dup_name_addr


# ── Core Audit Engine ────────────────────────────────────────────────

def audit_row(row_id: int, row: dict, dup_emails: dict, dup_name_addr: dict) -> dict:
    """
    Audit a single row. Returns:
    {
        "row_id": int,
        "fixes": {col: new_value},
        "issues": [str],
        "actions": [str],
        "final_status": "Valid" | "Needs Review" | "Invalid",
        "validation_status": str  (written to sheet column)
    }
    """
    fixes = {}
    issues = []
    actions = []
    is_invalid = False
    needs_review = False

    # ── Name ──
    name_val, action, issue = _validate_name(row.get("Name", ""))
    if action == "invalid":
        is_invalid = True
        issues.append(issue)
        actions.append(f"Flagged as Invalid: {issue}")
    elif action == "fixed":
        fixes["Name"] = name_val
        issues.append(issue)
        actions.append(f"Auto-fixed: {issue}")

    # ── Email ──
    email_val, action, issue = _validate_email(row.get("Email", ""))
    if action == "fixed":
        fixes["Email"] = email_val
        actions.append(f"Auto-fixed: {issue}")
    elif action == "flag":
        needs_review = True
        issues.append(issue)
        actions.append(f"Flagged for review: {issue}")

    # ── Website ──
    web_val, action, issue = _validate_website(row.get("Website", ""))
    if action == "fixed":
        fixes["Website"] = web_val
        actions.append(f"Auto-fixed: {issue}")
    elif action == "flag":
        needs_review = True
        issues.append(issue)
        actions.append(f"Flagged for review: {issue}")

    # ── Status ──
    status_val, action, issue = _validate_status(row.get("Status", ""))
    if action == "fixed":
        fixes["Status"] = status_val
        actions.append(f"Auto-fixed: {issue}")
    elif action == "flag":
        needs_review = True
        issues.append(issue)
        actions.append(f"Flagged for review: {issue}")

    # ── Contacted ──
    contacted_val, action, issue = _validate_contacted(row.get("Contacted", ""))
    if action == "fixed":
        fixes["Contacted"] = contacted_val
        actions.append(f"Auto-fixed: {issue}")
    elif action == "flag":
        needs_review = True
        issues.append(issue)
        actions.append(f"Flagged for review: {issue}")

    # ── Rating ──
    _, action, issue = _validate_rating(row.get("Rating", ""))
    if action == "flag":
        needs_review = True
        issues.append(issue)
        actions.append(f"Flagged for review: {issue}")

    # ── Reachability check ──
    has_email = bool((fixes.get("Email") or row.get("Email", "")).strip())
    has_phone = bool(row.get("Phone", "").strip())
    if not has_email and not has_phone:
        needs_review = True
        issues.append("No email or phone — lead is unreachable")
        actions.append("Flagged for review: unreachable lead")

    # ── Duplicate check ──
    email_lower = str(row.get("Email", "")).strip().lower()
    name_lower = str(row.get("Name", "")).strip().lower()
    addr_lower = str(row.get("Address", "")).strip().lower()

    if email_lower and email_lower in dup_emails:
        dupes = [r + 2 for r in dup_emails[email_lower] if r + 2 != row_id]
        if dupes:
            needs_review = True
            issues.append(f"Duplicate email — also in row(s): {dupes}")
            actions.append(f"Flagged for review: duplicate email (rows {dupes})")

    if name_lower and (name_lower, addr_lower) in dup_name_addr:
        dupes = [r + 2 for r in dup_name_addr[(name_lower, addr_lower)] if r + 2 != row_id]
        if dupes:
            needs_review = True
            issues.append(f"Duplicate name+address — also in row(s): {dupes}")
            actions.append(f"Flagged for review: duplicate entry (rows {dupes})")

    # ── Determine final status ──
    if is_invalid:
        final_status = "Invalid"
        vs = "❌ Invalid"
    elif needs_review:
        final_status = "Needs Review"
        vs = "⚠️ Needs Review"
    else:
        final_status = "Valid"
        vs = "✅ Valid"

    # If auto-fixes applied but no remaining issues, still mark Valid
    if fixes and not issues:
        vs = "✅ Valid (auto-fixed)"

    return {
        "row_id": row_id,
        "fixes": fixes,
        "issues": issues,
        "actions": actions if actions else ["No issues found"],
        "final_status": final_status,
        "validation_status": vs,
    }


# ── Sheet Runner ─────────────────────────────────────────────────────

def run_quality_check(sheets_mgr, verbose: bool = True) -> list[dict]:
    """
    Run full data quality audit on the CRM sheet.

    - Reads all rows
    - Validates and cleans each one
    - Writes fixes + Validation Status column back to sheet
    - Returns audit report as list of dicts

    Args:
        sheets_mgr: authenticated SheetsManager instance
        verbose: print per-row output

    Returns:
        list of audit result dicts (one per row)
    """
    ws = sheets_mgr.worksheet
    if ws is None:
        print("[DQ] Worksheet not open. Call open_or_create_sheet() first.")
        return []

    # ── Ensure Validation Status column exists ──
    headers = ws.row_values(1)
    if QUALITY_COL not in headers:
        col_idx = len(headers) + 1
        ws.update_cell(1, col_idx, QUALITY_COL)
        headers = ws.row_values(1)
        print(f"  [DQ] Added '{QUALITY_COL}' column (col {col_idx})")

    quality_col_idx = headers.index(QUALITY_COL) + 1  # 1-based

    # ── Read all data ──
    records = ws.get_all_records()
    if not records:
        print("  [DQ] Sheet is empty — nothing to audit.")
        return []

    print(f"\n{'='*55}")
    print(f"  Data Quality Audit — {len(records)} rows")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*55}\n")

    # ── Build duplicate index ──
    dup_emails, dup_name_addr = _build_duplicate_index(records)

    results = []
    cells_to_update = []   # gspread.Cell list for batch write
    fix_cells = []         # cells for auto-fixes

    import gspread

    for i, record in enumerate(records):
        row_id = i + 2  # 1-based, skip header
        result = audit_row(row_id, record, dup_emails, dup_name_addr)
        results.append(result)

        # Queue Validation Status cell
        cells_to_update.append(
            gspread.Cell(row_id, quality_col_idx, result["validation_status"])
        )

        # Queue auto-fix cells
        for col_name, new_val in result["fixes"].items():
            if col_name in headers:
                col_i = headers.index(col_name) + 1
                fix_cells.append(gspread.Cell(row_id, col_i, new_val))

        # Print per-row output
        if verbose:
            icon = {"Valid": "✅", "Needs Review": "⚠️", "Invalid": "❌"}.get(
                result["final_status"], "·"
            )
            name = record.get("Name", "(no name)")[:40]
            print(f"  Row {row_id:>3} {icon} {name}")
            for issue in result["issues"]:
                print(f"           Issue : {issue}")
            for action in result["actions"]:
                if "Auto-fixed" in action or "defaulted" in action:
                    print(f"           Action: {action}")

    # ── Batch write back to sheet ──
    if fix_cells:
        ws.update_cells(fix_cells)
        print(f"\n  Auto-fixed {len(fix_cells)} cell(s) in sheet")

    if cells_to_update:
        ws.update_cells(cells_to_update)

    # ── Summary ──
    counts = {"Valid": 0, "Needs Review": 0, "Invalid": 0}
    auto_fixed = sum(1 for r in results if r["fixes"])
    for r in results:
        counts[r["final_status"]] += 1

    print(f"\n{'─'*55}")
    print(f"  Audit Complete")
    print(f"  ✅ Valid       : {counts['Valid']}")
    print(f"  ⚠️  Needs Review: {counts['Needs Review']}")
    print(f"  ❌ Invalid     : {counts['Invalid']}")
    print(f"  🔧 Auto-fixed  : {auto_fixed} row(s)")
    print(f"{'─'*55}\n")

    return results


def format_audit_report(results: list[dict]) -> str:
    """Format audit results as plain text (for Telegram/API response)."""
    lines = [f"📊 <b>Data Quality Report</b>  ({len(results)} rows)\n"]
    counts = {"Valid": 0, "Needs Review": 0, "Invalid": 0}
    for r in results:
        counts[r["final_status"]] += 1

    lines.append(
        f"✅ Valid: {counts['Valid']}  "
        f"⚠️ Review: {counts['Needs Review']}  "
        f"❌ Invalid: {counts['Invalid']}\n"
    )

    # Only list rows that need attention
    attention = [r for r in results if r["final_status"] != "Valid"]
    if attention:
        lines.append("<b>Rows Needing Attention:</b>")
        for r in attention[:20]:  # cap at 20 for Telegram message length
            lines.append(f"\n<b>Row {r['row_id']}</b> — {r['final_status']}")
            for issue in r["issues"][:2]:
                lines.append(f"  • {issue}")
            for action in r["actions"]:
                if "Auto-fixed" in action:
                    lines.append(f"  🔧 {action}")

    auto_fixed = sum(1 for r in results if r["fixes"])
    lines.append(f"\n🔧 Auto-fixed: {auto_fixed} row(s)")
    return "\n".join(lines)


# ── Standalone entry point ────────────────────────────────────────────
if __name__ == "__main__":
    from src.sheets_manager import SheetsManager

    mgr = SheetsManager()
    if not mgr.authenticate():
        print("Auth failed.")
        raise SystemExit(1)

    mgr.open_or_create_sheet()
    results = run_quality_check(mgr, verbose=True)

    # Print structured report
    print("\n── Detailed Report ──")
    for r in results:
        if r["final_status"] != "Valid" or r["fixes"]:
            print(f"\nRow ID    : {r['row_id']}")
            for issue in r["issues"]:
                print(f"Issue     : {issue}")
            for action in r["actions"]:
                print(f"Action    : {action}")
            print(f"Final     : {r['final_status']}")
