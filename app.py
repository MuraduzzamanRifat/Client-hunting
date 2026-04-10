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
        if not query:
            return render_template("maps.html", error="Search query is required")

        job_id = f"maps_{datetime.now().strftime('%H%M%S')}"
        _jobs[job_id] = {"status": "running", "query": query, "progress": 0, "total": 0, "added": 0, "log": []}

        def _run():
            try:
                from scraper.maps_scraper import search_google_maps, extract_email_from_website
                _jobs[job_id]["log"].append(f"Searching Google Maps: {query}")
                if location:
                    _jobs[job_id]["log"].append(f"Location: {location}")

                businesses = search_google_maps(query, location=location, num_results=max_results)
                _jobs[job_id]["total"] = len(businesses)
                _jobs[job_id]["log"].append(f"Found {len(businesses)} businesses")

                for i, biz in enumerate(businesses, 1):
                    _jobs[job_id]["progress"] = i
                    name = biz["title"]
                    domain = biz["domain"]

                    # Try to get email from website
                    email = None
                    if biz["website"]:
                        _jobs[job_id]["log"].append(f"[{i}/{len(businesses)}] {name} — crawling {biz['website']}")
                        try:
                            email = extract_email_from_website(biz["website"])
                        except Exception:
                            pass

                    # Use domain or title as unique key
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
                    ):
                        _jobs[job_id]["added"] += 1
                        status = f"[OK] {name}"
                        if email:
                            status += f" -> {email}"
                        if biz["phone"]:
                            status += f" | {biz['phone']}"
                        _jobs[job_id]["log"].append(status)
                    else:
                        _jobs[job_id]["log"].append(f"[SKIP] {name} (already exists)")

                _jobs[job_id]["status"] = "done"
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
    db.add_lead(
        data["domain"], data.get("store_name", ""), data.get("email"),
        data.get("niche", "ecommerce"), data.get("source", "api")
    )
    return jsonify({"status": "ok"})


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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("DEBUG", "false").lower() == "true")
