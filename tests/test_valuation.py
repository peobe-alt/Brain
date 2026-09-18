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


def test_a_real_car_is_never_priced_against_invented_ones(session):
    """Le marche synthetique ne doit jamais servir a fixer un prix reel.

    Mesure avant correction, sur ce meme scenario: la Golf reelle etait
    estimee a 6 935 EUR face a 10 comparables inventes, avec 0,58 de
    confiance, et sortait "A FUIR". La confiance ne protege pas: de fausses
    annonces sont parfaitement coherentes entre elles.
    """
    from carexpert.pipeline.ingest import ingest
    from carexpert.schemas import Fuel, Gearbox, ListingData
    from carexpert.sources.demo import DemoSource
    from carexpert.schemas import SearchQuery
    from carexpert.valuation import estimate
    from carexpert.db import Listing

    def golf(source, source_id, price, km):
        # Des kilometrages distincts: sinon la deduplication les considere
        # comme un seul et meme vehicule, ce qui est son role.
        return ListingData(
            source=source, source_id=source_id,
            url=f"https://{source}.test/{source_id}",
            title="Volkswagen Golf 1.6 TDI", price=price, price_eur=price,
            make="Volkswagen", model="Golf", year=2016, km=km,
            fuel=Fuel.DIESEL, gearbox=Gearbox.MANUAL,
        )

    ingest(session, list(DemoSource(seed=3, size=200).search(SearchQuery(limit=200))))
    ingest(session, [golf("demo", f"fake{i}", 4000, 140000 + 900 * i) for i in range(12)])
    ingest(session, [golf("autoscout24", f"real{i}", 12000, 140000 + 900 * i) for i in range(12)])
    session.flush()

    real = session.query(Listing).filter(Listing.source_id == "real0").one()
    valuation = estimate(session, real)
    assert valuation.comps_count > 0
    # Les fausses Golf a 4 000 EUR ne tirent pas l'estimation vers le bas.
    assert valuation.fair_price_eur > 9000


def test_valuation_does_not_load_what_it_never_reads(session):
    """Mesure : 31 ms par estimation sur 1 860 annonces d'un meme modele.

    Chaque estimation chargeait 400 lignes completes, donc deserialisait
    depuis JSON les photos et la charge brute de chaque comparable pour les
    jeter aussitot : 1 200 `json.loads` par estimation, dont les deux tiers
    inutiles. En ne lisant que les colonnes dont `Facts` a besoin, 8 ms.
    """
    from carexpert.db import Listing
    from carexpert.pipeline.ingest import ingest
    from carexpert.schemas import Fuel, Gearbox, ListingData, Photo
    from carexpert.valuation.comps import FACTS_COLUMNS, find_comparables, to_facts

    photos = [Photo(url=f"https://site.fr/img{i}.jpg") for i in range(30)]
    ingest(session, [
        ListingData(
            source="autoscout24", source_id=f"g{i}", url=f"https://site.fr/{i}",
            title="Volkswagen Golf 1.6 TDI 110", price=9000 + i, price_eur=9000 + i,
            make="Volkswagen", model="Golf", year=2016, km=100000 + 700 * i,
            fuel=Fuel.DIESEL, gearbox=Gearbox.MANUAL, photos=list(photos),
            extra={"payload": "x" * 2000},
        )
        for i in range(60)
    ])
    session.flush()

    names = {column.key for column in FACTS_COLUMNS}
    assert "photos" not in names and "raw" not in names
    assert "options" in names          # la valorisation, elle, s'en sert

    target = session.query(Listing).filter(Listing.source_id == "g0").one()
    comps, tier = find_comparables(session, to_facts(target), min_count=8)
    assert len(comps) >= 8
    assert all(c.make == "Volkswagen" and c.price_eur for c in comps)
    assert all(c.listing_id and c.fingerprint for c in comps)
