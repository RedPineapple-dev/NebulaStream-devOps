"""Unit tests for auth and rate limiting middleware."""

import pytest
from fastapi.testclient import TestClient
from server import app


@pytest.fixture
def client():
    return TestClient(app)


def test_auth_dev_mode(client, monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "")
    # Should succeed in dev mode without header
    res = client.post("/chaos/inject", json={"region": "eu-west", "delay": 300})
    assert res.status_code == 200


def test_auth_enforced_when_configured(client, monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "super-secret-key")
    
    # Missing header -> 403
    res = client.post("/chaos/inject", json={"region": "eu-west", "delay": 300})
    assert res.status_code == 403

    # Wrong header -> 403
    res = client.post(
        "/chaos/inject",
        json={"region": "eu-west", "delay": 300},
        headers={"X-API-KEY": "wrong-key"}
    )
    assert res.status_code == 403

    # Correct header -> 200
    res = client.post(
        "/chaos/inject",
        json={"region": "eu-west", "delay": 300},
        headers={"X-API-KEY": "super-secret-key"}
    )
    assert res.status_code == 200
