"""API tests.

Uses FastAPI's TestClient. The journal is monkeypatched to an in-memory stub so
these run without Postgres — same principle as the agent tests: CI-safe, no live
infra required.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402


class _StubJournal:
    def __init__(self) -> None:
        self.records: list = []

    def record(self, shipment, event, resolution, model_name=""):
        self.records.append((shipment, event, resolution))

    def recent_resolutions(self, limit=50):
        return []

    def resolutions_for_shipment(self, shipment_id):
        return []

    def record_human_override(self, resolution_id, corrected_type):
        if resolution_id == "missing":
            raise KeyError(resolution_id)


@pytest.fixture
def client(monkeypatch):
    # Patch the journal + LLM before importing the app module's globals.
    import waybill.db.repository as repo
    monkeypatch.setattr(repo, "Journal", _StubJournal)

    import importlib
    import waybill.api.app as appmod
    importlib.reload(appmod)
    return TestClient(appmod.app)


def test_submit_exception_returns_decision(client):
    resp = client.post("/exceptions", json={
        "tracking_number": "ABC123456789",
        "carrier": "DHL",
        "raw_message": "Shipment held by customs pending inspection",
        "origin": "Shanghai",
        "destination": "Hamburg",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["exception_type"] in {
        "customs_hold", "delay", "damaged_goods", "missing_docs",
        "address_issue", "lost", "unknown",
    }
    assert body["disposition"] in {"auto_resolved", "escalated", "needs_info"}
    assert "resolution_id" in body


def test_health_is_live(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_metrics_endpoint(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "waybill_up" in resp.text
