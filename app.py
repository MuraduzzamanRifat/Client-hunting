"""
Web Dashboard — Control the lead generation pipeline from your browser.
Runs on Koyeb at your custom domain.
"""

import os
import sys
import threading
import time
from datetime import datetime

from flask import Flask, jsonify, request, render_template_string, render_template

app = Flask(__name__)

# ── Start background services (works with both gunicorn and python app.py) ──
_bg_started = False

def _start_background_services():
    global _bg_started
    if _bg_started:
        return
    _bg_started = True

    try:
        from src.telegram_bot import start_bot_thread
        start_bot_thread()
    except Exception as e:
        print(f"[WARNING] Telegram bot failed: {e}")

    try:
        from src.followup import start_followup_thread
        start_followup_thread()
    except Exception as e:
        print(f"[WARNING] Follow-up thread failed: {e}")

    try:
        from src.watchdog import start_watchdog
        start_watchdog()
    except Exception as e:
        print(f"[WARNING] Watchdog failed: {e}")

    try:
        from scheduler import start_scheduler_thread
        start_scheduler_thread()
    except Exception as e:
        print(f"[WARNING] Scheduler failed: {e}")


# Start background services on import (for gunicorn)
_start_background_services()

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


def _run_pipeline(keyword: str, location: str, count: int, send_emails: bool = True):
    """
    1:1 Autonomous pipeline — for every lead scraped, if an email is found it is
    sent immediately (after a short delay). No manual trigger needed.

    Logic per lead:
      • Email found          → generate (SEO or web-creation angle) → send → sheet: Email Sent
      • No email + phone     → sheet: Call Queue
      • No email + no phone + no website + no facebook → skip entirely
      • Otherwise            → sheet: Needs Review
    """
    from src.scraper import fetch_leads, save_to_csv
    from src.email_finder import find_email_for_lead
    from src.sheets_manager import SheetsManager
    from src.lead_scoring import score_all_leads
    from src.email_sender import open_smtp_connection, send_single_lead
    from src.metrics import log_event, log_run

    pipeline_status["running"] = True
    pipeline_status["log"] = []
    stats = {"leads_scraped": 0, "emails_found": 0, "leads_uploaded": 0, "emails_sent": 0}

    try:
        # ── Step 1: Scrape ───────────────────────────────────────
        _log(f"Scraping '{keyword}' in '{location}' ({count} leads)...")
        leads = fetch_leads(keyword, location, count)
        stats["leads_scraped"] = len(leads)
        _log(f"Scraped {len(leads)} leads")

        if not leads:
            _log("No leads found. Stopping.")
            return

        # ── Step 2: Connect Google Sheets ────────────────────────
        sheets = SheetsManager()
        if not sheets.authenticate():
            _log("Google Sheets auth failed!")
            return
        sheets.open_or_create_sheet()

        # ── Step 3: Connect SMTP once for the whole batch ────────
        smtp = open_smtp_connection()
        if not smtp:
            _log("WARNING: SMTP connection failed — leads will be saved but emails skipped")
        from_addr = os.getenv("EMAIL_FROM") or os.getenv("EMAIL_USER", "")

        # ── Step 4: Process each lead inline (1:1) ───────────────
        for i, lead in enumerate(leads, 1):
            name = lead.get("Name", "Unknown")
            website = lead.get("Website", "").strip()
            phone = lead.get("Phone", "").strip()
            facebook = lead.get("Facebook", "").strip()

            _log(f"[{i}/{len(leads)}] {name}...")

            # Find email from website
            email = find_email_for_lead(lead) if website else ""

            if email:
                lead["Email"] = email
                lead["Email Status"] = "Email Found"
                stats["emails_found"] += 1
                log_event("collected", recipient=email, details=name)
                _log(f"  Email found: {email}")

                # Score the lead to set Priority / Outreach Type
                scored = score_all_leads([lead])
                lead.update(scored[0])
                lead["Status"] = "New"

                # Skip if already contacted in sheet
                existing = sheets.read_leads()
                if any(r.get("Email", "").lower() == email.lower()
                       and str(r.get("Contacted", "")).lower() == "yes"
                       for r in existing):
                    _log(f"  Already contacted {email} - skipping")
                    continue

                sheets.upload_to_sheets(sheets.clean_data([lead]))
                stats["leads_uploaded"] += 1

                # Find sheet row index
                all_leads = sheets.read_leads()
                sheet_row = 0
                for idx, r in enumerate(all_leads):
                    if r.get("Email", "").lower() == email.lower():
                        sheet_row = idx + 2
                        break

                # Queue for Telegram approval — do NOT send yet
                from src.telegram_bot import queue_email
                from src.ai_personalizer import generate_personalized_email
                content = generate_personalized_email(lead)
                queue_email(
                    recipient=email,
                    subject=content["subject"],
                    body=content["body"],
                    lead_data=lead,
                    service=content.get("service", ""),
                    angle=content.get("angle", ""),
                    sheet_row=sheet_row,
                )
                stats["emails_sent"] += 1
                _log(f"  Queued for Telegram approval: {email}")

            elif phone:
                # No email but has phone → Call Queue
                lead["Email"] = ""
                lead["Email Status"] = "No Email Found"
                lead["Status"] = "New"
                lead["Outreach Type"] = "Call Queue"
                lead["Contact Method"] = "Phone"
                sheets.upload_to_sheets(sheets.clean_data([lead]))
                stats["leads_uploaded"] += 1
                _log(f"  No email — added to Call Queue (phone: {phone})")

            elif not website and not facebook:
                # Nothing useful — skip
                _log(f"  Skipped — no email, phone, website, or Facebook")
                continue

            else:
                # Has website/facebook but no email found — save for review
                lead["Email"] = ""
                lead["Email Status"] = "No Email Found"
                lead["Status"] = "New"
                lead["Outreach Type"] = "Needs Review"
                sheets.upload_to_sheets(sheets.clean_data([lead]))
                stats["leads_uploaded"] += 1
                _log(f"  No email found — saved for review")

        # ── Step 5: Score remaining unscored rows ─────────────────
        try:
            from src.lead_scoring import update_sheet_scores
            update_sheet_scores(sheets)
        except Exception:
            pass

        # Close SMTP
        if smtp:
            try:
                smtp.quit()
            except Exception:
                pass

        log_run(keyword, location, stats["leads_scraped"], stats["emails_found"],
                "success", leads_uploaded=stats["leads_uploaded"])

    except Exception as e:
        _log(f"ERROR: {e}")
        try:
            from src.metrics import log_run
            log_run(keyword, location, stats.get("leads_scraped", 0),
                    stats.get("emails_found", 0), "error", error=str(e))
        except Exception:
            pass
    finally:
        pipeline_status["running"] = False
        pipeline_status["last_run"] = datetime.now().isoformat()
        pipeline_status["last_result"] = stats
        _log(f"Done — {stats['leads_scraped']} scraped | {stats['emails_found']} emails | {stats['emails_sent']} sent")


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
        .nav { display: flex; gap: 8px; margin-bottom: 20px; }
        .nav a { padding: 8px 16px; border-radius: 8px; text-decoration: none; font-size: 14px; font-weight: 600; color: #94a3b8; background: #1e293b; }
        .nav a.active { background: #38bdf8; color: #0f172a; }
        .nav a:hover { background: #334155; }
        .nav a.active:hover { background: #7dd3fc; }
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
        <div class="nav">
            <a href="/" class="active">Pipeline</a>
            <a href="/inbox">Inbox</a>
            <a href="/monitor">Monitor</a>
        </div>
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
    return render_template("pipeline.html", active_page="pipeline")


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


@app.route("/api/send", methods=["POST"])
def api_send():
    """Send emails to qualified leads already in the sheet (no re-scraping)."""
    if pipeline_status["running"]:
        return jsonify({"status": "error", "message": "Pipeline already running"}), 409

    def _do_send():
        from src.sheets_manager import SheetsManager
        from src.email_sender import run_outreach_with_approval

        pipeline_status["running"] = True
        pipeline_status["log"] = []
        try:
            _log("Connecting to Google Sheets...")
            sheets = SheetsManager()
            if not sheets.authenticate():
                _log("ERROR: Google Sheets auth failed")
                return
            sheets.open_or_create_sheet()

            _log(f"Sending emails (approval mode: {os.getenv('APPROVAL_MODE', 'telegram')})...")
            result = run_outreach_with_approval(sheets)

            if "queued" in result:
                _log(f"Queued {result['queued']} email(s) for Telegram approval")
                _log("Check Telegram — tap ✅ to approve each one")
            else:
                _log(f"Sent: {result.get('sent', 0)} | Failed: {result.get('failed', 0)}")
        except Exception as e:
            _log(f"ERROR: {e}")
        finally:
            pipeline_status["running"] = False
            pipeline_status["last_run"] = datetime.now().isoformat()

    threading.Thread(target=_do_send, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/health")
def health():
    return jsonify({"status": "ok", "timestamp": datetime.now().isoformat()})


# ── Inbox Dashboard ──────────────────────────────────────────────────
INBOX_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Email Inbox</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; padding: 20px; }
        .container { max-width: 900px; margin: 0 auto; }
        h1 { color: #38bdf8; margin-bottom: 20px; font-size: 24px; }
        .nav { display: flex; gap: 8px; margin-bottom: 20px; }
        .nav a { padding: 8px 16px; border-radius: 8px; text-decoration: none; font-size: 14px; font-weight: 600; color: #94a3b8; background: #1e293b; }
        .nav a.active { background: #38bdf8; color: #0f172a; }
        .nav a:hover { background: #334155; }
        .nav a.active:hover { background: #7dd3fc; }
        .card { background: #1e293b; border-radius: 12px; padding: 20px; margin-bottom: 16px; }
        .card h2 { color: #94a3b8; font-size: 14px; text-transform: uppercase; margin-bottom: 12px; }
        .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
        .stat { text-align: center; }
        .stat .num { font-size: 28px; font-weight: 700; color: #38bdf8; }
        .stat .label { font-size: 12px; color: #64748b; }
        .filters { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
        .filter-btn { padding: 8px 16px; border: 1px solid #334155; border-radius: 8px; background: #0f172a; color: #94a3b8; cursor: pointer; font-size: 13px; font-weight: 600; }
        .filter-btn.active { background: #38bdf8; color: #0f172a; border-color: #38bdf8; }
        .filter-btn:hover { border-color: #38bdf8; }
        .sort-select { padding: 8px 12px; border: 1px solid #334155; border-radius: 8px; background: #0f172a; color: #e2e8f0; font-size: 13px; margin-left: auto; }
        .email-list { margin-top: 12px; }
        .email-row { padding: 14px 0; border-bottom: 1px solid #334155; cursor: default; }
        .email-row:last-child { border-bottom: none; }
        .email-row:hover { background: #253348; margin: 0 -20px; padding: 14px 20px; border-radius: 8px; border-bottom-color: transparent; }
        .email-header { display: flex; align-items: center; gap: 10px; margin-bottom: 4px; }
        .badge { padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; }
        .badge.business { background: #1d4ed8; color: #bfdbfe; }
        .badge.personal { background: #15803d; color: #bbf7d0; }
        .badge.marketing { background: #b45309; color: #fde68a; }
        .email-subject { font-weight: 600; color: #f1f5f9; font-size: 14px; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .email-date { color: #64748b; font-size: 12px; white-space: nowrap; }
        .email-sender { color: #94a3b8; font-size: 13px; margin-bottom: 4px; }
        .email-snippet { color: #64748b; font-size: 13px; line-height: 1.4; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
        .loading { text-align: center; padding: 40px; color: #64748b; }
        .empty { text-align: center; padding: 40px; color: #64748b; }
        .refresh-btn { padding: 8px 16px; border: 1px solid #334155; border-radius: 8px; background: #0f172a; color: #38bdf8; cursor: pointer; font-size: 13px; font-weight: 600; }
        .refresh-btn:hover { border-color: #38bdf8; }
    </style>
</head>
<body>
    <div class="container">
        <div class="nav">
            <a href="/">Pipeline</a>
            <a href="/inbox" class="active">Inbox</a>
            <a href="/monitor">Monitor</a>
        </div>
        <h1>Email Inbox</h1>

        <div class="card">
            <h2>Overview</h2>
            <div class="stats">
                <div class="stat"><div class="num" id="countTotal">-</div><div class="label">Total</div></div>
                <div class="stat"><div class="num" id="countBusiness">-</div><div class="label">Business</div></div>
                <div class="stat"><div class="num" id="countPersonal">-</div><div class="label">Personal</div></div>
                <div class="stat"><div class="num" id="countMarketing">-</div><div class="label">Marketing</div></div>
            </div>
        </div>

        <div class="card">
            <h2>Filters</h2>
            <div class="filters">
                <button class="filter-btn active" data-cat="all" onclick="setFilter('all')">All</button>
                <button class="filter-btn" data-cat="business" onclick="setFilter('business')">Business</button>
                <button class="filter-btn" data-cat="personal" onclick="setFilter('personal')">Personal</button>
                <button class="filter-btn" data-cat="marketing" onclick="setFilter('marketing')">Marketing</button>
                <button class="refresh-btn" onclick="fetchInbox()">Refresh</button>
                <select class="sort-select" id="sortBy" onchange="fetchInbox()">
                    <option value="date">Sort by Date</option>
                    <option value="sender">Sort by Sender</option>
                    <option value="category">Sort by Category</option>
                </select>
            </div>
        </div>

        <div class="card">
            <h2>Emails</h2>
            <div id="emailList" class="email-list">
                <div class="loading">Loading emails...</div>
            </div>
        </div>
    </div>

    <script>
        let currentFilter = 'all';
        let allEmails = [];

        function setFilter(cat) {
            currentFilter = cat;
            document.querySelectorAll('.filter-btn').forEach(b => {
                b.classList.toggle('active', b.dataset.cat === cat);
            });
            renderEmails();
        }

        function renderEmails() {
            const list = document.getElementById('emailList');
            const sort = document.getElementById('sortBy').value;
            let filtered = currentFilter === 'all' ? [...allEmails] : allEmails.filter(e => e.category === currentFilter);

            if (sort === 'sender') filtered.sort((a, b) => a.sender.localeCompare(b.sender));
            else if (sort === 'category') filtered.sort((a, b) => a.category.localeCompare(b.category));
            // date is already sorted from API

            if (!filtered.length) {
                list.innerHTML = '<div class="empty">No emails found</div>';
                return;
            }

            list.innerHTML = filtered.map(e => `
                <div class="email-row">
                    <div class="email-header">
                        <span class="badge ${e.category}">${e.category}</span>
                        <span class="email-subject">${esc(e.subject)}</span>
                        <span class="email-date">${esc(e.date)}</span>
                    </div>
                    <div class="email-sender">${esc(e.sender)}</div>
                    <div class="email-snippet">${esc(e.snippet)}</div>
                </div>
            `).join('');
        }

        function esc(s) {
            const d = document.createElement('div');
            d.textContent = s || '';
            return d.innerHTML;
        }

        function fetchInbox() {
            const list = document.getElementById('emailList');
            list.innerHTML = '<div class="loading">Loading emails...</div>';

            fetch('/api/inbox?limit=50').then(r => r.json()).then(data => {
                allEmails = data.emails || [];
                document.getElementById('countTotal').textContent = data.counts.total || 0;
                document.getElementById('countBusiness').textContent = data.counts.business || 0;
                document.getElementById('countPersonal').textContent = data.counts.personal || 0;
                document.getElementById('countMarketing').textContent = data.counts.marketing || 0;
                renderEmails();
            }).catch(err => {
                list.innerHTML = '<div class="empty">Failed to load emails: ' + err.message + '</div>';
            });
        }

        // Load on page open
        fetchInbox();
        // Auto-refresh every 60 seconds
        setInterval(fetchInbox, 60000);
    </script>
</body>
</html>
"""


@app.route("/inbox")
def inbox_page():
    return render_template("inbox.html", active_page="inbox")


@app.route("/api/inbox")
def api_inbox():
    from src.inbox_reader import get_inbox
    limit = request.args.get("limit", 50, type=int)
    data = get_inbox(limit=min(limit, 100))
    return jsonify(data)


# ── Monitor Dashboard ────────────────────────────────────────────────
MONITOR_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>System Monitor</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; padding: 20px; }
        .container { max-width: 1000px; margin: 0 auto; }
        h1 { color: #38bdf8; margin-bottom: 20px; font-size: 24px; }
        .nav { display: flex; gap: 8px; margin-bottom: 20px; }
        .nav a { padding: 8px 16px; border-radius: 8px; text-decoration: none; font-size: 14px; font-weight: 600; color: #94a3b8; background: #1e293b; }
        .nav a.active { background: #38bdf8; color: #0f172a; }
        .nav a:hover { background: #334155; }
        .nav a.active:hover { background: #7dd3fc; }
        .card { background: #1e293b; border-radius: 12px; padding: 20px; margin-bottom: 16px; }
        .card h2 { color: #94a3b8; font-size: 14px; text-transform: uppercase; margin-bottom: 12px; }
        .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
        .stats-6 { grid-template-columns: repeat(3, 1fr); }
        .stat { text-align: center; background: #0f172a; border-radius: 8px; padding: 14px 8px; }
        .stat .num { font-size: 26px; font-weight: 700; color: #38bdf8; }
        .stat .num.green { color: #22c55e; }
        .stat .num.red { color: #ef4444; }
        .stat .num.yellow { color: #facc15; }
        .stat .label { font-size: 11px; color: #64748b; margin-top: 4px; }
        .chart-box { position: relative; height: 220px; margin-top: 8px; }
        .funnel { display: flex; align-items: center; gap: 4px; justify-content: center; flex-wrap: wrap; }
        .funnel-step { text-align: center; padding: 12px 16px; border-radius: 8px; min-width: 90px; }
        .funnel-step .num { font-size: 22px; font-weight: 700; }
        .funnel-step .label { font-size: 11px; color: #94a3b8; }
        .funnel-arrow { color: #334155; font-size: 20px; }
        .alert-row { display: flex; align-items: center; gap: 10px; padding: 10px; border-bottom: 1px solid #334155; font-size: 13px; }
        .alert-row:last-child { border-bottom: none; }
        .alert-badge { padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: 700; text-transform: uppercase; }
        .alert-badge.critical { background: #dc2626; color: #fff; }
        .alert-badge.warning { background: #d97706; color: #fff; }
        .alert-badge.info { background: #2563eb; color: #fff; }
        .alert-time { color: #64748b; font-size: 12px; margin-left: auto; white-space: nowrap; }
        .log-table { width: 100%; font-size: 12px; border-collapse: collapse; }
        .log-table th { text-align: left; padding: 8px; color: #64748b; border-bottom: 1px solid #334155; font-weight: 600; }
        .log-table td { padding: 8px; border-bottom: 1px solid #1e293b; color: #94a3b8; }
        .log-table tr:hover td { background: #253348; }
        .badge-sm { padding: 2px 6px; border-radius: 4px; font-size: 10px; font-weight: 700; }
        .badge-sm.sent { background: #22c55e22; color: #22c55e; }
        .badge-sm.bounced { background: #ef444422; color: #ef4444; }
        .badge-sm.failed { background: #ef444422; color: #ef4444; }
        .badge-sm.collected { background: #38bdf822; color: #38bdf8; }
        .domain-row { display: flex; align-items: center; gap: 10px; padding: 8px 0; border-bottom: 1px solid #334155; font-size: 13px; }
        .domain-name { min-width: 140px; color: #e2e8f0; font-weight: 600; }
        .domain-bar { flex: 1; height: 8px; background: #334155; border-radius: 4px; overflow: hidden; }
        .domain-bar-fill { height: 100%; border-radius: 4px; }
        .domain-stat { color: #64748b; font-size: 12px; min-width: 80px; text-align: right; }
        .tabs { display: flex; gap: 4px; margin-bottom: 12px; }
        .tab { padding: 6px 12px; border-radius: 6px; font-size: 12px; cursor: pointer; background: #0f172a; color: #94a3b8; border: 1px solid #334155; }
        .tab.active { background: #38bdf8; color: #0f172a; border-color: #38bdf8; }
        .heatmap { display: grid; grid-template-columns: 40px repeat(24, 1fr); gap: 2px; font-size: 10px; }
        .heatmap-cell { aspect-ratio: 1; border-radius: 3px; display: flex; align-items: center; justify-content: center; }
        .heatmap-label { display: flex; align-items: center; justify-content: flex-end; padding-right: 4px; color: #64748b; }
        .refresh-info { color: #64748b; font-size: 12px; text-align: right; margin-bottom: 8px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="nav">
            <a href="/">Pipeline</a>
            <a href="/inbox">Inbox</a>
            <a href="/monitor" class="active">Monitor</a>
        </div>
        <h1>System Monitor</h1>
        <div class="refresh-info">Auto-refresh: 30s | <span id="lastUpdate">-</span></div>

        <!-- Core Metrics -->
        <div class="card">
            <h2>Today's Metrics</h2>
            <div class="stats">
                <div class="stat"><div class="num" id="mSent">0</div><div class="label">Sent</div></div>
                <div class="stat"><div class="num green" id="mDelivered">0</div><div class="label">Delivered</div></div>
                <div class="stat"><div class="num red" id="mBounced">0</div><div class="label">Bounced</div></div>
                <div class="stat"><div class="num" id="mCollected">0</div><div class="label">Collected</div></div>
            </div>
        </div>

        <!-- Rates -->
        <div class="card">
            <h2>Deliverability Rates</h2>
            <div class="stats stats-6">
                <div class="stat"><div class="num green" id="rDelivery">0%</div><div class="label">Delivery Rate</div></div>
                <div class="stat"><div class="num red" id="rBounce">0%</div><div class="label">Bounce Rate</div></div>
                <div class="stat"><div class="num" id="rReply">0%</div><div class="label">Reply Rate</div></div>
            </div>
        </div>

        <!-- Funnel -->
        <div class="card">
            <h2>Email Funnel</h2>
            <div class="funnel" id="funnel"></div>
        </div>

        <!-- Hourly Chart -->
        <div class="card">
            <h2>Activity (Last 24 Hours)</h2>
            <div class="chart-box"><canvas id="hourlyChart"></canvas></div>
        </div>

        <!-- Sending Heatmap -->
        <div class="card">
            <h2>Sending Heatmap (Best Times)</h2>
            <div class="heatmap" id="heatmap"></div>
        </div>

        <!-- Domain Stats -->
        <div class="card">
            <h2>Domain Breakdown</h2>
            <div id="domainList"></div>
        </div>

        <!-- Alerts -->
        <div class="card">
            <h2>Alerts</h2>
            <div id="alertList"><div style="color:#64748b;padding:12px;">No alerts</div></div>
        </div>

        <!-- Event Log -->
        <div class="card">
            <h2>Event Log</h2>
            <div class="tabs">
                <div class="tab active" data-type="" onclick="setLogFilter(this, '')">All</div>
                <div class="tab" data-type="sent" onclick="setLogFilter(this, 'sent')">Sent</div>
                <div class="tab" data-type="bounced" onclick="setLogFilter(this, 'bounced')">Bounced</div>
                <div class="tab" data-type="collected" onclick="setLogFilter(this, 'collected')">Collected</div>
            </div>
            <div style="max-height:300px;overflow-y:auto;">
                <table class="log-table">
                    <thead><tr><th>Time</th><th>Event</th><th>Recipient</th><th>Subject</th><th>Status</th></tr></thead>
                    <tbody id="logBody"></tbody>
                </table>
            </div>
        </div>
    </div>

    <script>
        let hourlyChart = null;
        let logFilter = '';
        const DAYS = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];

        function esc(s) { const d = document.createElement('div'); d.textContent = s||''; return d.innerHTML; }

        function setLogFilter(el, type) {
            logFilter = type;
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            el.classList.add('active');
            fetchLog();
        }

        function renderFunnel(funnel) {
            const steps = [
                {label:'Collected', num:funnel.collected, color:'#38bdf8'},
                {label:'Sent', num:funnel.sent, color:'#a78bfa'},
                {label:'Delivered', num:funnel.delivered, color:'#22c55e'},
                {label:'Replied', num:funnel.replied, color:'#facc15'},
            ];
            document.getElementById('funnel').innerHTML = steps.map((s,i) =>
                (i>0?'<div class="funnel-arrow">&#8594;</div>':'') +
                `<div class="funnel-step" style="background:${s.color}22">` +
                `<div class="num" style="color:${s.color}">${s.num}</div>` +
                `<div class="label">${s.label}</div></div>`
            ).join('');
        }

        function renderHeatmap(data) {
            const grid = document.getElementById('heatmap');
            const maxCount = Math.max(1, ...data.map(d=>d.count));
            let html = '<div class="heatmap-label"></div>';
            for(let h=0;h<24;h++) html += `<div class="heatmap-label" style="justify-content:center">${h}</div>`;
            for(let d=0;d<7;d++){
                html += `<div class="heatmap-label">${DAYS[d]}</div>`;
                for(let h=0;h<24;h++){
                    const cell = data.find(x=>x.dow===d && x.hour===h);
                    const count = cell ? cell.count : 0;
                    const intensity = count/maxCount;
                    const bg = count===0 ? '#1e293b' : `rgba(56,189,248,${0.15 + intensity*0.85})`;
                    html += `<div class="heatmap-cell" style="background:${bg}" title="${DAYS[d]} ${h}:00 — ${count} sent">${count||''}</div>`;
                }
            }
            grid.innerHTML = html;
        }

        function renderDomains(domains) {
            const el = document.getElementById('domainList');
            if(!domains.length){el.innerHTML='<div style="color:#64748b;padding:12px;">No data yet</div>';return;}
            const maxTotal = Math.max(1,...domains.map(d=>(d.sent||0)+(d.delivered||0)));
            el.innerHTML = domains.slice(0,10).map(d => {
                const total = (d.sent||0)+(d.delivered||0);
                const pct = total/maxTotal*100;
                const color = d.bounce_rate > 5 ? '#ef4444' : d.bounce_rate > 2 ? '#facc15' : '#22c55e';
                return `<div class="domain-row">
                    <div class="domain-name">${esc(d.domain)}</div>
                    <div class="domain-bar"><div class="domain-bar-fill" style="width:${pct}%;background:${color}"></div></div>
                    <div class="domain-stat">${total} sent | ${d.bounce_rate}% bounce</div>
                </div>`;
            }).join('');
        }

        function renderAlerts(alerts) {
            const el = document.getElementById('alertList');
            if(!alerts.length){el.innerHTML='<div style="color:#64748b;padding:12px;">No alerts — all clear</div>';return;}
            el.innerHTML = alerts.map(a =>
                `<div class="alert-row">
                    <span class="alert-badge ${a.level}">${a.level}</span>
                    <span>${esc(a.message)}</span>
                    <span class="alert-time">${a.timestamp.slice(11,19)}</span>
                </div>`
            ).join('');
        }

        function renderLog(events) {
            document.getElementById('logBody').innerHTML = events.map(e =>
                `<tr>
                    <td>${e.timestamp.slice(11,19)}</td>
                    <td><span class="badge-sm ${e.event_type}">${e.event_type}</span></td>
                    <td>${esc(e.recipient)}</td>
                    <td>${esc((e.subject||'').slice(0,40))}</td>
                    <td>${esc(e.status)}</td>
                </tr>`
            ).join('') || '<tr><td colspan="5" style="text-align:center;color:#64748b;">No events yet</td></tr>';
        }

        function renderHourlyChart(hourly) {
            const ctx = document.getElementById('hourlyChart').getContext('2d');
            const labels = hourly.map(h => h.hour.slice(11,16));
            if(hourlyChart) hourlyChart.destroy();
            hourlyChart = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels,
                    datasets: [
                        {label:'Sent', data:hourly.map(h=>h.sent), backgroundColor:'#38bdf8', borderRadius:4},
                        {label:'Collected', data:hourly.map(h=>h.collected), backgroundColor:'#a78bfa', borderRadius:4},
                        {label:'Bounced', data:hourly.map(h=>h.bounced), backgroundColor:'#ef4444', borderRadius:4},
                    ]
                },
                options: {
                    responsive:true, maintainAspectRatio:false,
                    plugins:{legend:{labels:{color:'#94a3b8',font:{size:11}}}},
                    scales:{
                        x:{ticks:{color:'#64748b',font:{size:10}},grid:{color:'#1e293b'}},
                        y:{ticks:{color:'#64748b'},grid:{color:'#1e293b'},beginAtZero:true}
                    }
                }
            });
        }

        function fetchAll() {
            fetch('/api/monitor/summary').then(r=>r.json()).then(d => {
                document.getElementById('mSent').textContent = d.total_sent;
                document.getElementById('mDelivered').textContent = d.funnel.delivered;
                document.getElementById('mBounced').textContent = d.funnel.bounced;
                document.getElementById('mCollected').textContent = d.total_collected;
                document.getElementById('rDelivery').textContent = d.rates.delivery+'%';
                document.getElementById('rBounce').textContent = d.rates.bounce+'%';
                document.getElementById('rReply').textContent = d.rates.reply+'%';
                renderFunnel(d.funnel);
            });
            fetch('/api/monitor/hourly').then(r=>r.json()).then(d => renderHourlyChart(d));
            fetch('/api/monitor/heatmap').then(r=>r.json()).then(d => renderHeatmap(d));
            fetch('/api/monitor/domains').then(r=>r.json()).then(d => renderDomains(d));
            fetch('/api/monitor/alerts').then(r=>r.json()).then(d => renderAlerts(d));
            fetchLog();
            document.getElementById('lastUpdate').textContent = new Date().toLocaleTimeString();
        }

        function fetchLog() {
            const url = logFilter ? `/api/monitor/log?type=${logFilter}` : '/api/monitor/log';
            fetch(url).then(r=>r.json()).then(d => renderLog(d));
        }

        fetchAll();
        setInterval(fetchAll, 30000);
    </script>
</body>
</html>
"""


@app.route("/monitor")
def monitor_page():
    return render_template("monitor.html", active_page="monitor")


@app.route("/api/monitor/summary")
def api_monitor_summary():
    from src.metrics import get_summary
    return jsonify(get_summary())


@app.route("/api/monitor/hourly")
def api_monitor_hourly():
    from src.metrics import get_hourly_stats
    return jsonify(get_hourly_stats(24))


@app.route("/api/monitor/heatmap")
def api_monitor_heatmap():
    from src.metrics import get_sending_heatmap
    return jsonify(get_sending_heatmap())


@app.route("/api/monitor/domains")
def api_monitor_domains():
    from src.metrics import get_domain_stats
    return jsonify(get_domain_stats())


@app.route("/api/monitor/alerts")
def api_monitor_alerts():
    from src.metrics import get_alerts
    return jsonify(get_alerts())


@app.route("/api/approvals")
def api_approvals():
    from src.telegram_bot import get_queue, get_pending_count, get_daily_summary
    status = request.args.get("status", "")
    return jsonify({
        "queue": get_queue(status=status),
        "pending": get_pending_count(),
        "summary": get_daily_summary(),
    })


@app.route("/api/approvals/<int:queue_id>/approve", methods=["POST"])
def api_approve(queue_id):
    from src.telegram_bot import approve_email
    success = approve_email(queue_id)
    return jsonify({"status": "sent" if success else "failed"})


@app.route("/api/approvals/<int:queue_id>/reject", methods=["POST"])
def api_reject(queue_id):
    from src.telegram_bot import reject_email
    reject_email(queue_id)
    return jsonify({"status": "rejected"})


@app.route("/api/score", methods=["POST"])
def api_score_lead():
    """Score a single lead using the smart scoring engine."""
    from src.smart_scorer import smart_score
    lead = request.json or {}
    result = smart_score(lead)
    return jsonify(result)


@app.route("/api/watchdog/status")
def api_watchdog_status():
    from src.watchdog import is_system_paused, _original_daily_cap
    return jsonify({
        "paused": is_system_paused(),
        "daily_cap": config.MAX_EMAILS_PER_DAY,
        "original_cap": _original_daily_cap,
        "approval_mode": config.APPROVAL_MODE,
    })


@app.route("/api/watchdog/report")
def api_watchdog_report():
    from src.watchdog import generate_daily_report
    return jsonify({"report": generate_daily_report()})


@app.route("/api/revenue/analyze", methods=["POST"])
def api_analyze_lead():
    from src.revenue_engine import analyze_lead
    lead = request.json or {}
    return jsonify(analyze_lead(lead))


@app.route("/api/revenue/strategy")
def api_revenue_strategy():
    from src.revenue_engine import get_strategic_recommendations, get_industry_performance
    return jsonify({
        "recommendations": get_strategic_recommendations(),
        "industry_performance": get_industry_performance(),
    })


@app.route("/api/watchdog/scan", methods=["POST"])
def api_watchdog_scan():
    from src.watchdog import scan_email_quality
    data = request.json or {}
    result = scan_email_quality(data.get("subject", ""), data.get("body", ""))
    return jsonify(result)


@app.route("/api/learning")
def api_learning():
    """Run the learning cycle and return weight adjustments."""
    from src.smart_scorer import run_learning_cycle
    result = run_learning_cycle()
    return jsonify(result)


@app.route("/api/followups")
def api_followups():
    from src.followup import get_all_recipients, get_followup_summary
    status = request.args.get("status", "")
    return jsonify({
        "recipients": get_all_recipients(status=status),
        "summary": get_followup_summary(),
    })


@app.route("/api/monitor/log")
def api_monitor_log():
    from src.metrics import get_event_log
    event_type = request.args.get("type", "")
    return jsonify(get_event_log(limit=50, event_type=event_type))


@app.route("/api/quality/run", methods=["POST"])
def api_quality_run():
    """Run data quality audit on the CRM sheet."""
    from src.sheets_manager import SheetsManager
    from src.data_quality import run_quality_check, format_audit_report

    try:
        sheets = SheetsManager()
        if not sheets.authenticate():
            return jsonify({"error": "Google Sheets auth failed"}), 500
        sheets.open_or_create_sheet()
        results = run_quality_check(sheets, verbose=False)

        counts = {"Valid": 0, "Needs Review": 0, "Invalid": 0}
        for r in results:
            counts[r["final_status"]] += 1

        return jsonify({
            "total": len(results),
            "valid": counts["Valid"],
            "needs_review": counts["Needs Review"],
            "invalid": counts["Invalid"],
            "auto_fixed": sum(1 for r in results if r["fixes"]),
            "rows": [
                {
                    "row_id": r["row_id"],
                    "final_status": r["final_status"],
                    "validation_status": r["validation_status"],
                    "issues": r["issues"],
                    "actions": r["actions"],
                    "fixes_applied": list(r["fixes"].keys()),
                }
                for r in results
            ],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/quality/report")
def api_quality_report():
    """Get the last quality run results (read-only, no sheet write)."""
    from src.sheets_manager import SheetsManager
    from src.data_quality import run_quality_check, format_audit_report

    try:
        sheets = SheetsManager()
        if not sheets.authenticate():
            return jsonify({"error": "Google Sheets auth failed"}), 500
        sheets.open_or_create_sheet()
        results = run_quality_check(sheets, verbose=False)
        report_text = format_audit_report(results)
        return jsonify({"report": report_text, "results": results})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Entry point ──────────────────────────────────────────────────────
def start_app():
    """Start Flask app with optional scheduler and Telegram bot."""
    # Start background scheduler
    try:
        from scheduler import start_scheduler_thread
        start_scheduler_thread()
    except Exception as e:
        print(f"[WARNING] Scheduler failed to start: {e}")

    # Start Telegram approval bot
    try:
        from src.telegram_bot import start_bot_thread
        start_bot_thread()
    except Exception as e:
        print(f"[WARNING] Telegram bot failed to start: {e}")

    # Start follow-up checker (reply detection + scheduled follow-ups)
    try:
        from src.followup import start_followup_thread
        start_followup_thread()
    except Exception as e:
        print(f"[WARNING] Follow-up thread failed to start: {e}")

    # Start autonomous watchdog (monitoring + auto-healing)
    try:
        from src.watchdog import start_watchdog
        start_watchdog()
    except Exception as e:
        print(f"[WARNING] Watchdog failed to start: {e}")

    port = int(os.getenv("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    start_app()
