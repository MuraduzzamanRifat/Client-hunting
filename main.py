#!/usr/bin/env python3
"""
Cold Email System — Integrated CLI
Commands: scrape, personalize, send, status, export, reply
"""

import sys
import os

# Fix Windows console encoding
if sys.platform == "win32":
    os.environ.setdefault("PYTHONUTF8", "1")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
import csv
import json
import time

import click
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

# Add project root to path
sys.path.insert(0, os.path.dirname(__file__))

import db
from scraper.google_scraper import search_shopify_stores
from scraper.email_extractor import extract_store_info
from personalizer.generator import generate_first_lines
from sender.sequence import run_sequence

console = Console()


@click.group()
def cli():
    """Cold Email System — Shopify lead scraper + AI personalization + email sequences."""
    db.init_db()


# ──────────────────────────────────────────────
# SCRAPE command
# ──────────────────────────────────────────────
@cli.command()
@click.argument("niche")
@click.option("--max-results", "-n", default=50, help="Max stores to find")
def scrape(niche, max_results):
    """Scrape Shopify stores in a niche. Example: scrape 'fitness apparel'"""
    console.print(f"\n[bold]Searching for Shopify stores in: {niche}[/bold]\n")

    # Step 1: Search for domains
    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as progress:
        task = progress.add_task("Searching...", total=None)
        domains = search_shopify_stores(niche, max_results=max_results)
        progress.update(task, description=f"Found {len(domains)} domains")

    if not domains:
        console.print("[red]No stores found. Try a different niche.[/red]")
        return

    console.print(f"Found [green]{len(domains)}[/green] domains. Extracting info...\n")

    # Step 2: Crawl each domain for emails
    added = 0
    for i, domain in enumerate(domains, 1):
        console.print(f"  [{i}/{len(domains)}] {domain}...", end=" ")
        try:
            info = extract_store_info(domain)
            if info["email"]:
                db.add_lead(
                    domain=info["domain"],
                    store_name=info["store_name"],
                    email=info["email"],
                    niche=niche,
                    source="google_search",
                )
                console.print(f"[green]{info['email']}[/green] — {info['store_name']}")
                added += 1
            else:
                # Still save the lead without email
                db.add_lead(
                    domain=info["domain"],
                    store_name=info["store_name"],
                    email=None,
                    niche=niche,
                    source="google_search",
                )
                console.print("[yellow]no email found[/yellow]")
        except Exception as e:
            console.print(f"[red]error: {e}[/red]")

    console.print(f"\n[bold green]Done![/bold green] Added {added} leads with emails out of {len(domains)} stores.")


# ──────────────────────────────────────────────
# IMPORT command
# ──────────────────────────────────────────────
@cli.command(name="import")
@click.argument("filepath")
@click.option("--niche", "-n", default="ecommerce", help="Niche label")
def import_leads(filepath, niche):
    """Import leads from CSV. Columns: domain, store_name, email"""
    if not os.path.exists(filepath):
        console.print(f"[red]File not found: {filepath}[/red]")
        return

    added = 0
    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            domain = row.get("domain", "").strip()
            email = row.get("email", "").strip()
            store_name = row.get("store_name", "").strip()
            if domain:
                if db.add_lead(domain, store_name or domain, email or None, niche, "csv_import"):
                    added += 1

    console.print(f"[green]Imported {added} leads from {filepath}[/green]")


# ──────────────────────────────────────────────
# PERSONALIZE command
# ──────────────────────────────────────────────
@cli.command()
@click.option("--limit", "-n", default=50, help="Max leads to personalize")
def personalize(limit):
    """Generate AI-powered first lines for leads without one."""
    leads = db.get_leads()
    # Filter to those without first_line
    needs_personalization = [l for l in leads if not l.get("first_line") and l.get("email")][:limit]

    if not needs_personalization:
        console.print("[yellow]No leads need personalization.[/yellow]")
        return

    console.print(f"Generating first lines for [bold]{len(needs_personalization)}[/bold] leads...\n")

    results = generate_first_lines(needs_personalization)

    updated = 0
    for lead in needs_personalization:
        first_line = results.get(lead["domain"])
        if first_line:
            db.update_lead(lead["id"], first_line=first_line)
            console.print(f"  [green]{lead['domain']}[/green] → {first_line}")
            updated += 1

    console.print(f"\n[bold green]Personalized {updated} leads.[/bold green]")


# ──────────────────────────────────────────────
# SEND command
# ──────────────────────────────────────────────
@cli.command()
@click.option("--sender-name", "-s", default="", help="Your name in the email signature")
@click.option("--dry-run", is_flag=True, help="Preview emails without sending")
def send(sender_name, dry_run):
    """Send emails in the 3-step sequence."""
    if dry_run:
        console.print("[yellow]DRY RUN — no emails will be sent[/yellow]\n")

    console.print("[bold]Running email sequence...[/bold]\n")
    total = run_sequence(sender_name=sender_name, dry_run=dry_run)
    console.print(f"\n[bold]{'Would send' if dry_run else 'Sent'} {total} emails.[/bold]")


