"""Tests for the API's owner-access guard."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from badactors.server.app import create_app


@pytest.fixture(autouse=True)
def _no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BADACTORS_API_TOKEN", raising=False)


def test_health_is_open_even_with_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BADACTORS_API_TOKEN", "owner")
    response = TestClient(create_app()).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_token_is_required_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BADACTORS_API_TOKEN", "owner")
    client = TestClient(create_app())
    assert client.get("/anything").status_code == 401
    wrong = {"Authorization": "Bearer guess"}
    assert client.get("/anything", headers=wrong).status_code == 401
    right = {"Authorization": "Bearer owner"}
    assert client.get("/anything", headers=right).status_code == 404


def test_remote_clients_need_a_token() -> None:
    client = TestClient(create_app(), client=("203.0.113.9", 50000))
    assert client.get("/anything").status_code == 403


def test_loopback_hostname_is_required_without_a_token() -> None:
    client = TestClient(create_app(), base_url="http://castle.example")
    assert client.get("/anything").status_code == 403


def test_cross_origin_writes_are_refused() -> None:
    client = TestClient(create_app())
    foreign = {"Origin": "http://evil.example"}
    assert client.post("/anything", headers=foreign).status_code == 403
    same = {"Origin": "http://testserver"}
    assert client.post("/anything", headers=same).status_code == 404
