"""La precision de l'estimation, mesuree avec un metre independant.

`test_valuation.py` mesure l'estimateur sur le marche de demonstration. Or
`sources/demo.py` fabrique ses prix avec **exactement** les constantes de
`valuation/adjust.py`. Ce test-la regarde donc l'estimateur inverser son
propre generateur: il ne peut pas echouer, donc il ne mesure rien.

Ici le marche obeit a une autre loi (`tests/market.py`): perte concave en
kilometrage, decote annuelle en racine, un plancher de reprise, et une
rupture de generation que notre modele, continu par construction, n'a aucun
moyen de representer. Ce que l'estimateur en retrouve, il le retrouve pour
de bon.

Mesure du 20 septembre 2026, 320 annonces, divergence de forme x1,63 entre
les deux lois : **4,3 % d'erreur mediane, p90 a 11,7 %, biais -0,6 %**.
"""

from __future__ import annotations

import statistics

from carexpert.db import Listing, select
from carexpert.pipeline.ingest import ingest
from carexpert.valuation import estimate

from market import advert, foreign_market, foreign_price


def _errors(session, rows=None) -> tuple[list[float], list[float], list[float]]:
    """Ecarts absolus, biais signes et confiances, face au prix vrai."""
    rows = rows if rows is not None else session.execute(
        select(Listing)).scalars().all()
    absolute, signed, confidences = [], [], []
    for row in rows:
        truth = (row.raw or {}).get("juste")
        valuation = estimate(session, row)
        if not truth or not valuation.comps_count:
            continue
        absolute.append(abs(valuation.fair_price_eur - truth) / truth)
        signed.append((valuation.fair_price_eur - truth) / truth)
        confidences.append(valuation.confidence)
    return absolute, signed, confidences


# --- le metre lui-meme ----------------------------------------------------


def test_the_ruler_is_not_made_of_our_own_wood():
    """Le garde-fou de toute cette mesure.

    Si quelqu'un "corrige" un jour la loi de `market.py` pour la rapprocher
    de nos courbes, la mesure redevient circulaire **sans que rien
    n'echoue**: les chiffres s'ameliorent meme, ce qui est exactement le
    piege. On verifie donc que le rapport entre les deux lois varie: deux
    lois de meme forme donneraient un rapport constant.
    """
    from carexpert.schemas import Fuel, Gearbox, SellerType
    from carexpert.valuation.adjust import vehicle_factor

    ratios = []
    for age in (1, 2, 3, 5, 6, 7, 9, 12, 15):
        for km in (15_000, 50_000, 100_000, 160_000, 240_000):
            ours = 21_000 * vehicle_factor(
                age_years=age, km=km, make="Renault", fuel=Fuel.PETROL,
                gearbox=Gearbox.MANUAL, options=[],
                seller_type=SellerType.PRIVATE, country="FR")
            ratios.append(foreign_price(age, km, 21_000) / ours)

    divergence = max(ratios) / min(ratios)
    assert divergence > 1.30, (
        f"les deux lois ne divergent que de x{divergence:.2f}: le metre est "
        "fait du meme bois que ce qu'il mesure"
    )


# --- ce que l'estimateur retrouve vraiment --------------------------------


def test_the_estimate_holds_on_a_market_that_ignores_our_curves(session):
    """La precision vient de la selection des comparables, pas des courbes.

    Sur une bande etroite en annee et en kilometrage, toute loi de prix
    lisse est quasi lineaire: les courbes n'ont plus qu'une correction
    marginale a porter. C'est pour ca que l'estimateur tient face a une loi
    qu'il ne connait pas.
    """
    ingest(session, foreign_market(320))
    session.flush()

    absolute, signed, _ = _errors(session)

    assert len(absolute) > 250, "echantillon trop petit pour conclure"
    assert statistics.median(absolute) < 0.055, statistics.median(absolute)
    assert abs(statistics.median(signed)) < 0.03, (
        f"biais systematique de {statistics.median(signed) * 100:+.1f} %"
    )

    # La mediane ne sent presque pas les courbes de decote: sur une bande
    # etroite elles n'ont rien a corriger. Mesure en faisant varier
    # `AGE_RATE_MAINSTREAM` d'un facteur six, de 0,06 a 0,35: la mediane va
    # de 5,1 a 7,0 %, le p90 de 16,4 a 22,9 % contre 11,7 % a la bonne
    # valeur. C'est donc le p90 qui tient les courbes, et lui seul.
    p90 = sorted(absolute)[int(len(absolute) * 0.9)]
    assert p90 < 0.13, f"p90 a {p90 * 100:.1f} %: les courbes ont derive"


