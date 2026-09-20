"""Does the price estimate actually track the market?"""

from __future__ import annotations

import statistics

from carexpert.config import get_settings
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


def test_two_comparables_never_carry_a_verdict(session):
    """Deux annonces qui s'accordent ne font pas un marche.

    Mesure sur une page de six Twingo consultee dans le navigateur: un
    "A SAISIR, 12,4 % sous le marche" rendu sur deux comparables, a 0,02 du
    seuil. L'accord et l'extrapolation pesaient 55 % de la confiance, assez
    pour qu'un echantillon minuscule passe. Et a un seul comparable, la
    confiance montait encore a 0,44.
    """
    from carexpert.schemas import Fuel, Gearbox, SellerType
    from carexpert.valuation.comps import Facts
    from carexpert.valuation.estimator import _confidence

    def twingo() -> Facts:
        return Facts(
            make="Renault", model="Twingo", year=2015, age_years=9.0, km=100000,
            fuel=Fuel.PETROL, gearbox=Gearbox.MANUAL, body="citadine", options=[],
            price_eur=5000.0, country="FR", seller_type=SellerType.PRIVATE,
        )

    def best_case(count: int) -> float:
        """Le cas le plus favorable: accord parfait, aucune extrapolation."""
        return _confidence(count, 0.0, 5000.0, "modele", twingo(),
                           [twingo() for _ in range(count)])

    settings = get_settings()
    for thin in (1, 2, 3, 5):
        assert best_case(thin) < settings.min_confidence_for_verdict, thin
    # Et le seuil reste franchissable des que l'echantillon existe vraiment.
    assert best_case(8) >= settings.min_confidence_for_verdict
    assert best_case(12) > best_case(8) > best_case(5)


def test_a_supplied_market_still_reaches_the_documented_confidence(demo_market):
    """Le plafond ne doit mordre que sur les echantillons maigres.

    Invariant 13: sur un marche fourni la confiance mesuree va de 0,48 a
    0,70. Si la correction rabotait aussi ce cas, elle rendrait l'outil muet
    partout.
    """
    from carexpert.db import Listing, select
    from carexpert.valuation import estimate

    rows = demo_market.execute(select(Listing).limit(120)).scalars().all()
    values = [estimate(demo_market, row) for row in rows]
    rich = [v for v in values if v.comps_count >= 12]

    assert rich, "le marche de demonstration doit fournir des vehicules bien dotes"
    assert max(v.confidence for v in rich) >= 0.48


# --- ce qu'une annonce ne dit pas -----------------------------------------
#
# Mesure sur un vrai scan AutoScout24 de 399 Twingo: les vingt premieres du
# classement etaient toutes "A SAISIR", et la cote toujours 20 a 50 % au-dessus
# du prix demande. Cause: une page qui ne donne ni annee ni kilometrage
# produisait une voiture traitee comme neuve et a zero kilometre, donc tous ses
# comparables remis "a l'etat neuf" pour la rejoindre. Une Twingo a 1 800 EUR
# estimee 31 465 EUR, premiere du classement, avec 0,57 de confiance.
#
# Les 289 tests passaient. Aucun n'empruntait ce chemin.


def _marche_twingo():
    """Un marche plausible, de la Twingo II de 2009 a la Twingo III de 2023."""
    from carexpert.normalize import enrich
    from carexpert.schemas import ListingData

    lots = [(2200, 2009, 180_000), (2500, 2010, 165_000), (2900, 2011, 150_000),
            (3400, 2012, 140_000), (4200, 2014, 120_000), (5500, 2016, 95_000),
            (7200, 2018, 70_000), (8900, 2020, 55_000), (10500, 2021, 40_000),
            (12400, 2023, 20_000)]
    return [
        enrich(ListingData(
            source="autoscout24", source_id=str(i), url=f"https://autoscout24.fr/o/{i}",
            title=f"Renault Twingo {annee} essence", price=prix, km=km, year=annee,
            make="Renault", model="Twingo"))
        for i, (prix, annee, km) in enumerate(lots, start=1)
    ]


def _twingo_cible(session, **champs):
    from carexpert.normalize import enrich
    from carexpert.pipeline.ingest import ingest
    from carexpert.schemas import ListingData

    ingest(session, _marche_twingo())
    session.flush()
    return estimate(session, enrich(ListingData(
        source="autoscout24", source_id="999", url="https://autoscout24.fr/o/999",
        title="Renault Twingo 1.2", price=1800, make="Renault", model="Twingo",
        **champs)))


def test_a_car_with_neither_year_nor_mileage_is_not_valued(session):
    """Sans age ni kilometrage, il n'y a rien pour situer une occasion.

    Et le defaut ne s'arrete pas a l'estimation: sans annee et sans
    kilometrage, la selection n'applique ni tolerance d'annee ni tolerance de
    kilometrage. Elle ramene donc tout le modele, de 2009 a 2023, et appelle
    ca le palier strict.
    """
    valuation = _twingo_cible(session, km=None, year=None)

    assert valuation.comps_count == 0
    assert valuation.confidence == 0.0
    assert valuation.method == "aucune_reference"
    assert "ni annee ni kilometrage" in valuation.details["raison"]


def test_an_unknown_mileage_is_not_a_mileage_of_zero(session):
    """Ce qu'on ignore ne doit pas jouer en faveur de la voiture.

    La meme Twingo de 2010, avec et sans son kilometrage, doit valoir a peu
    pres la meme chose. Avant correction, l'absence de kilometrage valait a
    elle seule un facteur trois.
    """
    connue = _twingo_cible(session, km=170_000, year=2010)
    muette = _twingo_cible(session, km=None, year=2010)

    assert connue.comps_count and muette.comps_count
    ecart = abs(muette.fair_price_eur - connue.fair_price_eur) / connue.fair_price_eur
    assert ecart < 0.15, (
        f"cote {connue.fair_price_eur:.0f} EUR avec le kilometrage, "
        f"{muette.fair_price_eur:.0f} EUR sans"
    )


def test_a_thin_page_does_not_produce_the_best_deal_of_the_scan(session):
    """Le symptome tel qu'il s'est presente: la moins renseignee en tete.

    Une annonce dont la page ne donne presque rien ne doit pas ressortir
    devant une annonce complete au meme prix. C'est l'inverse qui est vrai:
    on en sait moins, donc on en dit moins.
    """
    from carexpert.normalize import enrich
    from carexpert.pipeline.ingest import ingest
    from carexpert.schemas import ListingData
    from carexpert.scoring import score_deal

    ingest(session, _marche_twingo())
    session.flush()

    def note(**champs):
        annonce = enrich(ListingData(
            source="autoscout24", source_id=str(champs.pop("sid")),
            url="https://autoscout24.fr/o/x", title="Renault Twingo 1.2",
            price=1800, make="Renault", model="Twingo", **champs))
        return score_deal(annonce, estimate(session, annonce), None)

    muette = note(sid=901, km=None, year=None)
    complete = note(sid=902, km=170_000, year=2010)

    assert muette.verdict == "unknown", "une page muette ne se prononce pas"
    assert muette.score <= complete.score, (
        f"la page muette note {muette.score}, la page complete {complete.score}"
    )
