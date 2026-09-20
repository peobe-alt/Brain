"""Le point d'entree du compagnon: une page entre, des verdicts sortent.

C'est toute la surface de l'extension. Elle publie la page que le navigateur
affiche deja, et recoit un verdict par annonce, indexe sur l'identifiant du
site pour qu'elle pose chaque pastille sur la bonne carte.

Le chemin complet - page, extension, serveur, verdicts, annotation - a ete
verifie dans un vrai Chromium avec l'extension chargee, contre une page
servie sous l'origine leboncoin.fr avec sa politique de securite active:
6 annonces, 6 pastilles. Ce fichier verrouille la moitie Python.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).parent / "fixtures"
LBC_URL = "https://www.leboncoin.fr/recherche?category=2&text=twingo"


@pytest.fixture()
def client(session):
    """Un client HTTP branche sur la base jetable de la session de test."""
    from carexpert.api.app import app

    return TestClient(app)


@pytest.fixture(scope="module")
def results_page() -> str:
    return (FIXTURES / "leboncoin_search.html").read_text(encoding="utf-8")


def test_a_captured_page_comes_back_as_verdicts(client, results_page):
    answer = client.post("/api/capture", json={"html": results_page, "url": LBC_URL})

    assert answer.status_code == 200
    body = answer.json()
    assert body["count"] == 6
    assert body["source"] == "leboncoin"
    assert len(body["verdicts"]) == 6


def test_the_source_comes_from_the_address_the_extension_reads(client, results_page):
    """L'extension lit la barre d'adresse: c'est la que l'utilisateur est.

    Sans elle, une page sans lien canonique retombait sur la source
    "capture", et les annonces n'etaient comparables a rien.
    """
    with_url = client.post(
        "/api/capture", json={"html": results_page, "url": LBC_URL}
    ).json()
    without = client.post("/api/capture", json={"html": results_page}).json()

    assert with_url["source"] == "leboncoin"
    assert without["source"] == "capture"


def test_each_verdict_carries_what_a_badge_needs(client, results_page):
    """Une pastille se pose sur une carte du site, dans une liste qui defile.

    Elle a besoin de l'identifiant pour trouver sa carte, et de quoi tenir en
    deux lignes: un verdict, un score, un ecart.
    """
    body = client.post("/api/capture", json={"html": results_page, "url": LBC_URL}).json()
    verdict = body["verdicts"][0]

    for field in ("source_id", "url", "score", "verdict", "verdict_label",
                  "price_eur", "fair_price_eur", "net_gain_eur", "delta_percent",
                  "title", "km", "year"):
        assert field in verdict, field
    assert verdict["verdict"] in ("grab", "check", "avoid", "unknown")


def test_the_percentage_is_a_percentage(client, results_page):
    """`delta_pct` porte une fraction ailleurs dans le code: 0,21 pour 21 %.

    L'API du compagnon expose un pourcentage et le dit dans son nom. Sans ca
    la pastille afficherait "0,2 % sous le marche" pour une affaire a -21 %,
    ce que personne ne regarderait deux fois.
    """
    body = client.post("/api/capture", json={"html": results_page, "url": LBC_URL}).json()

    for verdict in body["verdicts"]:
        assert -100 <= verdict["delta_percent"] <= 200


def test_the_best_deal_comes_first(client, results_page):
    body = client.post("/api/capture", json={"html": results_page, "url": LBC_URL}).json()
    scores = [verdict["score"] for verdict in body["verdicts"]]

    assert scores == sorted(scores, reverse=True)


def test_a_page_with_no_advert_answers_plainly(client):
    answer = client.post(
        "/api/capture",
        json={"html": "<html><body><p>Page d'accueil</p></body></html>",
              "url": "https://www.leboncoin.fr/"},
    )

    assert answer.status_code == 200
    body = answer.json()
    assert body["count"] == 0
    assert "aucune annonce" in body["detail"]


def test_an_empty_body_is_refused(client):
    assert client.post("/api/capture", json={"html": "   "}).status_code == 400
    assert client.post("/api/capture", json={}).status_code == 400


def test_the_same_page_twice_does_not_duplicate(client, results_page, session):
    """On reste sur la meme page, on reclique: rien ne doit se dedoubler."""
    from carexpert.db import Listing, select

    first = client.post("/api/capture", json={"html": results_page, "url": LBC_URL}).json()
    second = client.post("/api/capture", json={"html": results_page, "url": LBC_URL}).json()

    assert first["count"] == second["count"] == 6
    rows = session.execute(select(Listing)).scalars().all()
    assert len(rows) == 6


def test_the_extension_origin_is_allowed_through(client, results_page):
    """Le navigateur refuse la reponse avant meme de la lire si l'origine
    n'est pas autorisee. L'extension appelle depuis `chrome-extension://`."""
    answer = client.post(
        "/api/capture",
        json={"html": results_page, "url": LBC_URL},
        headers={"Origin": "chrome-extension://abcdefghijklmnopabcdefghijklmnop"},
    )

    assert answer.status_code == 200
    assert answer.headers.get("access-control-allow-origin")


def test_nothing_in_the_capture_path_reaches_the_network(client, results_page, monkeypatch):
    """La promesse du compagnon: c'est un humain qui a ouvert cette page.

    Une requete partant d'ici la rendrait fausse.
    """
    import httpx

    def forbidden(*args, **kwargs):  # pragma: no cover - ne doit jamais courir
        raise AssertionError("une capture ne fait pas de requete")

    monkeypatch.setattr(httpx.Client, "send", forbidden)
    body = client.post("/api/capture", json={"html": results_page, "url": LBC_URL}).json()

    assert body["count"] == 6


# --- l'extension elle-meme ------------------------------------------------

EXTENSION = Path(__file__).resolve().parent.parent / "extension"


def test_the_manifest_declares_what_the_extension_actually_needs():
    manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["manifest_version"] == 3
    hosts = manifest["host_permissions"]
    # Sans cette permission, le service worker ne peut pas joindre CarExpert,
    # et la page, elle, en a l'interdiction par la politique du site.
    assert any("127.0.0.1" in host for host in hosts)
    matched = manifest["content_scripts"][0]["matches"]
    assert any("leboncoin.fr" in pattern for pattern in matched)
    for name in ("background.js", "content.js", "badges.css", "popup.html", "popup.js"):
        assert (EXTENSION / name).exists(), name


def test_the_stylesheet_never_touches_the_site_own_classes():
    """L'extension pose ses pastilles dans la page de quelqu'un d'autre.

    Une regle qui deborde casse la mise en page du site, et c'est nous que
    l'utilisateur accuse (invariant 23).
    """
    import re

    css = (EXTENSION / "badges.css").read_text(encoding="utf-8")
    selectors = re.findall(r"^([^@{}/\s][^{]*)\{", css, re.MULTILINE)

    for selector in selectors:
        for part in selector.split(","):
            part = part.strip()
            if part:
                assert "carexpert" in part, f"selecteur non prefixe: {part}"


def test_only_browser_extensions_may_call_the_local_server(client, results_page):
    """Le serveur tourne en permanence sur la machine de l'utilisateur.

    Ouvrir le partage d'origine a tout `https://` ouvrait a tout le web:
    n'importe quelle page visitee pouvait lire /api/deals, donc l'inventaire,
    les prix et les veilles. Seules les extensions sont admises.
    """
    for hostile in ("https://evil.example.com", "http://attaquant.fr",
                    "https://www.leboncoin.fr"):
        answer = client.get("/api/deals", headers={"Origin": hostile})
        assert not answer.headers.get("access-control-allow-origin"), hostile

    for extension in ("chrome-extension://abcdefghijklmnopabcdefghijklmnop",
                      "moz-extension://1234abcd-5678-90ef-aaaa-bbbbccccdddd"):
        answer = client.get("/api/deals", headers={"Origin": extension})
        assert answer.headers.get("access-control-allow-origin") == extension


def test_an_advert_without_a_description_stays_to_be_reopened(client, session):
    """Une page de resultats ne porte pas toujours le descriptif.

    Or c'est la que se trouvent "moteur a revoir" et "vendu sans controle
    technique" (invariant 14). En marquant ces annonces "analysees", la
    capture les faisait sauter a la passe large du scan suivant, donc a la
    passe de detail qui serait justement allee chercher ce descriptif.

    leboncoin, lui, publie le descriptif des la liste: ses annonces n'ont
    rien a rouvrir, et le test le verifie aussi.
    """
    from carexpert.db import Listing, select

    bare = {"props": {"ads": [
        {"list_id": 7100000001, "url": "https://www.leboncoin.fr/ad/voitures/7100000001",
         "subject": "Peugeot 208 1.2 PureTech 82 Active", "price": [6900],
         "attributes": [{"key": "mileage", "value": "96000"},
                        {"key": "regdate", "value": "2017"}]},
    ]}}
    page = ('<html><head><link rel="canonical" href="' + LBC_URL + '"/>'
            '<script id="__NEXT_DATA__" type="application/json">'
            + json.dumps(bare) + "</script></head><body></body></html>")

    client.post("/api/capture", json={"html": page, "url": LBC_URL})
    row = session.execute(
        select(Listing).where(Listing.source_id == "7100000001")
    ).scalar_one()

    assert not row.description
    assert row.score is not None, "elle est tout de meme notee tout de suite"
    assert row.analyzed_at is None, "mais elle reste a rouvrir"


def test_an_advert_that_came_with_its_description_is_not_reopened(client,
                                                                  results_page, session):
    """Rouvrir une annonce deja complete coute une requete pour rien."""
    from carexpert.db import Listing, select

    client.post("/api/capture", json={"html": results_page, "url": LBC_URL})
    rows = session.execute(select(Listing)).scalars().all()

    assert rows and all(row.description for row in rows)
    assert all(row.analyzed_at is not None for row in rows)