# ──────────────────────────────────────────────
# STATUS command
# ──────────────────────────────────────────────
@cli.command()
def status():
    """Show pipeline status and stats."""
    stats = db.get_stats()

    table = Table(title="Cold Email Pipeline Status")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    table.add_row("Total Leads", str(stats["total_leads"]))
    table.add_row("With Email", str(stats["with_email"]))
    table.add_row("Contacted", str(stats["contacted"]))
    table.add_row("Replied", str(stats["replied"]))
    table.add_row("Sent Today", str(stats["today_sent"]))
    table.add_row("", "")

    for step, count in stats.get("by_step", {}).items():
        table.add_row(f"  {step}", str(count))

    console.print(table)

    # Conversion rates
    if stats["contacted"] > 0:
        reply_rate = (stats["replied"] / stats["contacted"]) * 100
        console.print(f"\nReply rate: [bold]{reply_rate:.1f}%[/bold]")


# ──────────────────────────────────────────────
# REPLY command (mark lead as replied)
# ──────────────────────────────────────────────
@cli.command()
@click.argument("lead_id", type=int)
@click.option("--notes", "-n", default="", help="Notes about the reply")
def reply(lead_id, notes):
    """Mark a lead as replied. Example: reply 42 --notes 'Interested in demo'"""
    db.mark_replied(lead_id, notes)
    console.print(f"[green]Lead {lead_id} marked as replied.[/green]")


# ──────────────────────────────────────────────
# EXPORT command
# ──────────────────────────────────────────────
@cli.command()
@click.option("--format", "-f", "fmt", type=click.Choice(["csv", "json"]), default="csv")
@click.option("--output", "-o", default=None, help="Output file path")
@click.option("--status", "lead_status", default=None, help="Filter by status")
def export(fmt, output, lead_status):
    """Export leads to CSV or JSON."""
    leads = db.get_leads(status=lead_status)
    if not leads:
        console.print("[yellow]No leads to export.[/yellow]")
        return

    if not output:
        output = f"leads_export.{fmt}"

    if fmt == "csv":
        with open(output, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=leads[0].keys())
            writer.writeheader()
            writer.writerows(leads)
    else:
        with open(output, "w", encoding="utf-8") as f:
            json.dump(leads, f, indent=2, default=str)

    console.print(f"[green]Exported {len(leads)} leads to {output}[/green]")


# ──────────────────────────────────────────────
# LEADS command (list leads)
# ──────────────────────────────────────────────
@cli.command()
@click.option("--limit", "-n", default=20, help="Number of leads to show")
@click.option("--status", "lead_status", default=None, help="Filter by status")
def leads(limit, lead_status):
    """List leads in the database."""
    rows = db.get_leads(status=lead_status, limit=limit)
    if not rows:
        console.print("[yellow]No leads found.[/yellow]")
        return

    table = Table(title=f"Leads ({len(rows)} shown)")
    table.add_column("ID", style="dim")
    table.add_column("Domain")
    table.add_column("Store")
    table.add_column("Email")
    table.add_column("Status")
    table.add_column("First Line", max_width=40)

    for r in rows:
        status_style = {"new": "white", "contacted": "yellow", "replied": "green"}.get(r["status"], "white")
        table.add_row(
            str(r["id"]),
            r["domain"] or "",
            r["store_name"] or "",
            r["email"] or "[dim]none[/dim]",
            f"[{status_style}]{r['status']}[/{status_style}]",
            (r.get("first_line") or "")[:40],
        )

    console.print(table)


# ──────────────────────────────────────────────
# FULL command (scrape + personalize + send)
# ──────────────────────────────────────────────
@cli.command()
@click.argument("niche")
@click.option("--max-results", "-n", default=50, help="Max stores to scrape")
@click.option("--sender-name", "-s", default="", help="Your name")
@click.option("--dry-run", is_flag=True, help="Don't actually send")
def full(niche, max_results, sender_name, dry_run):
    """Full pipeline: scrape → personalize → send. Example: full 'fitness supplements'"""
    console.print(f"\n[bold]Full Pipeline — Niche: {niche}[/bold]\n")

    # Step 1: Scrape
    console.rule("Step 1: Scraping")
    ctx = click.Context(scrape)
    ctx.invoke(scrape, niche=niche, max_results=max_results)

    # Step 2: Personalize
    console.rule("Step 2: Personalizing")
    ctx = click.Context(personalize)
    ctx.invoke(personalize, limit=max_results)

    # Step 3: Send
    console.rule("Step 3: Sending")
    ctx = click.Context(send)
    ctx.invoke(send, sender_name=sender_name, dry_run=dry_run)

    console.print("\n[bold green]Pipeline complete![/bold green]")


if __name__ == "__main__":
    cli()
