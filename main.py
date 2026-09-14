"""GHL lead webhook receiver.

Accepts a GoHighLevel "new lead / contact" webhook, validates it, normalizes
it into a canonical record, persists it through a storage abstraction (CSV /
SQLite fallback, or gspread when configured) and forwards it to an external
API with retry/timeout/logging.

Run:
    uvicorn main:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from sinks.sheet import build_sink
from sinks.forward import Forwarder

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("ghl-webhook")

app = FastAPI(title="GHL Lead Integration", version="1.0.0")

sink = build_sink()
forwarder = Forwarder()


class GHLLead(BaseModel):
    """Loosely typed inbound payload matching a GHL contact/lead webhook.

    GHL sends different field names depending on trigger/version, so we accept
    common aliases and treat everything but an identity signal as optional.
    """

    contact_id: Optional[str] = Field(default=None, alias="contactId")
    name: Optional[str] = None
    first_name: Optional[str] = Field(default=None, alias="firstName")
    last_name: Optional[str] = Field(default=None, alias="lastName")
    email: Optional[str] = None
    phone: Optional[str] = None
    source: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    custom_fields: dict[str, Any] = Field(default_factory=dict, alias="customFields")
    location_id: Optional[str] = Field(default=None, alias="locationId")

    model_config = {"populate_by_name": True, "extra": "allow"}

    @field_validator("email")
    @classmethod
    def _email_shape(cls, v: Optional[str]) -> Optional[str]:
        if v and "@" not in v:
            raise ValueError("email must contain '@'")
        return v


def normalize(lead: GHLLead) -> dict[str, Any]:
    """Map a raw GHL payload onto the canonical record shape."""
    full_name = (
        lead.name
        or " ".join(p for p in (lead.first_name, lead.last_name) if p).strip()
        or None
    )

    cf = lead.custom_fields
    if isinstance(cf, list):  # GHL sometimes sends [{id, value}, ...]
        cf = {str(item.get("id", i)): item.get("value") for i, item in enumerate(cf)}

    return {
        "received_at": datetime.now(timezone.utc).isoformat(),
        "contact_id": lead.contact_id,
        "name": full_name,
        "email": lead.email,
        "phone": lead.phone,
        "source": lead.source,
        "tags": ",".join(lead.tags),
        "location_id": lead.location_id,
        "custom_fields": cf,
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "sink": sink.name}


@app.post("/webhook/ghl-lead")
async def ghl_lead(request: Request) -> dict[str, Any]:
    try:
        raw = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")

    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="expected a JSON object")

    try:
        lead = GHLLead.model_validate(raw)
    except Exception as exc:  # pydantic ValidationError
        log.warning("validation failed: %s", exc)
        raise HTTPException(status_code=422, detail=str(exc))

    if not (lead.contact_id or lead.email or lead.phone):
        raise HTTPException(
            status_code=422,
            detail="need at least one identity field: contact_id, email or phone",
        )

    record = normalize(lead)
    sink.write(record)
    log.info(
        "stored lead contact_id=%s email=%s", record["contact_id"], record["email"]
    )

    forward_result = forwarder.send(record)

    return {
        "ok": True,
        "stored_in": sink.name,
        "record": record,
        "forwarded": forward_result,
    }
