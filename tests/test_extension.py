"""The browser extension, and the server side that answers it.

Two things are tested here that nothing else covers. First, that a page
captured in the browser is read *exactly* where it is: no request goes back
to the site, which is the whole reason the extension exists. Second, that a
local server open to the browser stays closed to the rest of the web.

The extension's own files are tested too - manifest, stylesheet - because a
mistake there is silent: the browser refuses the extension whole, without
telling the page that expected it.
"""

from __future__ import annotations

import json
import re
import socket
import zipfile

import pytest
from fastapi.testclient import TestClient

from carexpert.api import extension as ext
from carexpert.sources.capture import read_page

SEARCH_URL = "https://www.autoscout24.fr/lst/volvo/v70?cy=F"
ADVERT_URL = (
    "https://www.autoscout24.fr/offres/volvo-v70-d5-summum"
    "-2b9a71c4-5d3e-4f21-9a7c-1d2e3f4a5b6c"
)


@pytest.fixture()
def search_html() -> str:
    from pathlib import Path

    return (Path(__file__).parent / "fixtures" / "autoscout24_search.html").read_text()


@pytest.fixture()
def client(session, monkeypatch):
    monkeypatch.setattr(ext, "activity", ext.Activity())
    from carexpert.api.app import app

    return TestClient(app)


def _advert_html(with_similar: bool = True) -> str:
    """One advert page, carrying a carousel of similar cars like the real ones."""
    car = {
        "@type": "Car",
        "name": "Volvo V70 D5 Summum Geartronic",
        "brand": {"@type": "Brand", "name": "Volvo"},
        "model": "V70",
        "vehicleModelDate": "2012",
        "mileageFromOdometer": {"@type": "QuantitativeValue", "value": "145000",
                                "unitCode": "KMT"},
        "fuelType": "Diesel",
        "offers": {"@type": "Offer", "price": "12900", "priceCurrency": "EUR"},
        "description": "Carnet d'entretien complet, distribution faite a 140 000 km.",
    }
    similar = {
        "@type": "SearchResultsPage",
        "mainEntity": {
            "@type": "ItemList",
            "itemListElement": [
                {
                    "@type": "ListItem",
                    "url": f"https://www.autoscout24.fr/offres/voisine-{index}"
                           f"-0000000{index}-1111-2222-3333-44444444444{index}",
                    "item": {
                        "@type": "Car",
                        "name": f"Volvo V70 voisine {index}",
                        "offers": {"@type": "Offer", "price": "9900", "priceCurrency": "EUR"},
                    },
                }
                for index in range(3)
            ],
        },
    }
    blocks = [car] + ([similar] if with_similar else [])
    scripts = "".join(
        f'<script type="application/ld+json">{json.dumps(block)}</script>' for block in blocks
    )
    return f"<html><head>{scripts}</head><body><h1>Volvo V70</h1></body></html>"


# --- Lire une page sans la redemander -------------------------------------


def test_a_results_page_is_read_without_asking_the_site_anything(search_html, monkeypatch):
    """Toute la raison d'etre de l'extension: la page est deja chargee.

    Une requete de plus serait une requete de trop: elle se verrait dans les
    journaux du site, elle tomberait sur les protections que le projet ne
    contourne pas, et elle rendrait l'extension inutile sur les trois sites
    qui rendent leurs annonces en JavaScript.
    """
    def refuse(*args, **kwargs):
        raise AssertionError("une page capturee ne doit jamais etre rejouee vers le site")

    monkeypatch.setattr(socket.socket, "connect", refuse)

    page = read_page(search_html, SEARCH_URL)
    assert page.kind == "search"
    assert page.source == "autoscout24"
    assert len(page.listings) == 3
    assert all(listing.price_eur for listing in page.listings)


