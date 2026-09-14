"""Outbound client for the GoHighLevel API v2 (LeadConnector).

Creates or updates a contact in GHL from a normalized lead record. This is the
"push back into GHL" direction, complementing the inbound webhook: an agency can
receive a lead from anywhere and materialize it as a GHL contact.

Configuration (env):
  GHL_API_KEY      - Private Integration / API token (Bearer). Required to send.
  GHL_LOCATION_ID  - default location (sub-account) id used when a record has none.
  GHL_API_BASE     - API base (default https://services.leadconnectorhq.com).
  GHL_API_VERSION  - API version header (default 2021-07-28).
  GHL_TIMEOUT      - per-request timeout seconds (default 10).
  GHL_RETRIES      - attempts on transient failure (default 3).

The client is import-safe without a key; sending without a key is a no-op that
returns {"skipped": True, ...} so nothing crashes in test/dev.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

import httpx

log = logging.getLogger("ghl-webhook.ghl-client")

_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class GHLClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        location_id: Optional[str] = None,
        base_url: Optional[str] = None,
        api_version: Optional[str] = None,
        timeout: Optional[float] = None,
        retries: Optional[int] = None,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self.api_key = (
            api_key if api_key is not None else os.getenv("GHL_API_KEY", "")
        ).strip()
        self.location_id = (
            location_id if location_id is not None else os.getenv("GHL_LOCATION_ID", "")
        ).strip()
        self.base_url = (
            base_url
            or os.getenv("GHL_API_BASE", "https://services.leadconnectorhq.com")
        ).rstrip("/")
        self.api_version = api_version or os.getenv("GHL_API_VERSION", "2021-07-28")
        self.timeout = (
            timeout if timeout is not None else float(os.getenv("GHL_TIMEOUT", "10"))
        )
        self.retries = (
            retries if retries is not None else int(os.getenv("GHL_RETRIES", "3"))
        )
        # Injected client is used as-is (handy for tests with a MockTransport).
        self._client = client

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Version": self.api_version,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _http(self) -> httpx.Client:
        if self._client is not None:
            return self._client
        return httpx.Client(base_url=self.base_url, timeout=self.timeout)

    @staticmethod
    def _split_name(record: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
        name = (record.get("name") or "").strip()
        if not name:
            return None, None
        parts = name.split()
        if len(parts) == 1:
            return parts[0], None
        return parts[0], " ".join(parts[1:])

    def _to_contact_payload(self, record: dict[str, Any]) -> dict[str, Any]:
        first, last = self._split_name(record)
        payload: dict[str, Any] = {}
        if first:
            payload["firstName"] = first
        if last:
            payload["lastName"] = last
        if record.get("email"):
            payload["email"] = record["email"]
        if record.get("phone"):
            payload["phone"] = record["phone"]
        if record.get("source"):
            payload["source"] = record["source"]
        tags = record.get("tags")
        if isinstance(tags, str) and tags:
            payload["tags"] = [t for t in tags.split(",") if t]
        elif isinstance(tags, list) and tags:
            payload["tags"] = tags
        loc = record.get("location_id") or self.location_id
        if loc:
            payload["locationId"] = loc
        return payload

    def _request(
        self, method: str, path: str, json_body: dict[str, Any]
    ) -> dict[str, Any]:
        if not self.enabled:
            log.info("GHL_API_KEY not set -> outbound push skipped")
            return {"skipped": True, "reason": "GHL_API_KEY not set"}

        url = path if self._client is not None else f"{self.base_url}{path}"
        last_error: Optional[str] = None
        client = self._http()
        own = self._client is None
        try:
            for attempt in range(1, self.retries + 1):
                try:
                    resp = client.request(
                        method,
                        url,
                        json=json_body,
                        headers=self._headers(),
                        timeout=self.timeout,
                    )
                    if resp.status_code in _RETRYABLE_STATUS:
                        raise httpx.HTTPStatusError(
                            f"retryable status {resp.status_code}",
                            request=resp.request,
                            response=resp,
                        )
                    resp.raise_for_status()
                    data = resp.json() if resp.content else {}
                    log.info(
                        "GHL %s %s ok status=%d attempt=%d",
                        method,
                        path,
                        resp.status_code,
                        attempt,
                    )
                    return {
                        "ok": True,
                        "status": resp.status_code,
                        "attempts": attempt,
                        "data": data,
                    }
                except Exception as exc:  # noqa: BLE001 - deliberate retry envelope
                    last_error = str(exc)
                    log.warning(
                        "GHL %s %s attempt %d/%d failed: %s",
                        method,
                        path,
                        attempt,
                        self.retries,
                        exc,
                    )
                    if attempt < self.retries:
                        time.sleep(min(2 ** (attempt - 1), 10))
            return {"ok": False, "error": last_error, "attempts": self.retries}
        finally:
            if own:
                client.close()

    def create_contact(self, record: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/contacts/", self._to_contact_payload(record))

    def update_contact(self, contact_id: str, record: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "PUT", f"/contacts/{contact_id}", self._to_contact_payload(record)
        )

    def upsert_contact(self, record: dict[str, Any]) -> dict[str, Any]:
        """Update when the record carries a GHL contact_id, otherwise create."""
        contact_id = record.get("contact_id")
        if contact_id:
            return self.update_contact(str(contact_id), record)
        return self.create_contact(record)
