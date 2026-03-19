# Google Maps Lead Generation Pipeline

An end-to-end local Python system that scrapes business leads from Google Maps, enriches them with email addresses, manages them in a Google Sheets CRM, scores and prioritizes leads, and sends personalized outreach emails.

## Features

- **Google Maps Scraping** — Fetches business data (name, address, phone, website, rating, reviews) via SerpAPI with automatic pagination
- **Email Enrichment** — Visits each business website and contact pages to extract email addresses
- **Google Sheets CRM** — Uploads leads to a managed Google Sheet with status tracking, contact method routing, and notes
- **Lead Scoring** — Scores leads 0–100 based on data quality (email, website, rating, reviews) and assigns priority tiers
- **Smart Outreach Routing** — High-quality leads get emailed, phone-only leads go to a call queue, low-quality leads are skipped
- **Personalized Emails** — Generates unique emails per lead based on their rating, website presence, and business context
- **Safety Controls** — Daily email caps, delays between sends, deduplication at every step

## Project Structure

```
gmaps-lead-gen/
├── src/
│   ├── scraper.py           # Google Maps scraper (SerpAPI)
│   ├── email_finder.py      # Website email extraction
│   ├── sheets_manager.py    # Google Sheets CRM manager
│   ├── email_sender.py      # Gmail outreach system
│   ├── lead_scoring.py      # Lead scoring & prioritization
│   └── ai_personalizer.py   # Smart email personalization
├── main.py                  # Pipeline orchestrator & CLI
├── config.py                # All settings & thresholds
├── requirements.txt
├── .env.example
└── .gitignore
```

## Setup Instructions

### 1. Clone & Install

```bash
git clone https://github.com/YOUR_USERNAME/gmaps-lead-gen.git
cd gmaps-lead-gen
pip install -r requirements.txt
```

### 2. SerpAPI Key

1. Create a free account at [serpapi.com](https://serpapi.com)
2. Copy your API key from the dashboard

### 3. Gmail App Password

1. Go to your [Google Account Security](https://myaccount.google.com/security)
2. Enable **2-Step Verification** (required)
3. Go to **App Passwords** → generate a new one for "Mail"
4. Copy the 16-character password

### 4. Google Sheets API (Service Account)

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a new project (or select existing)
3. Enable the **Google Sheets API** and **Google Drive API**
4. Go to **Credentials** → **Create Credentials** → **Service Account**
5. Download the JSON key file → save as `credentials.json` in the project root
6. Copy the service account email (looks like `name@project.iam.gserviceaccount.com`)
7. Open Google Sheets → create a sheet named "Lead CRM" → Share it with the service account email (Editor access)

### 5. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` and fill in your keys:

```
SERPAPI_KEY=your_key_here
GMAIL_USER=your_email@gmail.com
GMAIL_APP_PASSWORD=your_app_password
GOOGLE_CREDS_FILE=credentials.json
```

## How to Run

### Full Pipeline (Interactive)

```bash
python main.py
```

You'll be prompted for:
```
Enter keyword (e.g., cafes): restaurants
Enter location (e.g., Key West, Florida): Miami, Florida
Number of leads to fetch: 50
```

### Pipeline Without Sending Emails

```bash
python main.py --skip-email
```

### Re-Score Existing Leads Only

```bash
python main.py --score-only
```

### CLI Arguments (Non-Interactive)

```bash
python main.py --keyword "cafes" --location "Key West, Florida" --count 50 --skip-email
```

## Pipeline Flow

```
Keyword + Location
       ↓
[1] Scrape Google Maps (SerpAPI)
       ↓
[2] Visit websites → Extract emails
       ↓
[3] Upload to Google Sheets CRM
       ↓
[4] Score & prioritize leads
       ↓
 ┌─────────────┬──────────────┬─────────────┐
 │ Email exists │ No email     │ Low quality  │
 │ Score ≥ 60   │ Score ≥ 70   │ Score < 50   │
 ↓             ↓              ↓
[Send Email]  [Call Queue]   [Skip]
```

## Configuration

All settings are in `config.py`:

| Setting | Default | Description |
|---------|---------|-------------|
| `MAX_EMAILS_PER_DAY` | 50 | Daily email sending cap |
| `EMAIL_DELAY_MIN` | 30s | Min delay between emails |
| `EMAIL_DELAY_MAX` | 90s | Max delay between emails |
| `MIN_OUTREACH_SCORE` | 60 | Min score for email outreach |
| `MIN_CALL_SCORE` | 70 | Min score for call queue |
| `HIGH_THRESHOLD` | 80 | Score for "High" priority |
| `MEDIUM_THRESHOLD` | 50 | Score for "Medium" priority |

## Lead Scoring Breakdown

| Signal | Points |
|--------|--------|
| Has email | +30 |
| Has website | +20 |
| Rating ≥ 4.0 | +20 |
| 50+ reviews | +20 |
| 11–50 reviews | +10 |
| 1–10 reviews | +5 |

## Daily Usage

1. Run `python main.py`
2. Check your Google Sheet for new leads
3. Reply to interested leads
4. Repeat daily with different keywords/locations

## Scheduling (Optional)

### Windows Task Scheduler

1. Open Task Scheduler → Create Basic Task
2. Trigger: Daily at your preferred time
3. Action: Start a program
   - Program: `python`
   - Arguments: `C:\path\to\gmaps-lead-gen\main.py --keyword "cafes" --location "Miami" --count 30`

### Linux/Mac (cron)

```bash
# Run daily at 9 AM
0 9 * * * cd /path/to/gmaps-lead-gen && python main.py --keyword "cafes" --location "Miami" --count 30
```

## License

MIT