def test_an_advert_page_is_not_read_as_its_own_list_of_similar_cars():
    """Une fiche publie six voisines: les lire ferait perdre la voiture lue.

    C'est l'adresse qui tranche, parce que c'est la seule chose qui distingue
    a coup sur les deux pages: les sites decrivent la forme de leurs URL
    d'annonce dans leur fichier YAML.
    """
    html = _advert_html(with_similar=True)

    advert = read_page(html, ADVERT_URL)
    assert advert.kind == "listing"
    assert len(advert.listings) == 1
    assert "Summum" in advert.listings[0].title
    assert advert.listings[0].price_eur == 12900

    # Le meme HTML, servi sous une URL de recherche, est bien une liste.
    listed = read_page(html, SEARCH_URL)
    assert listed.kind == "search"
    assert len(listed.listings) == 3


def test_a_results_page_is_never_stored_as_a_single_advert():
    """Une page de recherche qui porte un encart sponsorise reste une page.

    Sinon l'encart entre en base sous l'adresse de la recherche, avec son
    identifiant, et devient un comparable fantome que plus rien ne met a
    jour. Le site dit a quoi ressemblent ses URL d'annonce; celle-ci n'y
    ressemble pas.
    """
    html = _advert_html(with_similar=False)   # une seule voiture en JSON-LD

    search = read_page(html, "https://www.leboncoin.fr/recherche?category=2&text=golf")
    assert search.kind == "unknown"
    assert search.listings == []

    advert = read_page(html, "https://www.leboncoin.fr/ad/voitures/2345678901")
    assert advert.kind == "listing"
    assert len(advert.listings) == 1


def test_an_unknown_site_is_explained_rather_than_ignored():
    page = read_page("<html></html>", "https://www.exemple-inconnu.fr/annonces/1")
    assert page.kind == "unknown"
    assert page.source is None
    assert "pas encore connu" in page.reason


def test_a_known_site_with_nothing_readable_says_which_case_it_is():
    """Deux echecs differents, deux consignes differentes.

    "Ouvrez une page de resultats" quand on est bien sur une page de
    resultats laisse l'utilisateur chercher ce qu'il a mal fait, alors qu'il
    n'a rien fait de mal: c'est le site qui ne publie pas ses annonces dans
    un format lisible.
    """
    page = read_page("<html><body>Nos agences</body></html>", SEARCH_URL)
    assert page.kind == "unknown"
    assert page.source == "autoscout24"
    assert "autoscout24" in page.reason
    assert "pas encore pris en charge" in page.reason


def test_the_country_comes_from_the_domain_being_browsed():
    """Le meme catalogue vit sous dix domaines; le pays change la valorisation."""
    html = _advert_html(with_similar=False)
    german = read_page(html, ADVERT_URL.replace("autoscout24.fr", "autoscout24.de"))
    assert german.country == "DE"
    assert read_page(html, ADVERT_URL).country == "FR"


# --- De la page au verdict -------------------------------------------------


def test_a_captured_page_comes_back_scored_and_placeable(client, search_html):
    response = client.post("/api/extension/page", json={"url": SEARCH_URL, "html": search_html})
    assert response.status_code == 200
    payload = response.json()

    assert payload["kind"] == "search"
    assert payload["count"] == 3
    assert "3 annonces lues" in payload["message"]

    for result in payload["results"]:
        assert result["score"] is not None
        assert result["verdict_label"]
        # Sans identifiant ni adresse, l'extension ne sait pas quelle carte
        # annoter: le badge ne s'afficherait nulle part.
        assert result["source_id"] and result["url"]
        assert result["id"] > 0

    from carexpert.db import Listing, session_scope

    with session_scope() as db:
        assert db.query(Listing).count() == 3


def test_reading_the_same_page_twice_does_not_double_the_database(client, search_html):
    """Revenir sur une page de resultats est le geste le plus banal qui soit."""
    for _ in range(2):
        client.post("/api/extension/page", json={"url": SEARCH_URL, "html": search_html})

    from carexpert.db import Listing, session_scope

    with session_scope() as db:
        assert db.query(Listing).count() == 3


