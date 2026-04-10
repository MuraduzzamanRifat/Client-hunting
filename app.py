"""
Cold Email System — Web UI + API
Flask app serving dashboard, lead management, and pipeline controls.
"""

import sys
import os
import csv
import json
import io
import threading
import traceback
from datetime import datetime

if sys.platform == "win32":
    os.environ.setdefault("PYTHONUTF8", "1")

from flask import Flask, render_template, request, jsonify, redirect, url_for, Response

import db

app = Flask(__name__)
db.init_db()


@app.route("/health")
def health():
    return "ok"

# Track background jobs
_jobs = {}


# ──────────────────────────────────────────────
# DASHBOARD
# ──────────────────────────────────────────────
@app.route("/")
def dashboard():
    stats = db.get_stats()
    recent_leads = db.get_leads(limit=10)
    return render_template("dashboard.html", stats=stats, leads=recent_leads)


# ──────────────────────────────────────────────
# LEADS PAGE
# ──────────────────────────────────────────────
@app.route("/leads")
def leads_page():
    status_filter = request.args.get("status")
    page = int(request.args.get("page", 1))
    per_page = 50
    leads = db.get_leads(status=status_filter)
    total = len(leads)
    start = (page - 1) * per_page
    leads_page = leads[start:start + per_page]
    return render_template("leads.html", leads=leads_page, total=total,
                           page=page, per_page=per_page, status_filter=status_filter)


# ──────────────────────────────────────────────
# SCRAPE PAGE
# ──────────────────────────────────────────────
@app.route("/scrape", methods=["GET", "POST"])
def scrape_page():
    if request.method == "POST":
        niche = request.form.get("niche", "").strip()
        max_results = int(request.form.get("max_results", 50))
        if not niche:
            return render_template("scrape.html", error="Niche is required")

        job_id = f"scrape_{datetime.now().strftime('%H%M%S')}"
        _jobs[job_id] = {"status": "running", "niche": niche, "progress": 0, "total": 0, "added": 0, "log": []}

        def _run():
            try:
                from scraper.google_scraper import search_shopify_stores
                from scraper.email_extractor import extract_store_info
                _jobs[job_id]["log"].append(f"Searching for Shopify stores in: {niche}")
                domains = search_shopify_stores(niche, max_results=max_results)
                _jobs[job_id]["total"] = len(domains)
                _jobs[job_id]["log"].append(f"Found {len(domains)} domains")

                for i, domain in enumerate(domains, 1):
                    _jobs[job_id]["progress"] = i
                    try:
                        info = extract_store_info(domain)
                        if info["email"]:
                            db.add_lead(info["domain"], info["store_name"], info["email"], niche, "google_search")
                            _jobs[job_id]["added"] += 1
                            _jobs[job_id]["log"].append(f"[OK] {domain} -> {info['email']}")
                        else:
                            db.add_lead(info["domain"], info["store_name"], None, niche, "google_search")
                            _jobs[job_id]["log"].append(f"[NO EMAIL] {domain}")
                    except Exception as e:
                        _jobs[job_id]["log"].append(f"[ERROR] {domain}: {e}")

                _jobs[job_id]["status"] = "done"
            except Exception as e:
                _jobs[job_id]["status"] = "error"
                _jobs[job_id]["log"].append(f"Fatal: {e}")

        threading.Thread(target=_run, daemon=True).start()
        return redirect(url_for("job_status", job_id=job_id))

    return render_template("scrape.html")


