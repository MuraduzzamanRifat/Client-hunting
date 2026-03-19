"""
Web Dashboard — Control the lead generation pipeline from your browser.
Runs on Koyeb at your custom domain.
"""

import os
import sys
import threading
import time
from datetime import datetime

from flask import Flask, jsonify, request, render_template_string

app = Flask(__name__)

# ── Pipeline state ───────────────────────────────────────────────────
pipeline_status = {
    "running": False,
    "last_run": None,
    "last_result": {},
    "log": [],
}


def _log(msg: str):
    """Add a log entry with timestamp."""
    entry = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    pipeline_status["log"].append(entry)
    # Keep last 100 logs
    if len(pipeline_status["log"]) > 100:
        pipeline_status["log"] = pipeline_status["log"][-100:]
    print(entry)


def _run_pipeline(keyword: str, location: str, count: int, send_emails: bool):
    """Run the full pipeline in a background thread."""
    # Lazy imports to reduce startup memory
    from src.scraper import fetch_leads, save_to_csv
    from src.email_finder import enrich_leads, save_enriched_csv
    from src.sheets_manager import SheetsManager
    from src.lead_scoring import update_sheet_scores
    from src.email_sender import run_outreach

    pipeline_status["running"] = True
    pipeline_status["log"] = []
    stats = {"leads_scraped": 0, "emails_found": 0, "leads_uploaded": 0, "emails_sent": 0}

    try:
        # Step 1: Scrape
        _log(f"Step 1: Scraping '{keyword}' in '{location}' ({count} leads)...")
        leads = fetch_leads(keyword, location, count)
        stats["leads_scraped"] = len(leads)
        _log(f"Scraped {len(leads)} leads")

        if not leads:
            _log("No leads found. Stopping.")
            return

        csv_path = save_to_csv(leads, keyword, location)

        # Step 2: Enrich
        _log("Step 2: Enriching leads with emails...")
        enriched = enrich_leads(csv_path)
        stats["emails_found"] = sum(1 for l in enriched if l.get("Email"))
        _log(f"Emails found: {stats['emails_found']}/{len(enriched)}")

        enriched_path = save_enriched_csv(enriched)

        # Step 3: Upload to Sheets
        _log("Step 3: Uploading to Google Sheets...")
        sheets = SheetsManager()
        if not sheets.authenticate():
            _log("Google Sheets auth failed!")
            return
        sheets.open_or_create_sheet()
        raw = sheets.load_csv(enriched_path)
        clean = sheets.clean_data(raw)
        sheets.upload_to_sheets(clean)
        stats["leads_uploaded"] = len(clean)
        _log(f"Uploaded {len(clean)} leads to sheet")

        # Step 4: Score
        _log("Step 4: Scoring leads...")
        update_sheet_scores(sheets)
        _log("Scoring complete")

        # Step 5: Send emails
        if send_emails:
            _log("Step 5: Sending emails...")
            result = run_outreach(sheets)
            stats["emails_sent"] = result.get("sent", 0)
            _log(f"Emails sent: {stats['emails_sent']}")
        else:
            _log("Step 5: Email sending skipped")

    except Exception as e:
        _log(f"ERROR: {e}")
    finally:
        pipeline_status["running"] = False
        pipeline_status["last_run"] = datetime.now().isoformat()
        pipeline_status["last_result"] = stats
        _log(f"Pipeline complete: {stats}")