def test_a_page_with_nothing_to_read_answers_in_plain_words(client):
    payload = client.post(
        "/api/extension/page", json={"url": SEARCH_URL, "html": "<html>rien</html>"}
    ).json()
    assert payload["count"] == 0
    assert "pas encore pris en charge" in payload["message"]


def test_a_page_too_heavy_to_read_is_refused_with_a_reason(client):
    payload = {"url": SEARCH_URL, "html": "x" * (ext.MAX_HTML_BYTES + 1)}
    response = client.post("/api/extension/page", json=payload)
    assert response.status_code == 413
    assert "trop lourde" in response.json()["detail"]


def test_the_install_page_knows_whether_the_extension_talks(client, search_html):
    assert "Aucune page recue" in client.get("/extension").text

    client.post("/api/extension/page", json={"url": SEARCH_URL, "html": search_html})
    activity = client.get("/api/extension/status").json()["activity"]
    assert activity["pages"] == 1
    assert activity["listings"] == 3
    assert activity["last_url"] == SEARCH_URL


def test_the_page_says_which_adverts_have_not_been_opened_yet(client, search_html):
    """Le descriptif n'existe que sur la fiche, et c'est la que sont les pieges."""
    listed = client.post(
        "/api/extension/page", json={"url": SEARCH_URL, "html": search_html}
    ).json()["results"]
    assert all(result["has_detail"] is False for result in listed)

    # La meme annonce, ouverte: son descriptif entre en base.
    target = listed[0]
    detail = client.post("/api/extension/page", json={
        "url": target["url"],
        "html": _advert_html(with_similar=False).replace(
            "Volvo V70 D5 Summum Geartronic", target["title"]),
    }).json()
    assert detail["kind"] == "listing"
    assert detail["results"][0]["has_detail"] is True


# --- Ce que la base ne permet pas encore de dire --------------------------


def _golf(session, *, source_id, price, km, year=2016, fuel="diesel", gearbox="manual"):
    """Une Golf TDI de plus dans la base, pour nourrir les comparables."""
    from carexpert.normalize import enrich
    from carexpert.pipeline.ingest import ingest
    from carexpert.schemas import Fuel, Gearbox, ListingData

    listing = enrich(ListingData(
        source="autoscout24", source_id=source_id,
        url=f"https://www.autoscout24.fr/offres/golf-{source_id}",
        title=f"Volkswagen Golf 1.6 TDI 110 Confortline {year}",
        price=price, km=km, year=year,
        fuel=Fuel(fuel), gearbox=Gearbox(gearbox), country="FR",
    ))
    ingest(session, [listing])
    session.flush()


def test_a_page_of_six_cars_prices_none_of_them(client):
    """Le defaut mesure, et la raison d'etre du plancher par palier.

    Sur une page de resultats Golf lue base vide, l'outil annoncait une
    Golf 7 1.6 TDI de 255 000 km a 5 900 EUR comme "A SAISIR, 45% sous le
    marche, +4 749 EUR de gain". Ses trois comparables: une e-Golf
    electrique, un break TDI et une GTE hybride rechargeable. Six voitures
    qui se comparent entre elles ne sont pas un marche.
    """
    from pathlib import Path as _Path

    golf = (_Path(__file__).parent / "fixtures" / "autoscout24_search_golf.html").read_text()
    payload = client.post(
        "/api/extension/page",
        json={"url": "https://www.autoscout24.fr/lst/volkswagen/golf", "html": golf},
    ).json()

    assert payload["count"] == 6
    assert all(result["fair_price_eur"] is None for result in payload["results"])
    assert all(result["net_gain_eur"] == 0 for result in payload["results"])
    assert not any(result["verdict"] == "grab" for result in payload["results"])
    assert "base insuffisante" in payload["message"]

    # Et le bandeau sait quoi dire a la place: ce qui manque, en nombres.
    for result in payload["results"]:
        basis = result["basis"]
        assert basis["enough"] is False
        assert basis["comps"] < basis["needed"] or basis["needed"] == 0
        assert basis["known"] >= 1


