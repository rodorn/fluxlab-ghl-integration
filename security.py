"""Optional HMAC signature verification for the inbound webhook.

When WEBHOOK_SECRET is set, every request to the webhook must carry an
HMAC-SHA256 of the raw request body in a header (default X-Webhook-Signature).
When the secret is unset, verification is disabled (no-op) so the service still
runs out of the box.

The signature is compared with hmac.compare_digest to avoid timing leaks, and
both a bare hex digest and a "sha256=" prefixed form are accepted.
"""

from __future__ import annotations

import hashlib
import hmac
import os


def signature_header_name() -> str:
    return os.getenv("WEBHOOK_SIGNATURE_HEADER", "X-Webhook-Signature")


def compute_signature(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify_signature(body: bytes, provided: str | None) -> bool:
    """Return True if the request is authorized.

    No secret configured -> always True (verification disabled).
    Secret configured -> the provided header must match the HMAC of the body.
    """
    secret = os.getenv("WEBHOOK_SECRET", "").strip()
    if not secret:
        return True
    if not provided:
        return False
    provided = provided.strip()
    if provided.lower().startswith("sha256="):
        provided = provided[len("sha256=") :]
    expected = compute_signature(secret, body)
    return hmac.compare_digest(expected, provided)