# ──────────────────────────────────────────────
# GOOGLE MAPS SCRAPE
# ──────────────────────────────────────────────
@app.route("/maps", methods=["GET", "POST"])
def maps_page():
    if request.method == "POST":
        query = request.form.get("query", "").strip()
        location = request.form.get("location", "").strip()
        max_results = int(request.form.get("max_results", 20))
        method = request.form.get("method", "auto")
        if not query:
            return render_template("maps.html", error="Search query is required")

        job_id = f"maps_{datetime.now().strftime('%H%M%S')}"
        _jobs[job_id] = {"status": "running", "query": query, "progress": 0, "total": 0, "added": 0, "log": []}

        def _run():
            try:
                from scraper.maps_scraper import search_google_maps, extract_email_from_website
                from scraper.website_auditor import audit_website
                _jobs[job_id]["log"].append(f"Searching Google Maps: {query}")
                _jobs[job_id]["log"].append(f"Method: {method}")
                if location:
                    _jobs[job_id]["log"].append(f"Location: {location}")

                businesses = []
                if method == "direct":
                    from scraper.direct_maps_scraper import search_maps_direct
                    from scraper.proxy_manager import ProxyManager
                    _jobs[job_id]["log"].append("Using direct scraping (no API)...")
                    businesses = search_maps_direct(query, location=location,
                                                    num_results=max_results,
                                                    proxy_manager=ProxyManager())
                elif method == "outscraper":
                    _jobs[job_id]["log"].append("Using Outscraper API...")
                    businesses = search_google_maps(query, location=location, num_results=max_results)
                else:  # auto
                    import config as cfg
                    if cfg.OUTSCRAPER_API_KEY:
                        _jobs[job_id]["log"].append("Using Outscraper API (auto-detected)...")
                        businesses = search_google_maps(query, location=location, num_results=max_results)
                    elif cfg.SERPER_API_KEY:
                        _jobs[job_id]["log"].append("Using Serper API (auto-detected)...")
                        businesses = search_google_maps(query, location=location, num_results=max_results)
                    else:
                        from scraper.direct_maps_scraper import search_maps_direct
                        from scraper.proxy_manager import ProxyManager
                        _jobs[job_id]["log"].append("Using direct scraping (no API key found)...")
                        businesses = search_maps_direct(query, location=location,
                                                        num_results=max_results,
                                                        proxy_manager=ProxyManager())
                _jobs[job_id]["total"] = len(businesses)
                _jobs[job_id]["log"].append(f"Found {len(businesses)} businesses")

                qualified = 0
                for i, biz in enumerate(businesses, 1):
                    _jobs[job_id]["progress"] = i
                    name = biz["title"]
                    domain = biz["domain"]

                    # Use email from Outscraper if available, otherwise crawl
                    email = biz.get("email") or None
                    audit = {"score": 0, "has_chatbot": False, "has_automation": False,
                             "load_time": None, "personal_line": ""}

                    if biz["website"]:
                        action = "auditing"
                        if not email:
                            action = "auditing + crawling for email"
                        _jobs[job_id]["log"].append(f"[{i}/{len(businesses)}] {name} — {action}")
                        try:
                            audit = audit_website(biz["website"])
                            if not email:
                                email = extract_email_from_website(biz["website"])
                        except Exception:
                            pass
                    else:
                        audit["personal_line"] = f"Noticed {name} doesn't have a website yet — huge opportunity."
                        audit["score"] = 5  # No website = best lead

                    lead_domain = domain or name.lower().replace(" ", "-").replace(".", "")

                    if db.add_lead(
                        domain=lead_domain,
                        store_name=name,
                        email=email,
                        niche=query,
                        source="google_maps",
                        phone=biz.get("phone"),
                        address=biz.get("address"),
                        rating=biz.get("rating"),
                        website=biz.get("website"),
                        score=audit.get("score", 50),
                        has_chatbot=1 if audit.get("has_chatbot") else 0,
                        has_automation=1 if audit.get("has_automation") else 0,
                        load_time=audit.get("load_time"),
                    ):
                        # Auto-set personalization line from audit
                        if audit.get("personal_line"):
                            lead = db.get_conn().execute(
                                "SELECT id FROM leads WHERE domain = ?", (lead_domain,)
                            ).fetchone()
                            if lead:
                                db.update_lead(lead["id"], first_line=audit["personal_line"])

                        _jobs[job_id]["added"] += 1

                        # Build status line
                        tags = []
                        if email:
                            tags.append(f"email: {email}")
                        if biz["phone"]:
                            tags.append(f"phone: {biz['phone']}")
                        if not audit.get("has_chatbot") and biz["website"]:
                            tags.append("NO CHATBOT")
                        if audit.get("score", 50) < 30:
                            tags.append("HOT LEAD")
                            qualified += 1

                        _jobs[job_id]["log"].append(f"[OK] {name} | {' | '.join(tags)}")
                    else:
                        _jobs[job_id]["log"].append(f"[SKIP] {name} (already exists)")

                _jobs[job_id]["status"] = "done"
                _jobs[job_id]["qualified"] = qualified
                _jobs[job_id]["log"].append(f"Done! Added {_jobs[job_id]['added']} new leads.")
            except Exception as e:
                _jobs[job_id]["status"] = "error"
                _jobs[job_id]["log"].append(f"Error: {e}")

        threading.Thread(target=_run, daemon=True).start()
        return redirect(url_for("job_status", job_id=job_id))

    return render_template("maps.html")