# ── Dashboard HTML ───────────────────────────────────────────────────
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Lead Gen Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; padding: 20px; }
        .container { max-width: 800px; margin: 0 auto; }
        h1 { color: #38bdf8; margin-bottom: 20px; font-size: 24px; }
        .card { background: #1e293b; border-radius: 12px; padding: 20px; margin-bottom: 16px; }
        .card h2 { color: #94a3b8; font-size: 14px; text-transform: uppercase; margin-bottom: 12px; }
        label { display: block; margin-bottom: 4px; color: #94a3b8; font-size: 13px; }
        input, select { width: 100%; padding: 10px; border: 1px solid #334155; border-radius: 8px; background: #0f172a; color: #e2e8f0; margin-bottom: 12px; font-size: 14px; }
        .row { display: flex; gap: 12px; }
        .row > div { flex: 1; }
        button { padding: 12px 24px; border: none; border-radius: 8px; cursor: pointer; font-size: 14px; font-weight: 600; }
        .btn-primary { background: #38bdf8; color: #0f172a; width: 100%; }
        .btn-primary:hover { background: #7dd3fc; }
        .btn-primary:disabled { background: #334155; color: #64748b; cursor: not-allowed; }
        .checkbox-row { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
        .checkbox-row input { width: auto; margin: 0; }
        .status { padding: 8px 16px; border-radius: 8px; display: inline-block; font-size: 13px; font-weight: 600; }
        .status.running { background: #facc15; color: #0f172a; }
        .status.idle { background: #22c55e; color: #0f172a; }
        .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
        .stat { text-align: center; }
        .stat .num { font-size: 28px; font-weight: 700; color: #38bdf8; }
        .stat .label { font-size: 12px; color: #64748b; }
        #log { background: #0f172a; border: 1px solid #334155; border-radius: 8px; padding: 12px; max-height: 300px; overflow-y: auto; font-family: monospace; font-size: 12px; line-height: 1.6; color: #94a3b8; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Lead Generation Dashboard</h1>

        <div class="card">
            <h2>Pipeline Status</h2>
            <span id="statusBadge" class="status idle">Idle</span>
            <span id="lastRun" style="margin-left: 12px; color: #64748b; font-size: 13px;"></span>
        </div>

        <div class="card">
            <h2>Last Run Stats</h2>
            <div class="stats">
                <div class="stat"><div class="num" id="statScraped">0</div><div class="label">Scraped</div></div>
                <div class="stat"><div class="num" id="statEmails">0</div><div class="label">Emails Found</div></div>
                <div class="stat"><div class="num" id="statUploaded">0</div><div class="label">Uploaded</div></div>
                <div class="stat"><div class="num" id="statSent">0</div><div class="label">Emails Sent</div></div>
            </div>
        </div>

        <div class="card">
            <h2>Run Pipeline</h2>
            <div class="row">
                <div><label>Keyword</label><input id="keyword" placeholder="e.g., cafes" value=""></div>
                <div><label>Location</label><input id="location" placeholder="e.g., Key West, Florida" value=""></div>
            </div>
            <div class="row">
                <div><label>Number of Leads</label><input id="count" type="number" value="20"></div>
                <div>
                    <label>&nbsp;</label>
                    <div class="checkbox-row">
                        <input type="checkbox" id="sendEmails">
                        <label for="sendEmails" style="margin:0; cursor:pointer;">Send emails after scraping</label>
                    </div>
                </div>
            </div>
            <button class="btn-primary" id="runBtn" onclick="runPipeline()">Run Pipeline</button>
        </div>

        <div class="card">
            <h2>Live Log</h2>
            <div id="log">Waiting for pipeline run...</div>
        </div>
    </div>

    <script>
        function runPipeline() {
            const keyword = document.getElementById('keyword').value;
            const location = document.getElementById('location').value;
            const count = document.getElementById('count').value;
            const sendEmails = document.getElementById('sendEmails').checked;

            if (!keyword || !location) { alert('Enter keyword and location'); return; }

            fetch('/api/run', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({keyword, location, count: parseInt(count), send_emails: sendEmails})
            }).then(r => r.json()).then(d => {
                if (d.status === 'started') pollStatus();
                else alert(d.message || 'Error');
            });
        }

        function pollStatus() {
            const interval = setInterval(() => {
                fetch('/api/status').then(r => r.json()).then(d => {
                    const badge = document.getElementById('statusBadge');
                    badge.textContent = d.running ? 'Running...' : 'Idle';
                    badge.className = 'status ' + (d.running ? 'running' : 'idle');

                    document.getElementById('runBtn').disabled = d.running;

                    if (d.last_run) document.getElementById('lastRun').textContent = 'Last: ' + d.last_run;
                    if (d.last_result) {
                        document.getElementById('statScraped').textContent = d.last_result.leads_scraped || 0;
                        document.getElementById('statEmails').textContent = d.last_result.emails_found || 0;
                        document.getElementById('statUploaded').textContent = d.last_result.leads_uploaded || 0;
                        document.getElementById('statSent').textContent = d.last_result.emails_sent || 0;
                    }

                    const logDiv = document.getElementById('log');
                    if (d.log && d.log.length) {
                        logDiv.innerHTML = d.log.map(l => '<div>' + l + '</div>').join('');
                        logDiv.scrollTop = logDiv.scrollHeight;
                    }

                    if (!d.running) clearInterval(interval);
                });
            }, 2000);
        }

        // Poll once on load
        fetch('/api/status').then(r => r.json()).then(d => {
            if (d.running) pollStatus();
            if (d.last_result) {
                document.getElementById('statScraped').textContent = d.last_result.leads_scraped || 0;
                document.getElementById('statEmails').textContent = d.last_result.emails_found || 0;
                document.getElementById('statUploaded').textContent = d.last_result.leads_uploaded || 0;
                document.getElementById('statSent').textContent = d.last_result.emails_sent || 0;
            }
            if (d.last_run) document.getElementById('lastRun').textContent = 'Last: ' + d.last_run;
        });
    </script>
</body>
</html>
"""


# ── API Routes ───────────────────────────────────────────────────────

@app.route("/")
def dashboard():
    return render_template_string(DASHBOARD_HTML)


@app.route("/api/status")
def api_status():
    return jsonify(pipeline_status)


@app.route("/api/run", methods=["POST"])
def api_run():
    if pipeline_status["running"]:
        return jsonify({"status": "error", "message": "Pipeline already running"}), 409

    data = request.json or {}
    keyword = data.get("keyword", "")
    location = data.get("location", "")
    count = data.get("count", 20)
    send_emails = data.get("send_emails", False)

    if not keyword or not location:
        return jsonify({"status": "error", "message": "Keyword and location required"}), 400

    thread = threading.Thread(
        target=_run_pipeline,
        args=(keyword, location, count, send_emails),
        daemon=True,
    )
    thread.start()

    return jsonify({"status": "started"})


@app.route("/health")
def health():
    return jsonify({"status": "ok", "timestamp": datetime.now().isoformat()})


# ── Entry point ──────────────────────────────────────────────────────
def start_app():
    """Start Flask app with optional scheduler."""
    # Start background scheduler (won't crash if Google Sheets not configured)
    try:
        from scheduler import start_scheduler_thread
        start_scheduler_thread()
    except Exception as e:
        print(f"[WARNING] Scheduler failed to start: {e}")

    port = int(os.getenv("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    start_app()