def test_the_estimate_does_not_quietly_favour_one_end_of_the_market(session):
    """Un biais qui change de signe avec l'age se voit mal en mediane.

    Il suffirait pourtant a remplir le haut du classement de vieilles
    voitures, ou de recentes, quel que soit leur prix reel.
    """
    ingest(session, foreign_market(320))
    session.flush()

    from datetime import date

    this_year = date.today().year
    young, old = [], []
    for row in session.execute(select(Listing)).scalars().all():
        truth = (row.raw or {}).get("juste")
        valuation = estimate(session, row)
        if not truth or not valuation.comps_count or not row.year:
            continue
        bias = (valuation.fair_price_eur - truth) / truth
        (young if this_year - row.year <= 6 else old).append(bias)

    assert young and old
    ecart = abs(statistics.median(young) - statistics.median(old))
    assert ecart < 0.08, (
        f"recentes {statistics.median(young) * 100:+.1f} %, "
        f"anciennes {statistics.median(old) * 100:+.1f} %"
    )


# --- les invariants, verifies hors de leur propre marche ------------------


def test_cross_posting_does_not_lift_the_estimate(session):
    """Invariant 4, mesure sur un marche que nous n'avons pas fabrique.

    Les marchands crosspostent plus que les particuliers et affichent plus
    cher. Compter chaque copie ferait deriver toutes les estimations vers le
    haut, et toutes les annonces paraitraient meilleures qu'elles ne sont.
    """
    from carexpert.schemas import SellerType

    base = foreign_market(200)
    ingest(session, base)
    session.flush()
    propre, _, _ = _errors(session)

    copies = []
    for index, original in enumerate(base[:120]):
        for rang, premium in enumerate((1.12, 1.15)):
            copies.append(advert(
                f"copie-{index}-{rang}", original.make, original.model,
                original.year, original.km, original.price * premium,
                original.extra["juste"], source="crosspost",
                seller_type=SellerType.PRO,
            ))
    ingest(session, copies)
    session.flush()

    rows = [row for row in session.execute(select(Listing)).scalars().all()
            if row.source != "crosspost"]
    apres, _, _ = _errors(session, rows)

    derive = statistics.median(apres) - statistics.median(propre)
    assert derive < 0.02, (
        f"240 copies pros ont fait bouger l'erreur mediane de "
        f"{derive * 100:+.1f} points"
    )


def test_a_car_outside_the_sample_loses_its_verdict(session):
    """Invariant 13: un verdict exige de quoi le tenir.

    Une base de voitures recentes ne sait rien d'une vieille a 220 000 km.
    L'estimation sortira, forcement; la confiance doit dire qu'elle ne vaut
    rien.
    """
    from datetime import date

    this_year = date.today().year
    ingest(session, foreign_market(160, make="Renault", model="Clio",
                                   years=(this_year - 5, this_year - 1)))
    session.flush()

    dedans = advert("cible-dedans", "Renault", "Clio", this_year - 3, 45_000,
                    foreign_price(3, 45_000, 21_000),
                    foreign_price(3, 45_000, 21_000))
    dehors = advert("cible-dehors", "Renault", "Clio", this_year - 13, 220_000,
                    foreign_price(13, 220_000, 21_000),
                    foreign_price(13, 220_000, 21_000))

    proche = estimate(session, dedans)
    lointaine = estimate(session, dehors)

    assert proche.confidence >= 0.35, proche.confidence
    assert lointaine.confidence < proche.confidence
    assert lointaine.confidence < 0.35, (
        f"une Clio de 13 ans a 220 000 km estimee avec "
        f"{lointaine.confidence:.2f} de confiance sur un parc de recentes"
    )
