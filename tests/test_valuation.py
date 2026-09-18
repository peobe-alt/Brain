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


def _cross_posted(session, sites: tuple[str, ...], price: float, km: int = 90_500):
    """The same physical car, advertised on several sites at once."""
    from carexpert.normalize import enrich
    from carexpert.pipeline.ingest import ingest
    from carexpert.schemas import ListingData

    rows = [
        enrich(ListingData(
            source=site, source_id="marchand", url=f"https://{site}/marchand",
            title="Volkswagen Golf 1.6 TDI 115 Confortline 2019",
            price=price, km=km, year=2019,
        ))
        for site in sites
    ]
    ingest(session, rows)
    session.flush()


def _healthy_market(session, count: int = 10):
    from carexpert.normalize import enrich
    from carexpert.pipeline.ingest import ingest
    from carexpert.schemas import ListingData

    rows = [
        enrich(ListingData(
            source="siteA", source_id=f"n{i}", url=f"https://siteA/n{i}",
            title="Volkswagen Golf 1.6 TDI 115 Confortline 2019",
            price=14_500 + i * 120, km=88_000 + i * 900, year=2019,
        ))
        for i in range(count)
    ]
    ingest(session, rows)
    session.flush()


def test_a_car_posted_on_four_sites_counts_once(session):
    """Dealers cross-post far more than private sellers, and price higher:
    counting each copy biases every estimate upward."""
    _healthy_market(session)
    _cross_posted(session, ("siteA", "siteB", "siteC", "siteD"), price=21_000)

    target = session.execute(select(Listing).where(Listing.source_id == "n0")).scalar_one()
    valuation = estimate(session, target)
    assert valuation.comps_count == 10          # 9 autres + 1 seule fois le marchand
    assert valuation.details["vehicules_distincts"] == 10


def test_deduplication_keeps_the_cheapest_copy(session):
    from carexpert.valuation.comps import deduplicate, to_facts

    _cross_posted(session, ("siteA",), price=21_000)
    _cross_posted(session, ("siteB",), price=19_500)
    rows = session.execute(select(Listing)).scalars().all()
    kept = deduplicate([to_facts(row) for row in rows])
    assert len(kept) == 1
    assert kept[0].price_eur == 19_500          # le prix auquel on peut vraiment l'acheter


def test_similar_but_distinct_cars_are_not_merged(session):
    """Nine cars 900 km apart are nine comparables, not one."""
    _healthy_market(session, count=10)
    target = session.execute(select(Listing).where(Listing.source_id == "n0")).scalar_one()
    valuation = estimate(session, target)
    assert valuation.comps_count == 9


def test_the_same_car_elsewhere_is_not_its_own_comparable(session):
    _healthy_market(session)
    _cross_posted(session, ("siteA", "siteB"), price=16_000)
    target = session.execute(
        select(Listing).where(Listing.source == "siteA", Listing.source_id == "marchand")
    ).scalar_one()
    valuation = estimate(session, target)
    urls = {example["url"] for example in valuation.details["exemples"]}
    assert "https://siteB/marchand" not in urls


def _same_model_two_engines(session, count: int = 10):
    """One model, two engines, two price levels.

    This is what a model-level comparison cannot see: both cars are called
    Scenic, both are petrol, both are 2018, and one is worth five thousand
    euros more than the other.
    """
    from carexpert.normalize import enrich
    from carexpert.pipeline.ingest import ingest
    from carexpert.schemas import ListingData

    rows = []
    for index in range(count):
        rows.append(enrich(ListingData(
            source="siteA", source_id=f"petit{index}", url=f"https://siteA/petit{index}",
            title="Renault Scenic Intens TCe 110 2018",
            price=11_000 + index * 100, km=90_000 + index * 800, year=2018, power_hp=110,
        )))
        rows.append(enrich(ListingData(
            source="siteA", source_id=f"gros{index}", url=f"https://siteA/gros{index}",
            title="Renault Scenic Intens TCe 160 2018",
            price=16_000 + index * 100, km=90_000 + index * 800, year=2018, power_hp=160,
        )))
    ingest(session, rows)
    session.flush()


def test_two_engines_of_the_same_model_are_not_comparables(session):
    from carexpert.valuation.comps import find_comparables, to_facts

    _same_model_two_engines(session)
    target = session.execute(select(Listing).where(Listing.source_id == "petit0")).scalar_one()

    comps, tier = find_comparables(session, to_facts(target))
    assert {comp.power_hp for comp in comps} == {110}
    assert tier == "strict"

    # And the estimate stays on the 110's price level instead of being
    # dragged halfway to the 160's.
    valuation = estimate(session, target)
    assert valuation.fair_price_eur < 13_000


