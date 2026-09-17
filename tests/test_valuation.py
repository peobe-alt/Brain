"""Does the price estimate actually track the market?"""

from __future__ import annotations

import statistics

from carexpert.db import Listing, select
from carexpert.valuation import estimate


def test_estimate_recovers_the_synthetic_market(demo_market):
    """The demo market has a known fair price; we should land close to it."""
    session = demo_market
    errors = []
    for row in session.execute(select(Listing)).scalars().all():
        truth = (row.raw or {}).get("demo_fair_price")
        if not truth:
            continue
        valuation = estimate(session, row)
        if valuation.comps_count < 6:
            continue
        errors.append(abs(valuation.fair_price_eur - truth) / truth)

    assert len(errors) > 100, "echantillon trop petit pour conclure"
    assert statistics.median(errors) < 0.12


def test_bargains_and_traps_are_both_flagged_as_cheap(demo_market):
    """Valuation alone cannot tell them apart: that is the scorer's job."""
    session = demo_market
    deltas = {"bargain": [], "trap": [], "fair": []}
    for row in session.execute(select(Listing)).scalars().all():
        kind = (row.raw or {}).get("demo_kind")
        if kind not in deltas:
            continue
        valuation = estimate(session, row)
        if valuation.comps_count >= 6:
            deltas[kind].append(valuation.delta_pct)

    assert statistics.median(deltas["bargain"]) > 0.10
    assert statistics.median(deltas["trap"]) > 0.20
    assert abs(statistics.median(deltas["fair"])) < 0.08


def test_no_comparables_means_no_confidence(session):
    from carexpert.normalize import enrich
    from carexpert.pipeline.ingest import ingest
    from carexpert.schemas import ListingData

    listing = ListingData(
        source="t", source_id="solo", url="u",
        title="Lamborghini Diablo VT 1994", price=280000, km=42000, year=1994,
    )
    enrich(listing)
    ingest(session, [listing])
    session.flush()
    row = session.execute(select(Listing).where(Listing.source_id == "solo")).scalar_one()

    valuation = estimate(session, row)
    assert valuation.comps_count == 0
    assert valuation.confidence == 0.0
    assert valuation.delta_pct == 0.0


def test_confidence_rises_with_the_sample(demo_market):
    session = demo_market
    rows = session.execute(select(Listing)).scalars().all()
    valuations = [estimate(session, row) for row in rows[:60]]
    with_comps = [v for v in valuations if v.comps_count >= 10]
    thin = [v for v in valuations if 0 < v.comps_count < 5]
    if with_comps and thin:
        assert statistics.mean(v.confidence for v in with_comps) > statistics.mean(
            v.confidence for v in thin
        )


def test_refitting_depreciation_recovers_planted_rates():
    from carexpert.valuation.adjust import fit_depreciation

    samples = [
        (age, km, 30000 * (0.875**age) * (0.70 ** (km / 150_000)))
        for age in range(1, 9)
        for km in (20_000, 60_000, 100_000, 160_000)
    ]
    age_rate, km_rate = fit_depreciation(samples)
    assert abs(age_rate - 0.125) < 0.02
    assert abs(km_rate - 0.30) < 0.03
