"""Doing things from the browser: launching a scan, keeping a watchlist.

The dashboard used to be read-only, which meant every scan started in a
terminal. These are the paths that make the tool usable without one, so they
are tested as the paths they are: an HTTP request that must not hang, and a
background job whose progress a page can poll.
"""

from __future__ import annotations

import re
import time

import pytest
from fastapi.testclient import TestClient

from carexpert.api import jobs
from carexpert.pipeline.run import ScanReport


@pytest.fixture()
def client(session, monkeypatch):
    """A client whose scans never touch the network."""
    calls: list[dict] = []

    def fake_scan(_session, **kwargs):
        calls.append(kwargs)
        progress = kwargs.get("on_progress")
        if progress:
            progress("Collecte des annonces", 20)
            progress("Estimation", 60)
        report = ScanReport()
        report.valued = 3
        report.top = [{"score": 82}, {"score": 40}]
        return report

    monkeypatch.setattr("carexpert.pipeline.scan", fake_scan)
    # Un runner neuf par test: l'etat d'un scan ne doit pas fuir sur le suivant.
    monkeypatch.setattr(jobs, "runner", jobs.ScanRunner())
    monkeypatch.setattr("carexpert.api.app.runner", jobs.runner)

    from carexpert.api.app import app

    test_client = TestClient(app)
    test_client.scan_calls = calls
    return test_client


