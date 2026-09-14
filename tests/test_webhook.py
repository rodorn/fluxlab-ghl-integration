"""Inbound webhook: validation, aliases, persistence, idempotency, signature."""

from __future__ import annotations

import sqlite3

from security import compute_signature


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["sink"] == "sqlite"


def test_alias_contactId_is_accepted(client):
    r = client.post(
        "/webhook/ghl-lead", json={"contactId": "abc123", "email": "a@b.com"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["record"]["contact_id"] == "abc123"


def test_snake_case_contact_id_is_accepted(client):
    r = client.post(
        "/webhook/ghl-lead", json={"contact_id": "snake1", "phone": "+48500"}
    )
    assert r.status_code == 200
    assert r.json()["record"]["contact_id"] == "snake1"


def test_missing_identity_returns_422(client):
    r = client.post("/webhook/ghl-lead", json={"source": "nowhere", "tags": ["x"]})
    assert r.status_code == 422
    assert "identity" in r.json()["detail"]


def test_invalid_email_returns_422(client):
    r = client.post(
        "/webhook/ghl-lead", json={"contact_id": "x", "email": "not-an-email"}
    )
    assert r.status_code == 422


def test_record_is_persisted_to_sqlite(make_client, tmp_path):
    c, main = make_client()
    payload = {
        "contactId": "persist-1",
        "firstName": "Jan",
        "lastName": "Kowalski",
        "email": "jan@example.com",
        "phone": "+48501234567",
        "tags": ["new-lead"],
    }
    r = c.post("/webhook/ghl-lead", json=payload)
    assert r.status_code == 200

    db_path = main.sink.path
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT contact_id, name, email, tags FROM leads WHERE contact_id = ?",
            ("persist-1",),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "persist-1"
    assert rows[0][1] == "Jan Kowalski"
    assert rows[0][2] == "jan@example.com"
    assert rows[0][3] == "new-lead"


def test_idempotent_dedup_by_contact_id(make_client):
    c, main = make_client()
    payload = {"contactId": "dup-1", "email": "dup@example.com"}

    first = c.post("/webhook/ghl-lead", json=payload)
    assert first.status_code == 200
    assert first.json()["duplicate"] is False

    second = c.post("/webhook/ghl-lead", json=payload)
    assert second.status_code == 200
    assert second.json()["duplicate"] is True

    with sqlite3.connect(main.sink.path) as conn:
        (count,) = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE contact_id = ?", ("dup-1",)
        ).fetchone()
    assert count == 1


def test_leads_endpoint_returns_recent(make_client):
    c, _main = make_client()
    for i in range(3):
        c.post(
            "/webhook/ghl-lead", json={"contactId": f"lead-{i}", "email": f"{i}@x.com"}
        )

    r = c.get("/leads?limit=2")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    # newest first
    assert body["leads"][0]["contact_id"] == "lead-2"


def test_signature_required_when_secret_set(make_client):
    c, _main = make_client(WEBHOOK_SECRET="s3cr3t")
    r = c.post("/webhook/ghl-lead", json={"contact_id": "x", "email": "a@b.com"})
    assert r.status_code == 401


def test_valid_signature_passes(make_client):
    secret = "s3cr3t"
    c, _main = make_client(WEBHOOK_SECRET=secret)
    body = b'{"contact_id": "sig-1", "email": "a@b.com"}'
    sig = compute_signature(secret, body)
    r = c.post(
        "/webhook/ghl-lead",
        content=body,
        headers={"Content-Type": "application/json", "X-Webhook-Signature": sig},
    )
    assert r.status_code == 200
    assert r.json()["record"]["contact_id"] == "sig-1"


def test_bad_signature_rejected(make_client):
    c, _main = make_client(WEBHOOK_SECRET="s3cr3t")
    body = b'{"contact_id": "sig-2", "email": "a@b.com"}'
    r = c.post(
        "/webhook/ghl-lead",
        content=body,
        headers={"Content-Type": "application/json", "X-Webhook-Signature": "deadbeef"},
    )
    assert r.status_code == 401