def test_a_break_is_not_compared_to_a_saloon(session):
    """Same model, same engine, same year: the body alone moves the price."""
    from carexpert.normalize import enrich
    from carexpert.pipeline.ingest import ingest
    from carexpert.schemas import BodyType, ListingData
    from carexpert.valuation.comps import find_comparables, to_facts

    rows = []
    for index in range(10):
        rows.append(enrich(ListingData(
            source="siteA", source_id=f"berline{index}", url=f"https://siteA/b{index}",
            title="Peugeot 308 Allure BlueHDi 130 berline 2019",
            price=13_000 + index * 100, km=80_000 + index * 700, year=2019, power_hp=130,
        )))
        rows.append(enrich(ListingData(
            source="siteA", source_id=f"break{index}", url=f"https://siteA/sw{index}",
            title="Peugeot 308 SW Allure BlueHDi 130 2019",
            price=16_500 + index * 100, km=85_000 + index * 700, year=2019, power_hp=130,
        )))
    ingest(session, rows)
    session.flush()

    target = session.execute(select(Listing).where(Listing.source_id == "break0")).scalar_one()
    assert target.body == BodyType.ESTATE.value

    comps, _ = find_comparables(session, to_facts(target))
    assert comps, "le break doit garder ses propres comparables"
    assert all(comp.body != BodyType.SEDAN.value for comp in comps)


def test_an_advert_without_a_declared_power_stays_a_comparable(session):
    """A blank is silence, not a contradiction.

    Many sites publish no power at all. Dropping those adverts would cost
    far more comparables than the occasional mismatched engine, and the
    pairwise engine correction simply does not apply to them.
    """
    from carexpert.normalize import enrich
    from carexpert.pipeline.ingest import ingest
    from carexpert.schemas import ListingData
    from carexpert.valuation.comps import find_comparables, to_facts

    rows = [
        enrich(ListingData(
            source="siteA", source_id=f"n{index}", url=f"https://siteA/n{index}",
            title="Renault Scenic Intens TCe 110 2018",
            price=11_000 + index * 100, km=90_000 + index * 800, year=2018, power_hp=110,
        ))
        for index in range(9)
    ]
    rows.append(enrich(ListingData(
        source="siteA", source_id="muet", url="https://siteA/muet",
        title="Renault Scenic Intens essence 2018",
        price=11_400, km=97_500, year=2018,
    )))
    ingest(session, rows)
    session.flush()

    muet = session.execute(select(Listing).where(Listing.source_id == "muet")).scalar_one()
    assert muet.power_hp is None

    target = session.execute(select(Listing).where(Listing.source_id == "n0")).scalar_one()
    comps, _ = find_comparables(session, to_facts(target))
    assert "https://siteA/muet" in {comp.url for comp in comps}


def test_the_engine_gap_is_corrected_when_it_cannot_be_avoided():
    """Past the tiers, the remaining gap is priced rather than ignored."""
    from carexpert.valuation.adjust import POWER_RATIO_CAP, power_ratio

    assert power_ratio(160, 110) > 1.0          # la cible est plus puissante
    assert power_ratio(110, 160) < 1.0
    assert power_ratio(130, 130) == 1.0
    # Silence on either side must never scale a price.
    assert power_ratio(None, 130) == 1.0
    assert power_ratio(130, None) == 1.0
    assert power_ratio(0, 130) == 1.0
    # Never let the engine alone explain a whole price gap.
    assert power_ratio(600, 60) <= POWER_RATIO_CAP
    assert power_ratio(60, 600) >= 1 / POWER_RATIO_CAP


def test_a_version_is_reduced_to_what_names_the_finish():
    from carexpert.valuation.comps import same_trim, trim_tokens

    assert trim_tokens("SW GT BlueHDi 130") == frozenset({"gt"})
    assert trim_tokens("Allure PureTech 130") == frozenset({"allure"})

    allure = trim_tokens("Allure PureTech 130")
    assert not same_trim(allure, trim_tokens("SW GT BlueHDi 130"))
    assert same_trim(allure, trim_tokens("Allure Business BlueHDi 100"))
    # A version nobody wrote cannot disagree with one that was written.
    assert same_trim(allure, trim_tokens(None))
    assert same_trim(trim_tokens(None), allure)
