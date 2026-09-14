"""Forward a normalized lead to an external HTTP API with retry/timeout.

Configured via env:
  FORWARD_URL     - target endpoint (if empty, forwarding is skipped -> no-op)
  FORWARD_TOKEN   - optional bearer token
  FORWARD_RETRIES - attempts on failure (default 3)
  FORWARD_TIMEOUT - per-request timeout seconds (default 10)
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

log = logging.getLogger("ghl-webhook.forward")


class Forwarder:
    def __init__(self) -> None:
        self.url = os.getenv("FORWARD_URL", "").strip()
        self.token = os.getenv("FORWARD_TOKEN", "").strip()
        self.retries = int(os.getenv("FORWARD_RETRIES", "3"))
        self.timeout = float(os.getenv("FORWARD_TIMEOUT", "10"))

    def send(self, record: dict[str, Any]) -> dict[str, Any]:
        if not self.url:
            log.info("FORWARD_URL not set -> forwarding skipped")
            return {"skipped": True, "reason": "FORWARD_URL not set"}

        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        last_error = None
        for attempt in range(1, self.retries + 1):
            try:
                resp = httpx.post(
                    self.url, json=record, headers=headers, timeout=self.timeout
                )
                resp.raise_for_status()
                log.info("forwarded ok attempt=%d status=%d", attempt, resp.status_code)
                return {"ok": True, "status": resp.status_code, "attempts": attempt}
            except Exception as exc:
                last_error = str(exc)
                log.warning(
                    "forward attempt %d/%d failed: %s", attempt, self.retries, exc
                )
                if attempt < self.retries:
                    time.sleep(min(2 ** (attempt - 1), 10))

        return {"ok": False, "error": last_error, "attempts": self.retries}
