# GHL Lead Integration (webhook -> Sheet + external API)

A small, production-shaped FastAPI service that receives a **GoHighLevel (GHL)
new-lead webhook**, validates and normalizes it, stores it through a pluggable
storage layer, and forwards it to any external API with retry/timeout/logging.

This is a portfolio / proof piece for agency subcontract work: the same
building block behind most "connect GHL to X" jobs.

## What it does

```
GHL webhook  ->  POST /webhook/ghl-lead
                     |
                     |-- validate + normalize (pydantic)
                     |-- store via LeadSink (csv | sqlite | gsheet)
                     '-- forward to FORWARD_URL (retry/timeout)
```

- **Storage abstraction** (`sinks/sheet.py`): an interface `LeadSink` with three
  implementations. `csv` and `sqlite` work with **zero credentials** so the
  service runs and is verifiable out of the box; `gsheet` (gspread) is a
  drop-in for production.
- **Forwarding** (`sinks/forward.py`): POSTs the normalized record to a
  configurable URL with bearer token, retries and exponential backoff. If
  `FORWARD_URL` is unset it is a safe no-op.

## Run locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
```

Send a sample webhook:

```bash
curl -X POST http://127.0.0.1:8000/webhook/ghl-lead \
     -H "Content-Type: application/json" \
     -d @sample_payload.json
```

Response (abridged):

```json
{
  "ok": true,
  "stored_in": "csv",
  "record": {
    "contact_id": "aBcD1234efGh5678",
    "name": "Jan Kowalski",
    "email": "jan.kowalski@example.com",
    "phone": "+48501234567",
    "source": "Facebook Lead Ad - Summer Promo",
    "custom_fields": { "budget": "5000-10000", "...": "..." }
  },
  "forwarded": { "skipped": true, "reason": "FORWARD_URL not set" }
}
```

The lead is appended to `data/leads.csv`. Health check: `GET /health`.

## Configuration

Copy `.env.example` to `.env` (or export the vars). Key ones:

| var                                   | meaning                       | default          |
| ------------------------------------- | ----------------------------- | ---------------- |
| `SINK`                                | `csv` \| `sqlite` \| `gsheet` | `csv`            |
| `CSV_PATH` / `SQLITE_PATH`            | file location                 | `data/leads.*`   |
| `FORWARD_URL`                         | external API to POST leads to | empty (disabled) |
| `FORWARD_TOKEN`                       | bearer token for forwarding   | empty            |
| `FORWARD_RETRIES` / `FORWARD_TIMEOUT` | resilience knobs              | `3` / `10`       |

## Connecting the real GHL webhook

1. In GHL: **Automation -> Workflows -> add a "Webhook" action** (or use a
   _Inbound Webhook / Contact Created_ trigger).
2. Set the URL to your deployed endpoint, e.g.
   `https://your-host/webhook/ghl-lead`, method **POST**, format **JSON**.
3. Map the workflow fields to the payload keys. GHL uses several field-name
   conventions; this service accepts both `contactId`/`contact_id`,
   `firstName`+`lastName` or `name`, plus `email`, `phone`, `source`, `tags`,
   `customFields`, `locationId`. Unknown extra keys are accepted and ignored.
4. Secure it: put the service behind HTTPS and (recommended) add a shared
   secret header check, or restrict by GHL's source IPs.

> Tip: GHL's built-in webhook action sends a flat JSON object. Use the field
> picker (`{{contact.email}}` etc.) to populate keys. The `custom_fields` block
> can be sent either as an object or as GHL's `[{id, value}]` array — both are
> handled.

## Switching to Google Sheets (gspread)

1. Create a Google Cloud **service account**, download its JSON key.
2. Enable the **Google Sheets API** and **Drive API** for the project.
3. Share the target spreadsheet with the service account email (Editor).
4. Set env:
   ```bash
   SINK=gsheet
   GSHEET_ID=<spreadsheet id from the URL>
   GSHEET_WORKSHEET=Leads
   GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
   ```
5. `pip install gspread` (already in requirements) and restart. On first run the
   worksheet is created with a header row if missing; each lead is appended.

## Files

- `main.py` — FastAPI app, validation, normalization, routing.
- `sinks/sheet.py` — `LeadSink` interface + CSV / SQLite / gspread impls.
- `sinks/forward.py` — external-API forwarder with retry/timeout.
- `sample_payload.json` — realistic GHL lead payload.
- `requirements.txt`, `.env.example`.


---

## O autorze / About

Zbudowane przez Pawła Iwanka, **FluxLab**, automatyzacja procesów biznesowych i wdrożenia AI dla małych firm.

Strona: https://fluxlab.pl

Potrzebujesz podobnej automatyzacji na zamówienie? Napisz przez https://fluxlab.pl
