"""Forwarder behaviour, including the no-op when FORWARD_URL is unset."""

from __future__ import annotations

import httpx

from sinks.forward import Forwarder


def test_forward_noop_when_url_missing(monkeypatch):
    monkeypatch.delenv("FORWARD_URL", raising=False)
    fwd = Forwarder()
    result = fwd.send({"contact_id": "x"})
    assert result == {"skipped": True, "reason": "FORWARD_URL not set"}


def test_forward_posts_when_url_set(monkeypatch):
    calls = {}

    def fake_post(url, json, headers, timeout):
        calls["url"] = url
        calls["json"] = json
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setenv("FORWARD_URL", "https://example.test/hook")
    monkeypatch.setattr(httpx, "post", fake_post)

    fwd = Forwarder()
    result = fwd.send({"contact_id": "abc"})
    assert result["ok"] is True
    assert result["status"] == 200
    assert calls["url"] == "https://example.test/hook"
    assert calls["json"]["contact_id"] == "abc"
