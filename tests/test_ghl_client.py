"""Outbound GHL API v2 client, tested against a mocked HTTP transport."""

from __future__ import annotations

import httpx

from ghl_client import GHLClient

RECORD = {
    "contact_id": None,
    "name": "Jan Kowalski",
    "email": "jan@example.com",
    "phone": "+48501234567",
    "source": "Facebook",
    "tags": "new-lead,fb",
    "location_id": "loc_1",
}


def _client_with(handler):
    transport = httpx.MockTransport(handler)
    http = httpx.Client(
        transport=transport, base_url="https://services.leadconnectorhq.com"
    )
    return GHLClient(api_key="test-key", client=http)


def test_disabled_without_api_key():
    ghl = GHLClient(api_key="")
    result = ghl.upsert_contact(RECORD)
    assert result["skipped"] is True


def test_create_contact_builds_payload_and_posts():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        seen["version"] = request.headers.get("Version")
        import json as _json

        seen["body"] = _json.loads(request.content)
        return httpx.Response(201, json={"contact": {"id": "new-1"}})

    ghl = _client_with(handler)
    result = ghl.create_contact(RECORD)

    assert result["ok"] is True
    assert result["status"] == 201
    assert result["data"]["contact"]["id"] == "new-1"
    assert seen["method"] == "POST"
    assert seen["url"].endswith("/contacts/")
    assert seen["auth"] == "Bearer test-key"
    assert seen["version"]
    assert seen["body"]["firstName"] == "Jan"
    assert seen["body"]["lastName"] == "Kowalski"
    assert seen["body"]["email"] == "jan@example.com"
    assert seen["body"]["tags"] == ["new-lead", "fb"]
    assert seen["body"]["locationId"] == "loc_1"


def test_upsert_updates_when_contact_id_present():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"contact": {"id": "abc"}})

    ghl = _client_with(handler)
    result = ghl.upsert_contact({**RECORD, "contact_id": "abc"})

    assert result["ok"] is True
    assert seen["method"] == "PUT"
    assert seen["url"].endswith("/contacts/abc")


def test_retry_then_success():
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 2:
            return httpx.Response(503, json={"error": "temporary"})
        return httpx.Response(201, json={"contact": {"id": "ok"}})

    ghl = GHLClient(
        api_key="test-key",
        retries=3,
        client=httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="https://services.leadconnectorhq.com",
        ),
    )
    result = ghl.create_contact(RECORD)
    assert result["ok"] is True
    assert result["attempts"] == 2
    assert attempts["n"] == 2


def test_exhausts_retries_on_persistent_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    ghl = GHLClient(
        api_key="test-key",
        retries=2,
        client=httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="https://services.leadconnectorhq.com",
        ),
    )
    result = ghl.create_contact(RECORD)
    assert result["ok"] is False
    assert result["attempts"] == 2