def _wait(client, job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = client.get(f"/api/scan/{job_id}").json()
        if payload["done"]:
            return payload
        time.sleep(0.02)
    raise AssertionError("le scan ne s'est jamais termine")


# --- Lancer une recherche -------------------------------------------------


def test_the_search_page_renders(client):
    response = client.get("/recherche")
    assert response.status_code == 200
    assert "Coller une recherche" in response.text
    assert "autoscout24" in response.text.lower()


def test_a_pasted_url_starts_a_scan_without_blocking_the_page(client):
    response = client.post(
        "/recherche",
        data={"url": "https://www.autoscout24.fr/lst/volkswagen/golf-tous?cy=F"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("/scan/")

    job_id = location.rsplit("/", 1)[1]
    payload = _wait(client, job_id)
    assert payload["status"] == "termine"
    assert payload["percent"] == 100

    # Le site a ete devine depuis l'adresse: l'utilisateur n'a rien choisi.
    assert client.scan_calls[0]["sources"] == ["autoscout24"]
    assert client.scan_calls[0]["search_url"].endswith("cy=F")


def test_criteria_work_without_any_url(client):
    response = client.post(
        "/recherche",
        data={"make": "Volvo", "model": "V70", "price_max": "12000", "country": "DE"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    _wait(client, response.headers["location"].rsplit("/", 1)[1])

    query = client.scan_calls[0]["query"]
    assert query.make == "Volvo" and query.model == "V70"
    assert query.price_max == 12000 and query.countries == ["DE"]
    assert client.scan_calls[0]["search_url"] is None


@pytest.mark.parametrize(
    "payload, expected",
    [
        ({"url": "autoscout24.fr/lst/volvo"}, "ne ressemble pas a un lien"),
        ({"url": "https://www.exemple-inconnu.fr/annonces"}, "pas encore connu"),
        ({}, "au moins une marque"),
    ],
)
def test_a_mistake_is_explained_in_plain_words(client, payload, expected):
    """L'utilisateur n'est pas developpeur: une erreur doit se lire."""
    response = client.post("/recherche", data=payload, follow_redirects=True)
    assert response.status_code == 200
    assert expected in response.text
    assert client.scan_calls == []


def test_two_scans_at_once_are_refused(client, monkeypatch):
    """Deux scans ensemble interrogeraient le site deux fois plus vite."""
    import threading

    release = threading.Event()

    def slow_scan(_session, **kwargs):
        release.wait(timeout=5)
        return ScanReport()

    monkeypatch.setattr("carexpert.pipeline.scan", slow_scan)

    first = client.post("/recherche", data={"make": "Volvo"}, follow_redirects=False)
    assert first.status_code == 303

    second = client.post("/recherche", data={"make": "Audi"}, follow_redirects=True)
    assert "deja en cours" in second.text
    release.set()


def test_the_button_comes_back_once_the_scan_is_over(client):
    """Sinon l'outil ne sert qu'une fois: le bouton reste grise pour toujours."""
    response = client.post("/recherche", data={"make": "Volvo"}, follow_redirects=False)
    _wait(client, response.headers["location"].rsplit("/", 1)[1])

    page = client.get("/recherche")
    assert "Un scan est en cours" not in page.text
    assert "disabled" not in page.text
    assert client.get("/veilles").text.count("disabled") == 0

    again = client.post("/recherche", data={"make": "Audi"}, follow_redirects=False)
    assert again.status_code == 303


def test_an_unknown_scan_is_a_404(client):
    assert client.get("/scan/nexistepas").status_code == 404
    assert client.get("/api/scan/nexistepas").status_code == 404


def test_the_progress_page_shows_what_is_happening(client):
    response = client.post("/recherche", data={"make": "Volvo"}, follow_redirects=False)
    job_id = response.headers["location"].rsplit("/", 1)[1]
    _wait(client, job_id)

    page = client.get(f"/scan/{job_id}")
    assert page.status_code == 200
    assert "Voir les bonnes affaires" in page.text


def test_a_failing_scan_says_so_instead_of_dying(client, monkeypatch):
    def broken_scan(_session, **kwargs):
        raise RuntimeError("le site a repondu 503")

    monkeypatch.setattr("carexpert.pipeline.scan", broken_scan)
    response = client.post("/recherche", data={"make": "Volvo"}, follow_redirects=False)
    payload = _wait(client, response.headers["location"].rsplit("/", 1)[1])

    assert payload["status"] == "echec"
    assert "503" in payload["message"]
    # Le serveur repond toujours.
    assert client.get("/recherche").status_code == 200


# --- Veilles --------------------------------------------------------------


def test_a_search_can_be_kept_as_a_watchlist_and_replayed(client):
    client.post(
        "/recherche",
        data={
            "url": "https://www.autoscout24.fr/lst/volvo/v70?cy=F",
            "save_as": "V70 francaises",
        },
        follow_redirects=True,
    )

    page = client.get("/veilles")
    assert "V70 francaises" in page.text
    assert "autoscout24.fr/lst/volvo/v70" in page.text

    from carexpert.db import Watchlist, session_scope

    with session_scope() as db:
        saved = db.query(Watchlist).filter(Watchlist.name == "V70 francaises").one()
        watchlist_id = saved.id
        assert saved.search_url.endswith("cy=F")
        assert saved.sources == ["autoscout24"]

    client.scan_calls.clear()
    replay = client.post(f"/veilles/{watchlist_id}/lancer", follow_redirects=False)
    assert replay.status_code == 303
    _wait(client, replay.headers["location"].rsplit("/", 1)[1])
    assert client.scan_calls[0]["search_url"].endswith("cy=F")

    with session_scope() as db:
        assert db.get(Watchlist, watchlist_id).last_run_at is not None

    client.post(f"/veilles/{watchlist_id}/supprimer", follow_redirects=True)
    with session_scope() as db:
        assert db.get(Watchlist, watchlist_id) is None


def test_the_watchlist_page_guides_a_first_time_user(client):
    page = client.get("/veilles")
    assert page.status_code == 200
    assert "Aucune veille" in page.text
    assert "Nouvelle recherche" in page.text


# --- Feuille de style -----------------------------------------------------


def _plain_class_selectors(css: str) -> list[str]:
    """Les selecteurs d'une seule classe, hors @media.

    Une regle sous @media redefinit volontairement une classe pour un petit
    ecran: ce n'est pas une collision, c'est le but.
    """
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)

    found: list[str] = []
    depth = 0
    skip_until = None
    for header, brace in re.findall(r"([^{}]*)([{}])", css):
        if brace == "}":
            depth -= 1
            if skip_until is not None and depth <= skip_until:
                skip_until = None
            continue
        depth += 1
        header = header.strip()
        if header.startswith("@"):
            if skip_until is None:
                skip_until = depth - 1
            continue
        if skip_until is not None:
            continue
        for selector in header.split(","):
            selector = selector.strip()
            if re.fullmatch(r"\.[a-z][\w-]*", selector):
                found.append(selector)
    return found


def test_no_class_is_styled_in_two_places():
    """Une classe redefinie plus bas repeint une page qu'on ne regarde pas.

    Mesure: les pages ajoutees reutilisaient .card, .panel et .bar. Le titre
    de "Nouvelle recherche" sortait en petites capitales grises, les veilles
    s'empilaient au centre, et la barre de confiance de la fiche d'annonce
    changeait d'epaisseur. Rien dans ces trois pages ne le laissait voir.
    """
    from carexpert.api.app import WEB_DIR

    css = (WEB_DIR / "static" / "app.css").read_text()
    selectors = _plain_class_selectors(css)

    twice = sorted({name for name in selectors if selectors.count(name) > 1})
    assert not twice, f"classes definies deux fois: {', '.join(twice)}"
