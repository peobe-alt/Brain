"""La cote est-elle centree, ou se contente-t-elle de plaire ?

Un scan affiche ses vingt meilleures, et ses vingt meilleures ont l'air
convaincantes quel que soit le biais. Sur un premier vrai scan AutoScout24
de 399 Twingo, les vingt premieres etaient toutes "A SAISIR" avec une cote
20 a 50 % au-dessus du prix demande - et rien dans l'outil ne permettait de
dire si c'etait un bon lot ou une estimation qui gonfle.

La propriete mesurable: la voiture mediane **est** le marche. Sur une base
d'un meme modele, la moitie des annonces doit ressortir au-dessus de sa cote
et l'autre en dessous.
"""

from __future__ import annotations

from carexpert.valuation.calibration import Calibration, calibration


# --- le temoin ------------------------------------------------------------


def test_a_centred_market_reads_as_centred(demo_market):
    """Le marche de demonstration a un prix juste connu: il doit sortir centre.

    C'est le temoin de la mesure elle-meme. S'il penche, c'est l'outil de
    mesure qu'il faut regarder avant les donnees.
    """
    from carexpert.pipeline import scan
    from carexpert.schemas import SearchQuery

    session = demo_market
    scan(session, sources=[], query=SearchQuery(
        limit=260, countries=["FR", "DE", "IT", "BE", "ES", "NL"]))
    session.flush()

    report = calibration(session)

    assert report.valued > 150, "echantillon trop petit pour conclure"
    assert report.state == "juste", report.headline
    assert 0.40 <= report.below_market_share <= 0.60, report.below_market_share
    assert abs(report.median_delta) < 0.05


def test_the_measure_reports_what_the_base_is_missing(demo_market):
    """Ce qui manque explique souvent ce qui penche."""
    report = calibration(demo_market)

    assert report.total == 260
    assert report.with_year <= report.total
    assert report.with_km <= report.total


# --- ce que la mesure doit savoir dire ------------------------------------


def _base(deltas: list[float]) -> Calibration:
    return Calibration(total=len(deltas), valued=len(deltas), deltas=deltas,
                       confidences=[0.6] * len(deltas), comps=[12] * len(deltas))


def test_an_estimate_that_likes_every_car_is_named():
    """Une base ou tout le monde est sous le marche n'est pas pleine
    d'affaires: c'est la cote qui gonfle."""
    report = _base([0.20 + i * 0.001 for i in range(200)])

    assert report.state == "faussee"
    assert report.below_market_share == 1.0
    assert "faussee" in report.headline
    assert "La moitie devrait l'etre" in report.headline


def test_a_slight_lean_is_told_apart_from_a_broken_estimate():
    penchee = _base([0.07 + i * 0.0001 for i in range(200)])
    juste = _base([(-1) ** i * 0.03 for i in range(200)])

    assert penchee.state == "penchee"
    assert "penche" in penchee.headline
    assert juste.state == "juste"


def test_an_estimate_that_dislikes_every_car_is_named_too():
    """Le biais inverse compte autant: il fait disparaitre les vraies affaires."""
    report = _base([-0.20 - i * 0.001 for i in range(200)])

    assert report.state == "faussee"
    assert report.below_market_share == 0.0
    assert "au-dessus de" in report.headline


def test_percentiles_describe_the_spread_not_just_the_middle():
    report = _base([i / 100 for i in range(-20, 21)])

    assert report.percentile(0.5) == 0.0
    assert report.percentile(0.1) < report.percentile(0.9)


# --- le piege de la mesure ------------------------------------------------


def test_an_advert_with_no_comparable_is_not_a_perfect_fit(session):
    """Une annonce non jugeable ne compte pas comme un ecart nul.

    Sinon une base entierement "A ESTIMER" paraitrait parfaitement calibree,
    et le diagnostic serait d'autant plus rassurant qu'il n'y a rien dedans
    (invariant 7).
    """
    from carexpert.db import Listing, Valuation as StoredValuation
    from carexpert.normalize import enrich
    from carexpert.pipeline.ingest import ingest
    from carexpert.schemas import ListingData

    ingest(session, [enrich(ListingData(
        source="autoscout24", source_id="1", url="https://autoscout24.fr/o/1",
        title="Renault Twingo 1.2", price=3000, km=120_000, year=2012,
        make="Renault", model="Twingo"))])
    session.flush()
    row = session.execute(Listing.__table__.select()).first()
    session.add(StoredValuation(
        listing_id=row.id, fair_price_eur=3000.0, low_eur=3000.0, high_eur=3000.0,
        confidence=0.0, comps_count=0, method="aucune_reference",
        delta_eur=0.0, delta_pct=0.0, details={}))
    session.flush()

    report = calibration(session)

    assert report.total == 1
    assert report.valued == 0, "une cote sans comparable n'est pas une cote"
    assert report.deltas == []
    assert report.state == "sans_mesure"


def test_the_measure_can_be_narrowed_to_one_model(session):
    from carexpert.normalize import enrich
    from carexpert.pipeline.ingest import ingest
    from carexpert.schemas import ListingData

    def annonce(sid, marque, modele):
        return enrich(ListingData(
            source="autoscout24", source_id=str(sid),
            url=f"https://autoscout24.fr/o/{sid}", title=f"{marque} {modele}",
            price=5000, km=90_000, year=2015, make=marque, model=modele))

    ingest(session, [annonce(1, "Renault", "Twingo"), annonce(2, "Renault", "Clio"),
                     annonce(3, "Peugeot", "208")])
    session.flush()

    assert calibration(session).total == 3
    assert calibration(session, make="Renault").total == 2
    assert calibration(session, make="Renault", model="Twingo").total == 1
    assert calibration(session, source="leboncoin").total == 0
