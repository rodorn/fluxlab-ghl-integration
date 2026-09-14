"""Storage abstraction for normalized lead records.

Interface `LeadSink` with three implementations:
  * CSVSink    - append to a CSV file (zero-config default, always works)
  * SQLiteSink - insert into a SQLite table
  * GSheetSink - append a row to a Google Sheet via gspread (needs creds)

Selection is driven by the SINK env var (csv | sqlite | gsheet). The default
is csv so the service runs and is verifiable without any credentials.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

log = logging.getLogger("ghl-webhook.sink")

FIELDNAMES = [
    "received_at",
    "contact_id",
    "name",
    "email",
    "phone",
    "source",
    "tags",
    "location_id",
    "custom_fields",
]


def _flatten(record: dict[str, Any]) -> dict[str, Any]:
    row = dict(record)
    if isinstance(row.get("custom_fields"), (dict, list)):
        row["custom_fields"] = json.dumps(row["custom_fields"], ensure_ascii=False)
    return row


class LeadSink(ABC):
    name: str = "abstract"

    @abstractmethod
    def write(self, record: dict[str, Any]) -> None: ...


class CSVSink(LeadSink):
    name = "csv"

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, record: dict[str, Any]) -> None:
        row = _flatten(record)
        exists = self.path.exists() and self.path.stat().st_size > 0
        with self.path.open("a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDNAMES, extrasaction="ignore")
            if not exists:
                writer.writeheader()
            writer.writerow(row)
        log.info("csv append -> %s", self.path)


class SQLiteSink(LeadSink):
    name = "sqlite"

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS leads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    received_at TEXT, contact_id TEXT, name TEXT, email TEXT,
                    phone TEXT, source TEXT, tags TEXT, location_id TEXT,
                    custom_fields TEXT
                )"""
            )

    def write(self, record: dict[str, Any]) -> None:
        row = _flatten(record)
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """INSERT INTO leads
                   (received_at, contact_id, name, email, phone, source, tags,
                    location_id, custom_fields)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                tuple(row.get(f) for f in FIELDNAMES),
            )
        log.info("sqlite insert -> %s", self.path)


class GSheetSink(LeadSink):
    """Append a row to a Google Sheet via gspread.

    Requires:
      GSHEET_ID                    - spreadsheet id
      GOOGLE_APPLICATION_CREDENTIALS - path to a service-account JSON key,
                                       with the sheet shared to that account.
    Optional: GSHEET_WORKSHEET (default 'Leads').
    """

    name = "gsheet"

    def __init__(self) -> None:
        import gspread  # imported lazily so the default path needs no dep

        sheet_id = os.environ["GSHEET_ID"]
        creds_path = os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
        self.worksheet_name = os.getenv("GSHEET_WORKSHEET", "Leads")

        gc = gspread.service_account(filename=creds_path)
        sh = gc.open_by_key(sheet_id)
        try:
            self.ws = sh.worksheet(self.worksheet_name)
        except gspread.WorksheetNotFound:
            self.ws = sh.add_worksheet(
                self.worksheet_name, rows=1000, cols=len(FIELDNAMES)
            )
            self.ws.append_row(FIELDNAMES)

    def write(self, record: dict[str, Any]) -> None:
        row = _flatten(record)
        self.ws.append_row([row.get(f, "") for f in FIELDNAMES])
        log.info("gsheet append_row -> %s", self.worksheet_name)


def build_sink() -> LeadSink:
    kind = os.getenv("SINK", "csv").lower()
    if kind == "sqlite":
        return SQLiteSink(os.getenv("SQLITE_PATH", "data/leads.db"))
    if kind == "gsheet":
        return GSheetSink()
    return CSVSink(os.getenv("CSV_PATH", "data/leads.csv"))
