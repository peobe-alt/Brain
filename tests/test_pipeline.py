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


def test_two_similar_but_distinct_cars_keep_distinct_identities():
    """Bucketing mileage would merge these, and shrink every comparable pool."""
    a = _listing(source_id="1", km=88_000)
    b = _listing(source_id="2", km=88_900)
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
                  notify=True, revalue_all=True)
    alerts_after_second = session.execute(select(Alert)).scalars().all()
    assert first.valued > 0 and second.valued > 0
    assert alerts_after_first, "la premiere passe doit alerter"
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


def test_the_whole_base_is_scored_not_a_capped_slice(session):
    """A cap would leave most of a real market silently unscored."""
    from carexpert.sources.demo import DemoSource

    listings = list(
        DemoSource(seed=5, size=1400).search(SearchQuery(limit=1400, countries=["FR"]))
    )
    ingest(session, listings)
    session.flush()
    total = len(session.execute(select(Listing)).scalars().all())
    assert total > 1000, "il faut depasser l'ancien plafond pour que le test ait un sens"

    report = scan(session, sources=[], query=SearchQuery(limit=5000, countries=["FR"]))
    assert report.valued == total
    unscored = session.execute(select(Listing).where(Listing.score.is_(None))).scalars().all()
    assert unscored == []


def test_a_second_scan_skips_what_is_still_fresh(session):
    query = SearchQuery(limit=200, countries=["FR"])
    first = scan(session, sources=["demo"], query=query)
    assert first.valued > 0
    assert first.skipped_fresh == 0

    second = scan(session, sources=[], query=query)
    assert second.valued == 0
    assert second.skipped_fresh == first.valued


def test_a_price_change_forces_a_new_valuation(session):
    ingest(session, [_listing(price=15_000)])
    session.flush()
    scan(session, sources=[], query=SearchQuery(limit=10, countries=["FR"]))

    # Same advert, lower price: it must be re-valued despite being fresh.
    stats = ingest(session, [_listing(price=12_000)])
    session.flush()
    assert stats.touched_ids
    report = scan(session, sources=[], query=SearchQuery(limit=10, countries=["FR"]))
    assert report.valued == 1


def test_price_drops_are_detected_in_one_query(session):
    from carexpert.pipeline.run import _price_drops

    ingest(session, [_listing(source_id="a", price=15_000),
                     _listing(source_id="b", price=9_000)])
    session.flush()
    ingest(session, [_listing(source_id="a", price=13_000)])
    session.flush()
    ids = [row.id for row in session.execute(select(Listing)).scalars().all()]
    dropped = _price_drops(session, ids)
    assert len(dropped) == 1
