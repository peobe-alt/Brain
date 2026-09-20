"""Parsing and normalising real-world advert wording."""

from __future__ import annotations

import pytest

from carexpert.normalize import (
    detect_body,
    detect_fuel,
    detect_gearbox,
    detect_options,
    detect_signals,
    enrich,
    parse_km,
    parse_power_hp,
    parse_price,
    parse_registration,
)
from carexpert.schemas import BodyType, Fuel, Gearbox, ListingData


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("12.500 €", 12500), ("12 500 EUR", 12500), ("£12,500", 12500),
        ("€ 9.990,00", 9990), ("Prix: 15990", 15990), (18500, 18500),
    ],
)
def test_parse_price_handles_european_formats(raw, expected):
    assert parse_price(raw) == expected


def test_parse_km_converts_miles():
    assert parse_km("125.000 km") == 125_000
    assert parse_km("68,500 miles") == pytest.approx(110_240, rel=0.01)


def test_parse_power_converts_kw():
    assert parse_power_hp("110 ch") == 110
    assert parse_power_hp("81 kW") == 110
    assert parse_power_hp("150 PS") == 150


def test_parse_registration_accepts_several_notations():
    assert parse_registration("03/2018").year == 2018
    assert parse_registration("mars 2018").month == 3
    assert parse_registration("2018-03-15").month == 3


def test_fuel_detection_is_multilingual():
    assert detect_fuel("1.6 HDi") is Fuel.DIESEL
    assert detect_fuel("2.0 TDI Benzin") is Fuel.DIESEL
    assert detect_fuel("Elektro 40 kWh") is Fuel.ELECTRIC
    assert detect_fuel("Hybride rechargeable") is Fuel.PHEV


def test_a_hybrid_is_never_read_as_an_electric():
    """Le libelle leboncoin d'une hybride simple contient "electrique".

    "Hybride essence/electrique" lu comme electrique valorisait une Yaris
    hybride sur la courbe de decote d'une batterie qu'elle n'a pas.
    """
    assert detect_fuel("Hybride essence/electrique") is Fuel.HYBRID
    assert detect_fuel("Hybride rechargeable essence/electrique") is Fuel.PHEV
    # Et l'inverse tient toujours: sans mention d'hybride, c'est une electrique.
    assert detect_fuel("Electrique") is Fuel.ELECTRIC
    assert detect_fuel("Tesla Model 3 Long Range") is Fuel.ELECTRIC


def test_fuel_detection_ignores_equipment_mentions():
    """`hayon electrique` is a power tailgate, not an electric car."""
    assert detect_fuel("Volkswagen Golf 1.6 TDI", None, "Options: hayon electrique") is Fuel.DIESEL


def test_fuel_detection_reads_german_engine_badges():
    assert detect_fuel("BMW Serie 3 320d Touring") is Fuel.DIESEL
    assert detect_fuel("BMW 320i Berline") is Fuel.PETROL


def test_gearbox_and_body_detection():
    assert detect_gearbox("EDC 6 rapports") is Gearbox.AUTOMATIC
    assert detect_gearbox("Boite manuelle 6 vitesses") is Gearbox.MANUAL
    assert detect_body("Peugeot 308 SW") is BodyType.ESTATE
    assert detect_body("Audi A4 Avant") is BodyType.ESTATE


def test_body_detection_does_not_match_inside_words():
    """`volkSWagen` must not be read as an SW estate."""
    assert detect_body("Volkswagen Golf Confortline") is not BodyType.ESTATE


def test_options_detection():
    options = detect_options("Toit panoramique, camera de recul, sieges chauffants")
    assert {"toit_ouvrant", "camera_recul", "sieges_chauffants"} <= set(options)


def test_negation_prevents_false_red_flags():
    """`jamais accidente` is the opposite of an accident."""
    codes = [s.code for s in detect_signals("Vehicule suivi, jamais accidente, non fumeur")]
    assert "accidente" not in codes
    assert "non_fumeur" in codes


def test_real_red_flags_are_still_caught():
    codes = [s.code for s in detect_signals("Vendu en l'etat, moteur hs, compteur non garanti")]
    assert {"vendu_en_letat", "moteur_hs", "km_non_garanti"} <= set(codes)


def test_import_keyword_does_not_fire_on_importante():
    assert [s.code for s in detect_signals("Voiture importante pour la famille")] == []


def test_enrich_fills_everything_from_the_title():
    listing = ListingData(
        source="t", source_id="1", url="u",
        title="Peugeot 308 SW 1.6 BlueHDi 130 EAT8 Allure 2019", price=15990,
    )
    enrich(listing)
    assert listing.make == "Peugeot"
    assert listing.model == "308"
    assert listing.fuel is Fuel.DIESEL
    assert listing.gearbox is Gearbox.AUTOMATIC
    assert listing.body is BodyType.ESTATE
    assert listing.power_hp == 130
    assert listing.year == 2019
    assert listing.price_eur == 15990


def test_cross_language_model_names_collapse():
    """The same car must land on the same label in FR, DE and EN adverts."""
    labels = []
    for title in ("BMW Serie 3 320d Touring", "BMW 3er 320d Touring", "BMW 3 Series 320d"):
        listing = ListingData(source="t", source_id="1", url="u", title=title, price=1)
        enrich(listing)
        labels.append((listing.make, listing.model))
    assert len(set(labels)) == 1


def test_a_commercial_badge_does_not_decide_the_fuel_alone():
    """Renault vend la Megane "E-Tech electrique" et la Clio "E-Tech hybride".

    Regression introduite en corrigeant l'invariant 28: en remontant HYBRID
    au-dessus d'ELECTRIC, le badge "e-tech" a fait basculer toutes les
    electriques Renault de grande serie en hybrides, donc valorisees sur la
    mauvaise courbe. Un badge ne tranche que lorsque aucun mot explicite ne
    l'a fait.
    """
    assert detect_fuel("Renault Megane E-Tech Electrique 220ch") is Fuel.ELECTRIC
    assert detect_fuel("Renault Scenic E-Tech electrique 170") is Fuel.ELECTRIC
    assert detect_fuel("Renault Clio E-Tech 145 hybride") is Fuel.HYBRID
    assert detect_fuel("Renault Captur E-Tech 160 hybride rechargeable") is Fuel.PHEV
    # Et sans mot explicite, le badge garde son sens usuel.
    assert detect_fuel("Renault Clio V E-Tech 140") is Fuel.HYBRID
    assert detect_fuel("Nissan Qashqai e-Power") is Fuel.HYBRID
