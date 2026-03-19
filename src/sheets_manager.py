"""
Step 3 — Google Sheets CRM Manager.

Authenticates with Google Sheets API via service account,
uploads/updates leads, manages CRM columns, and provides
read/update helpers for other modules.
"""

import csv
import json
import os
import sys
from datetime import date

import gspread
from google.oauth2.service_account import Credentials

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Full column order for the CRM sheet
CRM_HEADERS = [
    "Name", "Address", "Phone", "Website", "Email", "Rating", "Reviews",
    "Lead Score", "Priority", "Outreach Type",
    "Status", "Contact Method", "Contacted", "Notes", "Date Added",
]


class SheetsManager:
    """Manages a Google Sheets spreadsheet as a lead CRM."""

    def __init__(self, creds_file: str = ""):
        self.creds_file = creds_file or config.GOOGLE_CREDS_FILE
        self.client = None
        self.sheet = None
        self.worksheet = None

    def authenticate(self):
        """
        Authenticate with Google Sheets API.
        Supports two modes:
          1. GOOGLE_CREDS_JSON env var (for cloud deployment like Koyeb)
          2. Local credentials.json file (for local development)
        """
        # Mode 1: Credentials from environment variable (Koyeb/cloud)
        creds_json = os.getenv("GOOGLE_CREDS_JSON", "")
        if creds_json:
            try:
                info = json.loads(creds_json)
                creds = Credentials.from_service_account_info(info, scopes=SCOPES)
                self.client = gspread.authorize(creds)
                print("  Google Sheets authenticated (from env).")
                return True
            except Exception as e:
                print(f"[ERROR] Failed to parse GOOGLE_CREDS_JSON: {e}")
                return False

        # Mode 2: Local credentials file
        creds_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            self.creds_file,
        )
        if not os.path.exists(creds_path):
            print(f"[ERROR] Credentials file not found: {creds_path}")
            print("        Set GOOGLE_CREDS_JSON env var or place credentials.json in project root.")
            return False

        creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)
        self.client = gspread.authorize(creds)
        print("  Google Sheets authenticated (from file).")
        return True

    def open_or_create_sheet(self, sheet_name: str = ""):
        """Open existing spreadsheet or create a new one."""
        name = sheet_name or config.SHEET_NAME

        try:
            self.sheet = self.client.open(name)
            print(f"  Opened existing sheet: '{name}'")
        except gspread.SpreadsheetNotFound:
            self.sheet = self.client.create(name)
            print(f"  Created new sheet: '{name}'")

        self.worksheet = self.sheet.sheet1

        # Ensure headers exist
        existing = self.worksheet.row_values(1)
        if not existing:
            self.worksheet.update("A1", [CRM_HEADERS])
            self._format_headers()
            print("  Headers written.")

        return self.worksheet

    def _format_headers(self):
        """Bold the header row and freeze it."""
        try:
            self.worksheet.format("A1:O1", {
                "textFormat": {"bold": True},
                "backgroundColor": {"red": 0.2, "green": 0.2, "blue": 0.2},
                "horizontalAlignment": "CENTER",
                "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
            })
            self.worksheet.freeze(rows=1)
        except Exception as e:
            print(f"  [!] Header formatting skipped: {e}")

    def load_csv(self, csv_path: str) -> list[dict]:
        """Read enriched CSV into list of dicts."""
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))

    def clean_data(self, leads: list[dict]) -> list[dict]:
        """Strip whitespace and normalize empty values."""
        cleaned = []
        for lead in leads:
            row = {}
            for k, v in lead.items():
                v = str(v).strip() if v else ""
                row[k] = v if v and v.lower() not in ("none", "nan", "null") else ""
            cleaned.append(row)
        return cleaned

    def _get_existing_keys(self) -> set:
        """Get set of (name, address) keys already in the sheet."""
        records = self.worksheet.get_all_records()
        keys = set()
        for r in records:
            name = str(r.get("Name", "")).strip().lower()
            addr = str(r.get("Address", "")).strip().lower()
            if name:
                keys.add((name, addr))
        return keys

    def _determine_contact_method(self, lead: dict) -> str:
        """Determine contact method based on available data."""
        if lead.get("Email"):
            return "Email"
        elif lead.get("Phone"):
            return "Phone"
        return "None"

    def upload_to_sheets(self, leads: list[dict]):
        """
        Upload leads to Google Sheets with CRM columns.
        Deduplicates on Name + Address.
        """
        if not leads:
            print("[!] No leads to upload.")
            return

        print(f"\n{'='*50}")
        print(f"  Uploading to Google Sheets")
        print(f"  Leads: {len(leads)}")
        print(f"{'='*50}\n")

        existing_keys = self._get_existing_keys()
        today = date.today().isoformat()

        new_rows = []
        skipped = 0

        for lead in leads:
            name = lead.get("Name", "").strip()
            addr = lead.get("Address", "").strip()
            key = (name.lower(), addr.lower())

            if key in existing_keys:
                skipped += 1
                continue

            existing_keys.add(key)

            row = [
                name,
                addr,
                lead.get("Phone", ""),
                lead.get("Website", ""),
                lead.get("Email", ""),
                lead.get("Rating", ""),
                lead.get("Reviews", ""),
                "",  # Lead Score (filled by scoring module)
                "",  # Priority
                "",  # Outreach Type
                "New",
                self._determine_contact_method(lead),
                "No",
                "",  # Notes
                today,
            ]
            new_rows.append(row)

        if new_rows:
            # Batch append for efficiency
            self.worksheet.append_rows(new_rows, value_input_option="USER_ENTERED")
            print(f"  Uploaded {len(new_rows)} new leads ({skipped} duplicates skipped)")
        else:
            print(f"  No new leads to upload ({skipped} duplicates)")

    def read_leads(self) -> list[dict]:
        """Read all leads from the sheet as list of dicts."""
        return self.worksheet.get_all_records()

    def update_row(self, row_index: int, updates: dict):
        """
        Update specific cells in a row.
        row_index is 1-based (row 1 = header, row 2 = first data row).
        """
        headers = self.worksheet.row_values(1)
        cells = []
        for col_name, value in updates.items():
            if col_name in headers:
                col_idx = headers.index(col_name) + 1
                cells.append(gspread.Cell(row_index, col_idx, value))
        if cells:
            self.worksheet.update_cells(cells)

    def batch_update_column(self, col_name: str, values: list, start_row: int = 2):
        """Update an entire column efficiently."""
        headers = self.worksheet.row_values(1)
        if col_name not in headers:
            return
        col_idx = headers.index(col_name) + 1
        col_letter = chr(64 + col_idx) if col_idx <= 26 else chr(64 + (col_idx - 1) // 26) + chr(65 + (col_idx - 1) % 26)
        cell_range = f"{col_letter}{start_row}:{col_letter}{start_row + len(values) - 1}"
        cell_values = [[v] for v in values]
        self.worksheet.update(cell_range, cell_values, value_input_option="USER_ENTERED")

    def sort_by_score(self):
        """Sort the sheet by Lead Score column (descending)."""
        headers = self.worksheet.row_values(1)
        if "Lead Score" in headers:
            col_idx = headers.index("Lead Score") + 1
            self.worksheet.sort((col_idx, "des"), range=f"A2:O{self.worksheet.row_count}")
            print("  Sheet sorted by Lead Score (highest first)")


# ── Standalone test ──────────────────────────────────────────────────
if __name__ == "__main__":
    mgr = SheetsManager()
    if mgr.authenticate():
        mgr.open_or_create_sheet()
        csv_path = input("Enter enriched CSV path (or press Enter to skip upload): ").strip()
        if csv_path:
            raw = mgr.load_csv(csv_path)
            clean = mgr.clean_data(raw)
            mgr.upload_to_sheets(clean)
        leads = mgr.read_leads()
        print(f"  Total leads in sheet: {len(leads)}")
        print("  Upload complete.")
