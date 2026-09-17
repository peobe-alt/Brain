"""The dashboard and its JSON endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from carexpert.pipeline import scan
from carexpert.schemas import SearchQuery


@pytest.fixture()
def client(demo_market):
    scan(demo_market, sources=[], query=SearchQuery(limit=260,
         countries=["FR", "DE", "IT", "BE", "ES", "NL"]))
    demo_market.commit()
    from carexpert.api.app import app

    return TestClient(app)


def test_dashboard_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "CarExpert" in response.text


def test_health_reports_listings(client):
    payload = client.get("/api/health").json()
    assert payload["status"] == "ok"
    assert payload["listings"] > 0


def test_deals_endpoint_respects_the_score_filter(client):
    payload = client.get("/api/deals?min_score=75&limit=10").json()
    assert payload["count"] >= 1
    assert all(deal["score"] >= 75 for deal in payload["deals"])


def test_listing_page_shows_the_analysis(client):
    best = client.get("/api/deals?min_score=0&limit=1").json()["deals"][0]
    page = client.get(f"/listing/{best['id']}")
    assert page.status_code == 200
    assert str(best["score"]) in page.text
    assert "score" in client.get(f"/api/listing/{best['id']}").json()


def test_unknown_listing_returns_404(client):
    assert client.get("/listing/999999").status_code == 404
