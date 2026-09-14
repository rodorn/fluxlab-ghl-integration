"""Shared pytest fixtures.

The app builds its sink / forwarder / GHL client at import time from env vars,
so each test that needs a specific config sets the env and reloads `main`.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Make the project root importable when pytest is run from anywhere.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _fresh_app(monkeypatch, tmp_path, **env):
    monkeypatch.setenv("SINK", "sqlite")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "leads.db"))
    monkeypatch.delenv("FORWARD_URL", raising=False)
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("GHL_API_KEY", raising=False)
    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)

    import main

    main = importlib.reload(main)
    return main


@pytest.fixture
def make_client(monkeypatch, tmp_path):
    def _factory(**env):
        main = _fresh_app(monkeypatch, tmp_path, **env)
        return TestClient(main.app), main

    return _factory


@pytest.fixture
def client(make_client):
    c, _main = make_client()
    return c
