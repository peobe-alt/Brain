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


def _car(session, source_id, **kwargs):
    from carexpert.normalize import enrich
    from carexpert.pipeline.ingest import ingest
    from carexpert.schemas import Fuel, Gearbox, ListingData

    defaults = {
        "title": "Volkswagen Golf 1.6 TDI 110 Confortline", "price": 12_000, "km": 120_000,
        "year": 2016, "fuel": Fuel.DIESEL, "gearbox": Gearbox.MANUAL, "country": "FR",
    }
    defaults.update(kwargs)
    listing = enrich(ListingData(
        source="autoscout24", source_id=source_id,
        url=f"https://www.autoscout24.fr/offres/{source_id}", **defaults,
    ))
    ingest(session, [listing])
    session.flush()
    return session.execute(
        select(Listing).where(Listing.source_id == source_id)
    ).scalar_one()


def test_three_cars_of_the_very_same_spec_are_enough_to_price_one(session):
    """Un modele rare ne doit pas rester muet: trois jumelles suffisent."""
    for index in range(3):
        _car(session, f"jumelle{index}", price=12_000 + index * 200, km=118_000 + index * 3000)
    target = _car(session, "cible", price=9_500)

    valuation = estimate(session, target)
    assert valuation.details["tier"] == "strict"
    assert valuation.comps_count == 3
    assert valuation.fair_price_eur > 11_000
    assert valuation.delta_pct > 0.15


def test_three_cars_of_anything_are_not_a_market(session):
    """Le defaut mesure: trois Golf de trois energies valorisaient la quatrieme.

    Une e-Golf electrique, une GTE hybride et une essence de 2011 ne disent
    rien du prix d'une TDI de 2016, quel que soit le soin mis a la mediane.
    A ce niveau de similitude il faut une douzaine d'annonces, et tant
    qu'elles n'y sont pas la reponse est qu'on ne sait pas.
    """
    from carexpert.schemas import Fuel, Gearbox

    _car(session, "egolf", title="Volkswagen Golf e-golf electric 115",
         price=8_490, km=163_000, year=2015, fuel=Fuel.ELECTRIC, gearbox=Gearbox.AUTOMATIC)
    _car(session, "gte", title="Volkswagen Golf 1.4 TSI GTE 204H hybride rechargeable",
         price=15_990, km=129_000, year=2017, fuel=Fuel.PHEV, gearbox=Gearbox.AUTOMATIC)
    _car(session, "tfsi", title="Volkswagen Golf 1.8 TFSI 160 Highline",
         price=6_990, km=112_000, year=2014, fuel=Fuel.PETROL, gearbox=Gearbox.MANUAL)
    target = _car(session, "cible", title="Volkswagen Golf 7 1.6 TDI 110",
                  price=5_900, km=255_000, year=2016)

    valuation = estimate(session, target)
    assert valuation.method == "base_insuffisante"
    assert valuation.comps_count == 0          # ce qui ne compte pas ne compte pas
    assert valuation.confidence == 0.0
    assert valuation.delta_pct == 0.0
    assert valuation.details["comparables_trouves"] < valuation.details["comparables_requis"]

    # Et le verdict qui en decoule ne condamne ni ne recommande la voiture.
    from carexpert.pipeline.ingest import from_row
    from carexpert.scoring import score_deal

    score = score_deal(from_row(target), valuation, None)
    assert score.verdict == "unknown"
    assert "base insuffisante" in score.headline.lower()


def test_an_electric_car_is_never_priced_on_thermal_ones(session):
    """Mesure: une e-Golf de 163 000 km a 8 490 EUR sortait "8% au-dessus du
    marche, A FUIR" sur un echantillon de Golf 1.6 TDI.

    Un palier large accepte de melanger une essence et un diesel; il ne doit
    jamais melanger une electrique et une thermique. Batterie, autonomie,
    aides a l'achat, marche de l'occasion: rien n'est comparable, et aucun
    facteur de decote ne rattrape l'ecart de niveau de prix.
    """
    from carexpert.schemas import Fuel, Gearbox

    for index in range(14):
        _car(session, f"tdi{index}", price=11_000 + index * 200, km=150_000 + index * 3000,
             year=2015 + index % 3)
    target = _car(session, "egolf", title="Volkswagen Golf e-golf electric 115",
                  price=8_490, km=163_000, year=2015,
                  fuel=Fuel.ELECTRIC, gearbox=Gearbox.AUTOMATIC)

    valuation = estimate(session, target)
    # Quatorze Golf diesel en base, et pas une seule reference pour celle-ci.
    assert valuation.method == "aucune_reference"
    assert valuation.comps_count == 0
    assert valuation.delta_pct == 0.0

    # Quatre e-Golf en base, et la meme voiture se situe enfin.
    for index in range(4):
        _car(session, f"egolf{index}", title="Volkswagen Golf e-golf electric 115",
             price=8_000 + index * 300, km=150_000 + index * 4000, year=2015,
             fuel=Fuel.ELECTRIC, gearbox=Gearbox.AUTOMATIC)
    valuation = estimate(session, target)
    assert valuation.comps_count == 4
    assert valuation.details["tier"] == "strict"
    assert 7_500 < valuation.fair_price_eur < 10_000


def test_a_wide_tier_needs_a_wide_sample(session):
    """Le meme palier large, nourri, redevient exploitable."""
    from carexpert.schemas import Fuel, Gearbox

    fuels = [Fuel.PETROL, Fuel.DIESEL, Fuel.PHEV, Fuel.ELECTRIC]
    for index in range(14):
        _car(
            session, f"varie{index}",
            title=f"Volkswagen Golf variante {index}",
            price=11_000 + index * 150, km=100_000 + index * 9_000,
            year=2014 + index % 4, fuel=fuels[index % 4],
            gearbox=Gearbox.AUTOMATIC if index % 2 else Gearbox.MANUAL,
            country="DE" if index % 3 else "FR",
        )
    target = _car(session, "cible", price=9_000, km=150_000, year=2016)

    valuation = estimate(session, target)
    assert valuation.comps_count >= valuation.details["comparables_requis"]
    assert valuation.fair_price_eur > 0
    # Un palier large reste un palier large: la confiance ne monte pas au ciel.
    assert valuation.confidence < 0.75


def test_confidence_rises_with_the_sample(demo_market):
    """A tier egal, plus d'annonces vaut plus de confiance.

    Comparer a palier egal et non en absolu: depuis que chaque palier porte
    son propre seuil, quatre annonces strictement comparables passent la
    barre alors que dix "meme modele, toutes energies" ne la passent pas.
    Un petit echantillon n'est plus, en soi, un echantillon faible.
    """
    session = demo_market
    rows = session.execute(select(Listing)).scalars().all()
    valuations = [estimate(session, row) for row in rows[:60]]

    by_tier: dict[str, list] = {}
    for valuation in valuations:
        if valuation.comps_count:
            by_tier.setdefault(valuation.details["tier"], []).append(valuation)

    compared = 0
    for tier, group in by_tier.items():
        group.sort(key=lambda v: v.comps_count)
        half = len(group) // 2
        if half < 3:
            continue
        small = statistics.mean(v.confidence for v in group[:half])
        large = statistics.mean(v.confidence for v in group[-half:])
        assert large > small, f"palier {tier}: {large:.3f} <= {small:.3f}"
        compared += 1
    assert compared, "aucun palier n'avait assez d'annonces pour mesurer quoi que ce soit"


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
