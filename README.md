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

| var                                   | meaning                              | default                |
| ------------------------------------- | ------------------------------------ | ---------------------- |
| `SINK`                                | `csv` \| `sqlite` \| `gsheet`        | `csv`                  |
| `CSV_PATH` / `SQLITE_PATH`            | file location                        | `data/leads.*`         |
| `FORWARD_URL`                         | external API to POST leads to        | empty (disabled)       |
| `FORWARD_TOKEN`                       | bearer token for forwarding          | empty                  |
| `FORWARD_RETRIES` / `FORWARD_TIMEOUT` | resilience knobs                     | `3` / `10`             |
| `WEBHOOK_SECRET`                      | HMAC secret for inbound verification | empty (disabled)       |
| `WEBHOOK_SIGNATURE_HEADER`            | header carrying the signature        | `X-Webhook-Signature`  |
| `GHL_API_KEY`                         | token for the outbound GHL push      | empty (disabled)       |
| `GHL_LOCATION_ID`                     | default GHL sub-account id           | empty                  |
| `GHL_API_BASE` / `GHL_API_VERSION`    | GHL API base and version header      | LeadConnector defaults |
| `GHL_RETRIES` / `GHL_TIMEOUT`         | outbound resilience knobs            | `3` / `10`             |

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
> can be sent either as an object or as GHL's `[{id, value}]` array, both are
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

## Endpoints

- `POST /webhook/ghl-lead` receives, validates, stores, forwards and (optionally)
  pushes the lead back into GHL. Returns the normalized record and per-step results.
- `GET /health` liveness plus the active sink and whether outbound GHL is enabled.
- `GET /leads?limit=N` returns the most recent stored leads (newest first, capped at 200) for a quick eyeball of what has arrived. Handy in dev and demos.

## Webhook signature verification

Set `WEBHOOK_SECRET` to require a signature on every inbound request. The caller
must send `HMAC-SHA256(secret, raw_request_body)` (hex, optionally `sha256=` prefixed)
in the header named by `WEBHOOK_SIGNATURE_HEADER` (default `X-Webhook-Signature`).
A missing or wrong signature is rejected with `401`. Comparison is constant-time.
When `WEBHOOK_SECRET` is unset, verification is disabled so the service runs out of
the box.

GHL's native webhook action does not sign requests, so use this when you put a proxy,
a middleware step, or your own relay in front, or point a signing caller at the endpoint.

## Idempotency

If a lead carries a `contact_id` that is already stored, the webhook returns
`{"ok": true, "duplicate": true}` without writing a second row or re-firing the
forward / outbound steps. This makes GHL retries and at-least-once delivery safe.
Dedup is backed by the SQLite sink (and the CSV sink); backends that cannot check
cheaply simply never report duplicates.

## Pushing back into GHL (outbound, `ghl_client.py`)

`ghl_client.GHLClient` talks to the GoHighLevel API v2 (LeadConnector) to
`create_contact` / `update_contact` / `upsert_contact` from a normalized record,
with bearer auth, the `Version` header, per-request timeout, and retry with
exponential backoff on transient statuses (`429`, `5xx`, ...). Set `GHL_API_KEY`
to enable it; without a key the push is a safe no-op. `upsert_contact` updates when
the record has a `contact_id` and creates otherwise.

## Running tests

```bash
.venv/bin/pip install -r requirements.txt   # includes pytest
.venv/bin/pytest -q
```

The suite (`tests/`) covers payload validation and aliases, the missing-identity
`422`, SQLite persistence, idempotent dedup, the `/leads` endpoint, HMAC signature
verification (accept / reject / disabled), the forwarder no-op, and the outbound
`ghl_client` against a mocked HTTP transport (no real key needed). CI runs the same
suite on Python 3.12 via GitHub Actions (`.github/workflows/ci.yml`).

## Files

- `main.py` FastAPI app, validation, normalization, routing.
- `sinks/sheet.py` `LeadSink` interface plus CSV / SQLite / gspread impls (with dedup and recent-lead reads).
- `sinks/forward.py` external-API forwarder with retry/timeout.
- `ghl_client.py` outbound GoHighLevel API v2 client (create/update/upsert contact).
- `security.py` optional HMAC signature verification for the inbound webhook.
- `tests/` pytest suite.
- `sample_payload.json` realistic GHL lead payload.
- `requirements.txt`, `.env.example`, `.github/workflows/ci.yml`.

---

## O autorze / About

Zbudowane przez Pawła Iwanka, **FluxLab**, automatyzacja procesów biznesowych i wdrożenia AI dla małych firm.

Strona: https://fluxlab.pl

Potrzebujesz podobnej automatyzacji na zamówienie? Napisz przez https://fluxlab.pl
