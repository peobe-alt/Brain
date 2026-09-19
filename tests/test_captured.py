"""Lire une page que l'utilisateur a ouverte lui-meme.

leboncoin et La Centrale refusent la collecte automatisee, mesure deux fois:
HTTP 403 et interstitiel DataDome sur une requete simple, captcha sur un vrai
Chromium sans tete. C'est un non, et le projet ne discute pas un non.

Mais une personne qui cherche une Twingo **regarde deja la page**: le site la
lui a servie, volontairement, comme a un visiteur. La lire n'est pas de la
collecte, aucune protection n'est contournee, et le site n'a rien eu a
decider puisque c'est un humain qui a navigue.

Ce module n'a aucun code reseau et ne peut acquerir aucune page tout seul.
C'est exactement ce qui le laisse ouvert quand les voies automatisees sont
fermees.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from carexpert.schemas import Fuel, SellerType
from carexpert.sources.captured import (
    capture_files,
    page_origin,
    read_capture,
    read_captures,
    source_of,
)

FIXTURES = Path(__file__).parent / "fixtures"
LBC_SEARCH = "https://www.leboncoin.fr/recherche?category=2&text=twingo"


def saved_page(html: str, url: str) -> str:
    """Une page telle que "Enregistrer sous" la depose sur le disque.

    Le fichier perd son adresse; la page, elle, garde son lien canonique.
    """
    return html.replace(
        "<head>", f'<head><link rel="canonical" href="{url}"/>', 1
    )


@pytest.fixture(scope="module")
def leboncoin_capture() -> str:
    return saved_page(
        (FIXTURES / "leboncoin_search.html").read_text(encoding="utf-8"), LBC_SEARCH
    )


# --- retrouver d'ou vient un fichier --------------------------------------


def test_a_saved_page_still_remembers_its_address(leboncoin_capture):
    """Un fichier sur disque n'a pas d'URL, la page en porte une.

    Sans elle, les liens relatifs des annonces ne resolvent nulle part et
    chaque annonce perd son adresse.
    """
    assert page_origin(leboncoin_capture) == LBC_SEARCH
    assert source_of(leboncoin_capture) == "leboncoin"


def test_the_source_is_asked_of_the_page_not_of_the_user():
    """Le site a ecrit son adresse dans la page: une chose de moins a saisir."""
    page = '<html><head><meta property="og:url" content="https://www.lacentrale.fr/listing"/>' \
           "</head><body></body></html>"
    assert source_of(page) == "lacentrale"


def test_a_page_without_an_address_falls_back_on_what_was_asked():
    page = "<html><head></head><body><p>rien</p></body></html>"
    assert page_origin(page) is None
    assert source_of(page) is None
    assert source_of(page, fallback="leboncoin") == "leboncoin"


# --- lire les annonces ----------------------------------------------------


def test_a_captured_results_page_yields_its_adverts(leboncoin_capture):
    rows = read_capture(leboncoin_capture)

    assert len(rows) == 6
    assert {row.source for row in rows} == {"leboncoin"}
    advert = next(row for row in rows if row.source_id == "2456789012")
    assert advert.price == 12500
    assert advert.km == 84000
    assert advert.year == 2019
    assert advert.fuel is Fuel.DIESEL
    assert advert.seller_type is SellerType.PRIVATE
    assert advert.url == "https://www.leboncoin.fr/ad/voitures/2456789012"


def test_a_captured_advert_page_yields_one_advert():
    """Une page d'annonce, elle, porte le descriptif (invariant 14)."""
    url = "https://www.leboncoin.fr/ad/voitures/2456789012"
    state = {"props": {"ad": {
        "list_id": 2456789012,
        "url": "/ad/voitures/2456789012",
        "subject": "Peugeot 308 1.5 BlueHDi 130 Allure",
        "body": "Distribution faite a 120 000 km, factures a l'appui.",
        "price": [12500],
        "attributes": [{"key": "mileage", "value": "84000"},
                       {"key": "regdate", "value": "2019"}],
    }}}
    page = (f'<html><head><link rel="canonical" href="{url}"/>'
            f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(state)}'
            "</script></head><body></body></html>")

    rows = read_capture(page)

    assert len(rows) == 1
    assert rows[0].source_id == "2456789012"
    assert "Distribution faite" in rows[0].description