def test_the_price_appears_once_the_base_can_carry_it(client, session):
    """L'inverse du test precedent: la regle n'est pas "ne rien dire".

    Le meme vehicule, avec de vraies annonces comparables en base, doit
    retrouver une estimation. Sans quoi le plancher n'aurait fait que rendre
    l'outil muet.
    """
    for index in range(8):
        _golf(session, source_id=f"comp{index}", price=12_000 + index * 250, km=120_000 + index * 2000)
    session.commit()

    from carexpert.db import Listing, session_scope
    from carexpert.pipeline.capture import payload as overlay_payload
    from carexpert.pipeline.run import value_and_score

    _golf(session, source_id="cible", price=9_900, km=124_000)
    session.commit()

    with session_scope() as db:
        row = db.query(Listing).filter(Listing.source_id == "cible").one()
        value_and_score(db, row)
        data = overlay_payload(db, row)

    assert data["fair_price_eur"], "avec neuf Golf TDI comparables, le prix doit se situer"
    assert data["basis"]["enough"] is True
    assert data["basis"]["comps"] >= data["basis"]["needed"]
    assert data["basis"]["tier"] in ("strict", "modele_carburant", "modele")
    assert data["net_gain_eur"] > 0          # 9 900 EUR face a des Golf a 12 000


# --- L'expertise approfondie, qui coute -----------------------------------


def test_without_an_api_key_the_deep_reading_declines_instead_of_failing(client, search_html):
    """Invariant: la couche experte ne leve jamais d'exception."""
    first = client.post(
        "/api/extension/page", json={"url": SEARCH_URL, "html": search_html}
    ).json()["results"][0]

    payload = client.post("/api/extension/deep", json={"id": first["id"]}).json()
    assert payload["ok"] is False
    assert "cle API" in payload["message"]


def test_with_a_key_the_deep_reading_stores_what_claude_said(client, search_html, monkeypatch):
    """Un chemin conditionne par une cle API doit avoir son test avec un double."""
    from carexpert import config
    from carexpert.expert import analyst as analyst_module
    from carexpert.expert.analyst import ExpertAnalyst
    from carexpert.expert.photos import PreparedPhoto
    from carexpert.pipeline import run as run_module

    monkeypatch.setenv("ANTHROPIC_API_KEY", "cle-de-test")
    config.get_settings.cache_clear()

    from test_analyst import StubClient  # meme double que la couche experte

    monkeypatch.setattr(
        analyst_module, "prepare_photos",
        lambda photos, limit=None: [
            PreparedPhoto(index=i, url=p.url, media_type="image/jpeg", data_b64="Zm9v")
            for i, p in enumerate(photos[:2])
        ],
    )
    monkeypatch.setattr(
        run_module, "ExpertAnalyst", lambda: ExpertAnalyst(client=StubClient())
    )

    first = client.post(
        "/api/extension/page", json={"url": SEARCH_URL, "html": search_html}
    ).json()["results"][0]

    payload = client.post("/api/extension/deep", json={"id": first["id"]}).json()
    assert payload["ok"] is True
    assert payload["cost_eur"] > 0
    assert "Voiture saine" in payload["result"]["summary"]
    assert payload["result"]["photos_analyzed"] == payload["photos_analyzed"]

    # Le tableau de bord doit montrer la meme chose que le bandeau.
    stored = client.get(f"/api/listing/{first['id']}").json()
    assert stored["report"]["summary"] == payload["result"]["summary"]
    config.get_settings.cache_clear()


def test_a_deep_reading_of_an_unknown_advert_is_a_404(client, monkeypatch):
    from carexpert import config

    monkeypatch.setenv("ANTHROPIC_API_KEY", "cle-de-test")
    config.get_settings.cache_clear()
    assert client.post("/api/extension/deep", json={"id": 999999}).status_code == 404
    config.get_settings.cache_clear()


