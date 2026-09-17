"""Model-specific knowledge and the offline expert."""

from __future__ import annotations

from carexpert.expert import analyze_offline, match_defects
from carexpert.normalize import enrich
from carexpert.schemas import ListingData


def _listing(title: str, description: str = "", **kwargs) -> ListingData:
    listing = ListingData(
        source="t", source_id="1", url="u", title=title, description=description,
        price=kwargs.pop("price", 12_000), **kwargs,
    )
    return enrich(listing)


def test_known_engine_weaknesses_are_matched():
    codes = [d.code for d in match_defects(_listing("Peugeot 308 1.2 PureTech 130", year=2018))]
    assert "psa_puretech_courroie_humide" in codes


def test_weakness_outside_its_year_range_is_not_matched():
    codes = [d.code for d in match_defects(_listing("BMW 320d Touring", year=2020))]
    assert "bmw_n47_chaine" not in codes


def test_battery_check_only_applies_to_electrified_cars():
    diesel = match_defects(_listing("VW Golf 1.6 TDI", "Options: hayon electrique", year=2019))
    assert "ev_sante_batterie" not in [d.code for d in diesel]
    electric = match_defects(_listing("Tesla Model 3 Long Range", year=2019))
    assert "ev_sante_batterie" in [d.code for d in electric]


def test_offline_report_refuses_a_car_sold_as_seen():
    listing = _listing(
        "Peugeot 308 1.2 PureTech 130 Allure",
        "Vendu en l'etat, distribution a faire, voyant moteur allume.",
        year=2018, km=140_000,
    )
    report = analyze_offline(listing, {"delta_pct": 0.28, "comps_count": 14,
                                       "fair_price_eur": 9000, "low_eur": 8200,
                                       "high_eur": 9800, "confidence": 0.8,
                                       "method": "comparables:strict"}).report
    assert report.verdict == "avoid"
    assert report.estimated_repairs_eur > 0
    assert any(flag.severity in ("serieux", "redhibitoire") for flag in report.red_flags)
    assert report.questions_to_seller


def test_unexplained_discount_is_itself_a_red_flag():
    listing = _listing("Volkswagen Golf 1.6 TDI", "Bonne voiture.", year=2019, km=90_000)
    report = analyze_offline(listing, {"delta_pct": 0.35, "comps_count": 20,
                                       "fair_price_eur": 18000, "low_eur": 17000,
                                       "high_eur": 19000, "confidence": 0.9,
                                       "method": "comparables:strict"}).report
    assert any("decote" in flag.label.lower() for flag in report.red_flags)


def test_clean_advert_produces_no_false_alarm():
    listing = _listing("Toyota Yaris Hybride Dynamic",
                       "Jamais accidente, carnet complet, non fumeur.", year=2021, km=40_000)
    report = analyze_offline(listing).report
    assert not [f for f in report.red_flags if f.severity in ("serieux", "redhibitoire")]
    assert report.strengths