# ──────────────────────────────────────────────
# PERSONALIZE
# ──────────────────────────────────────────────
@app.route("/personalize", methods=["POST"])
def personalize_action():
    limit = int(request.form.get("limit", 50))
    leads = db.get_leads()
    needs = [l for l in leads if not l.get("first_line") and l.get("email")][:limit]

    if not needs:
        return jsonify({"status": "ok", "message": "No leads need personalization", "updated": 0})

    from personalizer.generator import generate_first_lines
    results = generate_first_lines(needs)
    updated = 0
    for lead in needs:
        fl = results.get(lead["domain"])
        if fl:
            db.update_lead(lead["id"], first_line=fl)
            updated += 1

    return jsonify({"status": "ok", "updated": updated})


# ──────────────────────────────────────────────
# SEND
# ──────────────────────────────────────────────
@app.route("/send", methods=["GET", "POST"])
def send_page():
    if request.method == "POST":
        sender_name = request.form.get("sender_name", "")
        dry_run = request.form.get("dry_run") == "on"

        job_id = f"send_{datetime.now().strftime('%H%M%S')}"
        _jobs[job_id] = {"status": "running", "log": [], "sent": 0}

        def _run():
            try:
                from sender.sequence import run_sequence
                total = run_sequence(sender_name=sender_name, dry_run=dry_run)
                _jobs[job_id]["sent"] = total
                _jobs[job_id]["status"] = "done"
                _jobs[job_id]["log"].append(f"{'Would send' if dry_run else 'Sent'} {total} emails")
            except Exception as e:
                _jobs[job_id]["status"] = "error"
                _jobs[job_id]["log"].append(f"Error: {e}")

        threading.Thread(target=_run, daemon=True).start()
        return redirect(url_for("job_status", job_id=job_id))

    return render_template("send.html")


# ──────────────────────────────────────────────
# JOB STATUS (polling endpoint)
# ──────────────────────────────────────────────
@app.route("/job/<job_id>")
def job_status(job_id):
    job = _jobs.get(job_id)
    if not job:
        return redirect(url_for("dashboard"))
    return render_template("job.html", job=job, job_id=job_id)


@app.route("/api/job/<job_id>")
def api_job_status(job_id):
    job = _jobs.get(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    return jsonify(job)


# ──────────────────────────────────────────────
# REPLY
# ──────────────────────────────────────────────
@app.route("/reply/<int:lead_id>", methods=["POST"])
def mark_reply(lead_id):
    notes = request.form.get("notes", "")
    db.mark_replied(lead_id, notes)
    return redirect(url_for("leads_page"))


# ──────────────────────────────────────────────
# IMPORT CSV
# ──────────────────────────────────────────────
@app.route("/import", methods=["POST"])
def import_csv():
    file = request.files.get("file")
    niche = request.form.get("niche", "ecommerce")
    if not file:
        return redirect(url_for("leads_page"))

    content = file.read().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(content))
    added = 0
    for row in reader:
        domain = row.get("domain", "").strip()
        email = row.get("email", "").strip()
        store_name = row.get("store_name", "").strip()
        if domain:
            if db.add_lead(domain, store_name or domain, email or None, niche, "csv_import"):
                added += 1

    return redirect(url_for("leads_page"))


# ──────────────────────────────────────────────
# EXPORT
# ──────────────────────────────────────────────
@app.route("/export")
def export_leads():
    fmt = request.args.get("format", "csv")
    status_filter = request.args.get("status")
    leads = db.get_leads(status=status_filter)

    if fmt == "json":
        return Response(
            json.dumps(leads, indent=2, default=str),
            mimetype="application/json",
            headers={"Content-Disposition": "attachment; filename=leads.json"}
        )

    output = io.StringIO()
    if leads:
        writer = csv.DictWriter(output, fieldnames=leads[0].keys())
        writer.writeheader()
        writer.writerows(leads)

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=leads.csv"}
    )


# ──────────────────────────────────────────────
# API ENDPOINTS
# ──────────────────────────────────────────────
@app.route("/api/stats")
def api_stats():
    return jsonify(db.get_stats())


@app.route("/api/leads")
def api_leads():
    status_filter = request.args.get("status")
    limit = request.args.get("limit", type=int)
    return jsonify(db.get_leads(status=status_filter, limit=limit))