def test_a_breakdown_says_what_to_do_instead_of_a_status_code(client, search_html, monkeypatch):
    """L'ecran de l'utilisateur, a ce moment-la, c'est le site du vendeur.

    Mesure: base de donnees devenue inaccessible, l'extension affichait
    "CarExpert a repondu 500" au-dessus des annonces. Ni ce qui se passe,
    ni quoi faire.
    """
    def broken(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr("carexpert.api.extension.absorb", broken)
    response = client.post("/api/extension/page", json={"url": SEARCH_URL, "html": search_html})

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert "n'a pas pu enregistrer cette page" in detail
    assert "rechargez la page" in detail
    # Le serveur repond toujours pour la page suivante.
    assert client.get("/api/extension/status").json()["ok"] is True


def test_a_broken_deep_reading_does_not_leave_a_raw_error(client, search_html, monkeypatch):
    from carexpert import config

    monkeypatch.setenv("ANTHROPIC_API_KEY", "cle-de-test")
    config.get_settings.cache_clear()
    first = client.post(
        "/api/extension/page", json={"url": SEARCH_URL, "html": search_html}
    ).json()["results"][0]

    def broken(*args, **kwargs):
        raise RuntimeError("disk I/O error")

    monkeypatch.setattr("carexpert.api.extension.deep_analyze", broken)
    payload = client.post("/api/extension/deep", json={"id": first["id"]}).json()
    assert payload["ok"] is False
    assert "Expertise impossible" in payload["message"]
    config.get_settings.cache_clear()


# --- Un serveur local est ouvert a tout le navigateur ---------------------


@pytest.mark.parametrize(
    "origin, allowed",
    [
        ("", True),                                   # le tableau de bord, curl
        ("http://127.0.0.1:8000", True),              # le tableau de bord, en POST
        ("https://www.autoscout24.fr", True),
        ("https://www.autoscout24.de", True),         # le meme site, autre pays
        ("https://suchen.mobile.de", True),
        ("chrome-extension://abcdefghijklmnop", True),
        ("moz-extension://abcdefghijklmnop", True),
        ("https://autoscout24.pirate.example", False),  # le nom, pas le domaine
        ("https://www.mobile.fr", False),               # un pays que le site n'a pas
        ("https://collecteur-de-donnees.example", False),
        ("ftp://www.autoscout24.fr", False),
    ],
)
def test_only_the_sites_we_know_may_post_pages(client, origin, allowed):
    """N'importe quelle page ouverte peut poster sur 127.0.0.1.

    Sans ce filtre, un site tiers remplirait la base de faux comparables, et
    toutes les estimations deriveraient avec: une annonce reelle ne se compare
    qu'a des annonces reelles.
    """
    headers = {"Origin": origin} if origin else {}
    response = client.post(
        "/api/extension/page",
        json={"url": SEARCH_URL, "html": "<html></html>"},
        headers=headers,
    )
    assert (response.status_code != 403) is allowed
    if allowed and origin:
        assert response.headers["access-control-allow-origin"] == origin


def test_the_browser_preflight_is_answered(client):
    response = client.options(
        "/api/extension/page",
        headers={
            "Origin": "https://www.autoscout24.fr",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 204
    assert response.headers["access-control-allow-origin"] == "https://www.autoscout24.fr"
    assert "content-type" in response.headers["access-control-allow-headers"]


def test_the_rest_of_the_dashboard_is_not_guarded(client):
    """Le filtre ne doit pas se mettre en travers des pages du tableau de bord."""
    assert client.get("/", headers={"Origin": "https://ailleurs.example"}).status_code == 200


# --- Les fichiers de l'extension ------------------------------------------


def _manifest() -> dict:
    return json.loads((ext.EXTENSION_DIR / "manifest.json").read_text())


#: Chrome n'accepte le joker que pour l'hote entier ou en tete de domaine.
#: `*://*.autoscout24.*/*` est invalide, et une seule ligne invalide fait
#: refuser l'extension entiere, pas seulement ce site.
MATCH_PATTERN = re.compile(r"^(\*|https?)://(\*|(\*\.)?[a-z0-9][a-z0-9.-]*)(:\d+)?/.*$")


def test_every_match_pattern_is_one_the_browser_accepts():
    manifest = _manifest()
    patterns = manifest["content_scripts"][0]["matches"] + manifest["host_permissions"]
    invalid = [pattern for pattern in patterns if not MATCH_PATTERN.match(pattern)]
    assert not invalid, f"motifs refuses par le navigateur: {invalid}"


def test_every_site_carexpert_knows_is_a_site_the_extension_watches():
    """Ajouter un YAML sans toucher au manifest laisse le site sans extension."""
    manifest = _manifest()
    matches = manifest["content_scripts"][0]["matches"]
    permissions = manifest["host_permissions"]

    def covered(patterns: list[str], host: str) -> bool:
        wanted = {host, f"*.{host}"}
        return any(pattern.split("://")[1].split("/")[0] in wanted for pattern in patterns)

    for host in ext.known_site_hosts():
        assert covered(matches, host), f"{host} n'est suivi par aucun content script"
        # Sans permission d'hote, le bouton "Analyser la page" ne peut pas
        # parler a l'onglet ouvert.
        assert covered(permissions, host), f"{host} n'est dans aucune permission d'hote"


def test_the_extension_only_talks_to_the_local_machine():
    remote = [
        url for url in _manifest()["host_permissions"]
        if url.startswith("http://") and "127.0.0.1" not in url and "localhost" not in url
    ]
    assert not remote, f"permission vers l'exterieur: {remote}"


def test_every_file_the_manifest_names_exists():
    manifest = _manifest()
    named = [
        manifest["background"]["service_worker"],
        manifest["action"]["default_popup"],
        *manifest["icons"].values(),
        *manifest["content_scripts"][0]["js"],
        *manifest["content_scripts"][0]["css"],
    ]
    missing = [name for name in named if not (ext.EXTENSION_DIR / name).exists()]
    assert not missing, f"fichiers annonces mais absents: {missing}"
    assert (ext.EXTENSION_DIR / "popup.js").exists()


def test_the_overlay_stylesheet_never_styles_a_class_twice():
    """Meme raison que pour le tableau de bord, en pire: la page n'est pas a nous.

    Une classe redefinie plus bas repeint un badge que personne ne relit, et
    le defaut ne se voit que sur le site d'un tiers.
    """
    from conftest import classes_defined_twice

    css = (ext.EXTENSION_DIR / "content.css").read_text()
    twice = classes_defined_twice(css)
    assert not twice, f"classes definies deux fois: {', '.join(twice)}"


def test_every_class_the_overlay_uses_is_styled():
    """Une classe oubliee dans la feuille, c'est un badge sans style sur le site."""
    css = (ext.EXTENSION_DIR / "content.css").read_text()
    script = (ext.EXTENSION_DIR / "content.js").read_text()

    styled = set(re.findall(r"\.(carexpert-[a-z0-9-]+)", css))
    used = set(re.findall(r"\b(carexpert-[a-z0-9-]+)", script))
    assert used <= styled, f"classes sans style: {sorted(used - styled)}"
    # Les quatre verdicts colorent le bandeau: aucun ne doit rester gris.
    assert {"carexpert-grab", "carexpert-check", "carexpert-avoid",
            "carexpert-unknown"} <= styled


def test_the_extension_can_be_downloaded_as_one_archive(client):
    """Installer ne doit pas demander un terminal: on telecharge, on decompresse."""
    response = client.get("/extension/carexpert-extension.zip")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"

    import io

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        assert "manifest.json" in names
        assert "content.js" in names
        assert "icons/icon-128.png" in names
        json.loads(archive.read("manifest.json"))


def test_the_install_page_says_where_the_folder_is(client):
    page = client.get("/extension")
    assert page.status_code == 200
    assert str(ext.EXTENSION_DIR) in page.text
    assert "carexpert-extension.zip" in page.text
    assert "autoscout24" in page.text