def test_an_empty_or_broken_capture_yields_nothing():
    assert read_capture("") == []
    assert read_capture("   ") == []
    assert read_capture("<html><body><p>Page blanche</p></body></html>") == []


# --- lire un lot ----------------------------------------------------------


def test_a_folder_imports_every_page_in_it(tmp_path, leboncoin_capture):
    (tmp_path / "a.html").write_text(leboncoin_capture, encoding="utf-8")
    (tmp_path / "b.html").write_text(leboncoin_capture, encoding="utf-8")
    (tmp_path / "notes.txt").write_text("pas une page", encoding="utf-8")

    files = list(capture_files(tmp_path))
    assert [f.name for f in files] == ["a.html", "b.html"]

    listings, problems = read_captures([tmp_path])
    # Les deux pages portent les memes annonces: une annonce, une fois.
    assert len(listings) == 6
    assert problems == []


def test_one_unreadable_page_does_not_stop_the_others(tmp_path, leboncoin_capture):
    """Importer vingt pages ne doit pas s'arreter sur celle a moitie ecrite."""
    (tmp_path / "bonne.html").write_text(leboncoin_capture, encoding="utf-8")
    (tmp_path / "tronquee.html").write_text(
        '<html><head><script id="__NEXT_DATA__">{"props": {', encoding="utf-8"
    )

    listings, problems = read_captures([tmp_path])

    assert len(listings) == 6
    assert len(problems) == 1
    assert "tronquee.html" in problems[0]
    # Le message dit quoi verifier: la taille et la source reconnue.
    assert "Ko" in problems[0]


def test_nothing_here_reaches_the_network(leboncoin_capture, monkeypatch):
    """La garantie du module: il ne peut pas acquerir une page tout seul.

    Si une requete partait d'ici, la promesse faite au site - "c'est un
    humain qui a ouvert cette page" - serait fausse.
    """
    import httpx

    def forbidden(*args, **kwargs):  # pragma: no cover - ne doit jamais courir
        raise AssertionError("une capture ne fait pas de requete")

    monkeypatch.setattr(httpx.Client, "get", forbidden)
    monkeypatch.setattr(httpx.Client, "send", forbidden)

    assert len(read_capture(leboncoin_capture)) == 6


# --- la chaine complete ---------------------------------------------------


def test_captured_pages_go_all_the_way_to_a_score(session, tmp_path, leboncoin_capture):
    """Importer, estimer, noter: la meme chaine qu'un scan, sans collecte."""
    from carexpert.pipeline import import_captures

    (tmp_path / "recherche.html").write_text(leboncoin_capture, encoding="utf-8")

    report = import_captures(session, [tmp_path])

    assert report.collected["leboncoin"].seen == 6
    assert report.valued == 6
    assert len(report.top) == 6
    assert all(deal["price_eur"] for deal in report.top)
    # Six modeles differents, donc aucun comparable: le verdict ne s'invente
    # pas (invariants 7 et 13).
    assert {deal["verdict"] for deal in report.top} == {"unknown"}


def test_importing_the_same_page_twice_does_not_duplicate(session, tmp_path,
                                                          leboncoin_capture):
    from carexpert.pipeline import import_captures

    (tmp_path / "r.html").write_text(leboncoin_capture, encoding="utf-8")

    first = import_captures(session, [tmp_path])
    second = import_captures(session, [tmp_path])

    assert first.collected["leboncoin"].created == 6
    assert second.collected["leboncoin"].created == 0
    assert second.collected["leboncoin"].seen == 6