@app.route("/api/leads", methods=["POST"])
def api_add_lead():
    data = request.get_json()
    if not data or not data.get("domain"):
        return jsonify({"error": "domain required"}), 400
    added = db.add_lead(
        domain=data["domain"],
        store_name=data.get("store_name", ""),
        email=data.get("email"),
        niche=data.get("niche", "ecommerce"),
        source=data.get("source", "api"),
        phone=data.get("phone"),
        address=data.get("address"),
        rating=data.get("rating"),
        website=data.get("website"),
        score=data.get("score", 0),
        has_chatbot=data.get("has_chatbot", 0),
        has_automation=data.get("has_automation", 0),
        load_time=data.get("load_time"),
    )
    # Set first_line if provided
    if added and data.get("first_line"):
        conn = db.get_conn()
        lead = conn.execute("SELECT id FROM leads WHERE domain = ?", (data["domain"],)).fetchone()
        conn.close()
        if lead:
            db.update_lead(lead["id"], first_line=data["first_line"])
    return jsonify({"status": "ok", "added": added})


# ──────────────────────────────────────────────
# DELETE LEAD
# ──────────────────────────────────────────────
@app.route("/delete/<int:lead_id>", methods=["POST"])
def delete_lead(lead_id):
    conn = db.get_conn()
    conn.execute("DELETE FROM sequence_state WHERE lead_id = ?", (lead_id,))
    conn.execute("DELETE FROM send_log WHERE lead_id = ?", (lead_id,))
    conn.execute("DELETE FROM replies WHERE lead_id = ?", (lead_id,))
    conn.execute("DELETE FROM leads WHERE id = ?", (lead_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("leads_page"))


# ──────────────────────────────────────────────
# CHATBOT DEMO ROUTES
# ──────────────────────────────────────────────
@app.route("/demo")
def demo_page():
    store_id = request.args.get("store", "demo")
    from chatbot.store_configs import get_store_config
    config = get_store_config(store_id)
    return render_template("demo.html", store=config, store_id=store_id)


@app.route("/widget")
def widget_page():
    store_id = request.args.get("store", "demo")
    from chatbot.store_configs import get_store_config
    config = get_store_config(store_id)
    return render_template("widget.html", store=config, store_id=store_id)


@app.route("/chat/api", methods=["POST"])
def chat_api():
    data = request.get_json()
    store_id = data.get("store_id", "demo")
    messages = data.get("messages", [])
    user_msg = data.get("message", "")
    if not user_msg:
        return jsonify({"reply": "Could you say that again?"})
    from chatbot.engine import chat
    reply = chat(store_id, messages, user_msg)
    return jsonify({"reply": reply})


@app.route("/setup", methods=["GET", "POST"])
def setup_page():
    from chatbot.store_configs import STORE_CONFIGS
    created_store_id = None

    if request.method == "POST":
        store_id = request.form.get("store_id", "").strip().lower().replace(" ", "-")
        store_name = request.form.get("store_name", "").strip()

        # Parse products
        products = []
        for line in request.form.get("products", "").strip().split("\n"):
            line = line.strip()
            if "|" in line:
                parts = line.split("|")
                products.append({
                    "name": parts[0].strip(),
                    "price": float(parts[1].strip()) if len(parts) > 1 else 0,
                    "desc": parts[2].strip() if len(parts) > 2 else "",
                    "category": "general",
                })

        shipping_countries = [c.strip() for c in request.form.get("shipping_countries", "US").split(",")]

        STORE_CONFIGS[store_id] = {
            "store_name": store_name,
            "tagline": f"{request.form.get('niche', '')} store",
            "niche": request.form.get("niche", "ecommerce"),
            "currency": "USD",
            "shipping_countries": shipping_countries,
            "shipping_time": request.form.get("shipping_time", "3-7 business days"),
            "free_shipping_over": int(request.form.get("free_shipping_over", 50)),
            "return_policy": request.form.get("return_policy", "30-day returns."),
            "support_email": request.form.get("support_email", f"support@{store_id}.com"),
            "support_hours": "Mon-Fri 9am-6pm EST",
            "products": products or [{"name": "Sample Product", "price": 29.99, "desc": "A great product", "category": "general"}],
            "brand_tone": "friendly, helpful, professional",
            "primary_color": request.form.get("primary_color", "#2D7D46"),
            "greeting": f"Hi! I'm the {store_name} assistant. How can I help you today?",
            "cart_recovery_msg": "I noticed you were browsing! Need help finding something or have any questions?",
        }
        created_store_id = store_id

    return render_template("setup.html", stores=STORE_CONFIGS, created_store_id=created_store_id)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("DEBUG", "false").lower() == "true")
