"""The scorer's whole job: telling a bargain from a trap."""

from __future__ import annotations

from carexpert.expert import analyze_offline
from carexpert.normalize import enrich
from carexpert.schemas import ListingData, Photo
from carexpert.scoring import score_deal
from carexpert.valuation.estimator import Valuation


def _listing(description: str, price: float) -> ListingData:
    listing = ListingData(
        source="t", source_id="1", url="u",
        title="Volkswagen Golf 1.6 TDI 115 Confortline 2019",
        description=description, price=price, km=92_000, year=2019,
        photos=[Photo(url=f"p{i}") for i in range(8)],
    )
    return enrich(listing)


def _valuation(price: float, fair: float = 17_000.0) -> Valuation:
    delta = fair - price
    return Valuation(
        fair_price_eur=fair, low_eur=fair * 0.95, high_eur=fair * 1.05, confidence=0.85,
        comps_count=16, method="comparables:strict", delta_eur=delta, delta_pct=delta / fair,
    )


def _score(description: str, price: float):
    listing = _listing(description, price)
    valuation = _valuation(price)
    report = analyze_offline(listing, valuation.as_dict()).report
    return score_deal(listing, valuation, report)


def test_a_clean_underpriced_car_scores_high():
    score = _score(
        "Carnet d'entretien complet, premiere main, CT vierge, distribution faite.", 13_500
    )
    assert score.score >= 75
    assert score.verdict == "grab"
    assert score.net_gain_eur > 3000


def test_a_cheaper_car_with_red_flags_scores_low():
    """Cheaper than the bargain, and it must still rank far below it."""
    score = _score(
        "Vendu en l'etat, compteur non garanti, vehicule importe, voyant moteur allume.", 10_500
    )
    assert score.score <= 35
    assert score.verdict == "avoid"


def test_the_trap_never_outranks_the_bargain():
    bargain = _score("Carnet d'entretien complet, premiere main, CT vierge.", 13_500)
    trap = _score("Vendu en l'etat, moteur hs, compteur non garanti.", 10_500)
    assert bargain.score > trap.score + 30


def test_market_priced_car_lands_in_the_middle():
    score = _score("Bon etat general, entretien a jour.", 16_800)
    assert 45 <= score.score <= 70


def test_overpriced_car_is_penalised():
    score = _score("Tres bon etat, factures.", 19_900)
    assert score.score < 45


def test_every_point_is_explained():
    score = _score("Carnet d'entretien complet.", 14_000)
    assert score.factors
    assert all(factor.detail for factor in score.factors)
    assert any("comparables" in factor.detail for factor in score.factors)


def test_without_comparables_the_score_stays_neutral():
    listing = _listing("Bon etat.", 14_000)
    empty = Valuation(0, 0, 0, 0.0, 0, "aucune_reference", 0, 0)
    score = score_deal(listing, empty, analyze_offline(listing).report)
    assert 30 <= score.score <= 70
    assert "reference" in score.headline.lower()


def test_no_comparables_means_unknown_not_avoid():
    """Telling someone to flee a sound car because the base is empty is the
    worst possible answer, and it is what the first analysis would produce."""
    listing = _listing("Carnet d'entretien complet, premiere main, CT vierge.", 15_990)
    empty = Valuation(0, 0, 0, 0.0, 0, "aucune_reference", 0, 0)
    score = score_deal(listing, empty, analyze_offline(listing).report)
    assert score.verdict == "unknown"


def test_a_real_red_flag_still_wins_over_unknown():
    listing = _listing("Vendu en l'etat, moteur hs, compteur non garanti.", 15_990)
    empty = Valuation(0, 0, 0, 0.0, 0, "aucune_reference", 0, 0)
    score = score_deal(listing, empty, analyze_offline(listing).report)
    assert score.verdict == "avoid"


def test_a_verdict_needs_more_than_two_references():
    """Deux comparables ne font pas un marche.

    Un modele rare - quatorze Volvo V70 en vente dans toute la France -
    donne une estimation a tres faible confiance. Annoncer "A FUIR" sur
    cette base serait affirmatif et faux; la reponse honnete est "a estimer".
    """
    from carexpert.schemas import Fuel, Gearbox, ListingData
    from carexpert.scoring import score_deal
    from carexpert.valuation.estimator import Valuation

    listing = ListingData(
        source="test", source_id="1", url="https://site.fr/1",
        title="Volvo V70 D5 Summum", price=8900, price_eur=8900,
        make="Volvo", model="V70", year=2011, km=187000,
        fuel=Fuel.DIESEL, gearbox=Gearbox.AUTOMATIC,
    )
    fragile = Valuation(
        fair_price_eur=6900, low_eur=6000, high_eur=7800, confidence=0.18,
        comps_count=2, method="comparables:modele", delta_eur=-2000, delta_pct=-22.5,
    )
    solid = Valuation(
        fair_price_eur=6900, low_eur=6400, high_eur=7400, confidence=0.60,
        comps_count=18, method="comparables:modele", delta_eur=-2000, delta_pct=-22.5,
    )

    assert score_deal(listing, fragile, None).verdict == "unknown"
    assert "2 references" in score_deal(listing, fragile, None).headline
    # La meme annonce, avec un vrai marche derriere: le verdict revient.
    assert score_deal(listing, solid, None).verdict == "avoid"
