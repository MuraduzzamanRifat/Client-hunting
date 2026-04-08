"""Google Sheets integration — store all emails, dedup, live dashboard."""

import logging
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

from config import GOOGLE_CREDS_FILE, SHEET_NAME

log = logging.getLogger("outreach.sheets")

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive',
]

HEADERS = ['Email', 'Name', 'Source', 'Source URL', 'Status', 'Followup #', 'Collected At', 'Last Sent At', 'Subject']


class SheetsManager:
    def __init__(self):
        self.gc = None
        self.sheet = None
        self.ws = None

    def connect(self):
        """Authenticate and open/create the sheet."""
        try:
            creds = Credentials.from_service_account_file(GOOGLE_CREDS_FILE, scopes=SCOPES)
            self.gc = gspread.authorize(creds)

            # Open or create
            try:
                self.sheet = self.gc.open(SHEET_NAME)
            except gspread.SpreadsheetNotFound:
                self.sheet = self.gc.create(SHEET_NAME)
                log.info(f"Created new sheet: {SHEET_NAME}")

            # Get or create worksheet tab
            try:
                self.ws = self.sheet.worksheet("Outreach")
            except gspread.WorksheetNotFound:
                self.ws = self.sheet.add_worksheet("Outreach", rows=1000, cols=len(HEADERS))
                self.ws.append_row(HEADERS, value_input_option='RAW')
                log.info("Created 'Outreach' tab with headers")

            # Ensure headers exist
            first_row = self.ws.row_values(1)
            if not first_row or first_row[0] != 'Email':
                self.ws.insert_row(HEADERS, 1, value_input_option='RAW')

            log.info(f"Connected to Google Sheet: {SHEET_NAME}")
            return True

        except Exception as e:
            log.error(f"Google Sheets connection failed: {e}")
            return False

    def get_all_emails(self):
        """Get set of all emails already in the sheet (for dedup)."""
        if not self.ws:
            return set()
        try:
            col = self.ws.col_values(1)  # Email column
            return set(e.lower().strip() for e in col[1:] if e)  # Skip header
        except Exception as e:
            log.warning(f"Failed to read sheet emails: {e}")
            return set()

    def add_row(self, email, name=None, source=None, source_url=None, status='new',
                followup_count=0, collected_at=None, last_sent_at=None, subject=None,
                _cache=None):
        """Add a single email row. Returns True if added (not duplicate)."""
        if not self.ws:
            return False

        # Dedup check — use cache if provided, else fetch
        existing = _cache if _cache is not None else self.get_all_emails()
        if email.lower().strip() in existing:
            return False

        row = [
            email,
            name or '',
            source or '',
            source_url or '',
            status,
            str(followup_count),
            collected_at or datetime.now().strftime('%Y-%m-%d %H:%M'),
            last_sent_at or '',
            subject or '',
        ]

        try:
            self.ws.append_row(row, value_input_option='RAW')
            return True
        except Exception as e:
            log.warning(f"Failed to add row: {e}")
            return False

    def sync_from_db(self, db_rows):
        """Bulk sync emails from SQLite to Sheets. Skips duplicates."""
        if not self.ws:
            return 0

        existing = self.get_all_emails()
        new_rows = []

        for row in db_rows:
            email = row['email'].lower().strip()
            if email not in existing:
                new_rows.append([
                    email,
                    row['name'] or '',
                    row['source'] or '',
                    row['source_url'] or '',
                    row['status'],
                    str(row['followup_count']),
                    row['collected_at'] or '',
                    row['last_sent_at'] or '',
                    '',  # subject filled when sent
                ])
                existing.add(email)

        if new_rows:
            try:
                self.ws.append_rows(new_rows, value_input_option='RAW')
                log.info(f"Synced {len(new_rows)} new emails to Sheets")
            except Exception as e:
                log.warning(f"Bulk sync failed: {e}")
                return 0

        return len(new_rows)

    def update_status(self, email, status, subject=None, last_sent_at=None):
        """Update an email's status in the sheet using cached row map."""
        if not self.ws:
            return
        try:
            # Build row map once, cache it
            if not hasattr(self, '_row_map') or not self._row_map:
                col = self.ws.col_values(1)
                self._row_map = {e.lower().strip(): i + 1 for i, e in enumerate(col)}

            row = self._row_map.get(email.lower().strip())
            if row:
                self.ws.update_cell(row, 5, status)
                if subject:
                    self.ws.update_cell(row, 9, subject)
                if last_sent_at:
                    self.ws.update_cell(row, 8, last_sent_at)
        except Exception as e:
            self._row_map = None  # Invalidate cache on error
            log.warning(f"Failed to update status for {email}: {e}")

    def invalidate_cache(self):
        """Clear cached row map (call after sync)."""
        self._row_map = None
