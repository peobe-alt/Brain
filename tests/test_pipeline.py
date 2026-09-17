"""Ingestion, deduplication and the full scan."""

from __future__ import annotations

from datetime import datetime, timedelta

from carexpert.db import Alert, Listing, Pricepoint, select
from carexpert.normalize import enrich
from carexpert.pipeline import fingerprint, ingest, mark_stale, scan
from carexpert.pipeline.dedupe import looks_like_same_car
from carexpert.schemas import ListingData, SearchQuery


def _listing(source="a", source_id="1", price=15000, **kwargs) -> ListingData:
    listing = ListingData(
        source=source, source_id=source_id, url=f"https://{source}/{source_id}",
        title=kwargs.pop("title", "Peugeot 308 SW BlueHDi 130 Allure 2019"),
        price=price, km=kwargs.pop("km", 98_000), year=kwargs.pop("year", 2019), **kwargs,
    )
    return enrich(listing)


def test_same_car_on_two_sites_shares_a_fingerprint():
    a = _listing(source="autoscout24", source_id="111")
    b = _listing(source="lacentrale", source_id="222", price=15400)
    assert fingerprint(a) == fingerprint(b)
    assert looks_like_same_car(a, b)


def test_different_mileage_means_different_car():
    a = _listing(km=98_000)
    b = _listing(km=140_000)
    assert fingerprint(a) != fingerprint(b)


def test_ingest_is_idempotent(session):
    ingest(session, [_listing()])
    ingest(session, [_listing()])
    assert session.execute(select(Listing)).scalars().all().__len__() == 1


def test_price_drop_is_recorded(session):
    ingest(session, [_listing(price=15_000)])
    stats = ingest(session, [_listing(price=13_500)])
    assert stats.price_drops
    prices = session.execute(select(Pricepoint.price_eur)).scalars().all()
    assert sorted(prices) == [13_500, 15_000]


def test_listings_without_price_are_skipped(session):
    stats = ingest(session, [_listing(price=None)])
    assert stats.skipped == 1
    assert session.execute(select(Listing)).scalars().all() == []


def test_stale_listings_are_deactivated(session):
    ingest(session, [_listing()])
    session.flush()
    count = mark_stale(session, "a", datetime.utcnow() + timedelta(days=1))
    assert count == 1
    assert session.execute(select(Listing)).scalar_one().active is False


def test_full_scan_on_the_demo_source(session):
    report = scan(session, sources=["demo"], query=SearchQuery(limit=120, countries=["FR"]))
    assert report.collected["demo"].created > 0
    assert report.valued > 0
    assert report.top
    assert all("score" in item for item in report.top)
    assert report.top[0]["score"] >= report.top[-1]["score"]


def test_scan_survives_a_broken_source(session):
    report = scan(session, sources=["demo", "source-inexistante"],
                  query=SearchQuery(limit=40, countries=["FR"]))
    assert report.errors
    assert report.collected["demo"].seen > 0


def test_watchlist_alerts_fire_once(session):
    from carexpert.alerts import create_watchlist

    create_watchlist(session, "golf", SearchQuery(countries=["FR"]), sources=["demo"],
                     min_score=70, channels=[])
    session.flush()
    first = scan(session, sources=["demo"], query=SearchQuery(limit=150, countries=["FR"]),
                 notify=True)
    alerts_after_first = session.execute(select(Alert)).scalars().all()
    second = scan(session, sources=["demo"], query=SearchQuery(limit=150, countries=["FR"]),
                  notify=True)
    alerts_after_second = session.execute(select(Alert)).scalars().all()
    assert first.valued > 0 and second.valued > 0
    assert len(alerts_after_first) == len(alerts_after_second)


def test_scan_ranks_real_bargains_above_traps(demo_market):
    """The product thesis, as a test."""
    session = demo_market
    report = scan(session, sources=[], query=SearchQuery(limit=260,
                  countries=["FR", "DE", "IT", "BE", "ES", "NL"]))
    top_ids = [item["id"] for item in report.top[:20]]
    rows = {row.id: row for row in session.execute(select(Listing)).scalars().all()}
    kinds = [(rows[i].raw or {}).get("demo_kind") for i in top_ids]
    assert kinds.count("trap") == 0
    assert kinds.count("bargain") >= 8
