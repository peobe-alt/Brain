"""Lire les annonces que les sites rendent "en JavaScript".

Le constat de depart, mesure sur leboncoin et La Centrale: la page de
resultats ne contient aucun lien d'annonce et aucun balisage schema.org. La
conclusion habituelle - "il faut un navigateur" - est fausse. Ces sites sont
des applications Next.js, et une application Next.js ne va pas chercher sa
premiere page de resultats apres coup: elle la serialise dans le HTML pour
son hydratation. Les annonces sont donc dans la premiere reponse HTTP.

Les deux fixtures reproduisent la **forme** de charge utile publiee par
chaque site - flux flight pour leboncoin, `__NEXT_DATA__` pour La Centrale -
et non une page capturee: ce depot ne collecte pas de site reel dans ses
tests. Ce qu'elles verrouillent, c'est la lecture de ces formes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from carexpert.schemas import Fuel, Gearbox, SellerType
from carexpert.sources.browser import (
    BrowserFetcher,
    detect_challenge,
    fetcher_for,
    page_carries_adverts,
)
from carexpert.sources.embedded import (
    embedded_states,
    extract_listing_from_state,
    extract_listings_from_state,
    find_vehicle_records,
    looks_like_vehicle,
)
from carexpert.sources.fetcher import FetchResult, PoliteFetcher
from carexpert.sources.structured import extract_from_page, extract_listings_from_search

FIXTURES = Path(__file__).parent / "fixtures"
LBC_URL = "https://www.leboncoin.fr/recherche?category=2&text=peugeot+308"
LC_URL = "https://www.lacentrale.fr/listing?makesModelsCommercialNames=RENAULT%3AClio"


@pytest.fixture(scope="module")
def leboncoin_html() -> str:
    return (FIXTURES / "leboncoin_search.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def lacentrale_html() -> str:
    return (FIXTURES / "lacentrale_search.html").read_text(encoding="utf-8")


# --- le piege que ce module resout ----------------------------------------


def test_the_results_page_publishes_no_schema_org_at_all(leboncoin_html):
    """La raison d'etre du module: rien a lire par la voie habituelle."""
    from carexpert.sources.structured import extract_jsonld, extract_listing_links

    assert extract_jsonld(leboncoin_html) == []
    assert extract_listing_links(leboncoin_html, LBC_URL, r"/ad/voitures/\d+") == []


def test_the_adverts_are_nonetheless_in_the_first_response(leboncoin_html):
    rows = extract_listings_from_state(leboncoin_html, base_url=LBC_URL, source="leboncoin")
    assert len(rows) == 6


# --- flux flight (routeur applicatif Next.js) -----------------------------


def test_a_leboncoin_advert_is_read_whole(leboncoin_html):
    rows = extract_listings_from_state(leboncoin_html, base_url=LBC_URL, source="leboncoin")
    advert = next(row for row in rows if row.source_id == "2456789012")

    assert advert.title == "Peugeot 308 1.5 BlueHDi 130 Allure"
    assert advert.url == "https://www.leboncoin.fr/ad/voitures/2456789012"
    assert advert.price == 12500
    assert advert.km == 84000
    assert advert.year == 2019
    assert advert.make == "Peugeot"
    assert advert.model == "308"
    assert advert.power_hp == 130
    assert advert.city == "Rennes"
    assert advert.postcode == "35000"
    assert advert.region == "Bretagne"
    assert advert.seller_type is SellerType.PRIVATE
    assert advert.seller_name == "Julien"
    assert advert.description.startswith("Peugeot 308")


def test_the_attribute_pairs_are_read_by_their_label_not_their_code(leboncoin_html):
    """`fuel` vaut "2" et "Diesel". Le code ne se devine pas, le label se lit.

    C'est ce qui fait la difference entre un vivier de comparables trie par
    energie et un tas ou tout se vaut.
    """
    rows = extract_listings_from_state(leboncoin_html, base_url=LBC_URL, source="leboncoin")
    by_id = {row.source_id: row for row in rows}

    assert by_id["2456789012"].fuel is Fuel.DIESEL
    assert by_id["2456789013"].fuel is Fuel.PETROL
    assert by_id["2456789016"].fuel is Fuel.HYBRID
    assert by_id["2456789012"].gearbox is Gearbox.MANUAL


def test_a_price_wrapped_in_a_list_is_still_a_price(leboncoin_html):
    """leboncoin repond `"price": [12500]`. Lu comme liste, tout est perdu."""
    rows = extract_listings_from_state(leboncoin_html, base_url=LBC_URL, source="leboncoin")
    assert all(row.price and row.price > 1000 for row in rows)


def test_the_large_photos_win_over_the_thumbnails(leboncoin_html):
    """Six photos, pas treize: les vignettes ne sont pas des photos de plus."""
    rows = extract_listings_from_state(leboncoin_html, base_url=LBC_URL, source="leboncoin")
    advert = next(row for row in rows if row.source_id == "2456789012")
    assert len(advert.photos) == 6
    assert all("-large.jpg" in photo.url for photo in advert.photos)


def test_a_professional_seller_is_recognised(leboncoin_html):
    rows = extract_listings_from_state(leboncoin_html, base_url=LBC_URL, source="leboncoin")
    pro = next(row for row in rows if row.source_id == "2456789013")
    assert pro.seller_type is SellerType.PRO
    assert pro.seller_name == "Garage Lefevre"


# --- __NEXT_DATA__ (routeur pages) ----------------------------------------


def test_lacentrale_adverts_are_read_from_next_data(lacentrale_html):
    rows = extract_listings_from_state(lacentrale_html, base_url=LC_URL, source="lacentrale")
    assert len(rows) == 4

    clio = next(row for row in rows if row.source_id == "69123456789")
    assert clio.price == 18990
    assert clio.km == 45000
    assert clio.first_registration.isoformat() == "2021-06-15"
    assert clio.year == 2021
    assert clio.power_hp == 90
    assert clio.gearbox is Gearbox.MANUAL
    assert clio.url == "https://www.lacentrale.fr/auto-occasion-annonce-69123456789.html"
    assert len(clio.photos) == 4


def test_a_missing_title_is_rebuilt_from_the_car(lacentrale_html):
    """Aucune annonce La Centrale ne porte de titre: il se deduit."""
    rows = extract_listings_from_state(lacentrale_html, base_url=LC_URL, source="lacentrale")
    clio = next(row for row in rows if row.source_id == "69123456789")
    assert clio.title == "RENAULT Clio V 1.0 TCe 90 Intens"


def test_the_seller_name_never_becomes_the_title(lacentrale_html):
    """Le piege exact: `seller.name` remonte en `name`, et `name` est un titre.

    Sans separation, les quatre annonces s'appelleraient "Garage Martin",
    "Groupe Rhone Auto", "Particulier"... et la marque se perdrait avec.
    """
    rows = extract_listings_from_state(lacentrale_html, base_url=LC_URL, source="lacentrale")
    titles = {row.title for row in rows}
    assert not titles & {"Garage Martin", "Groupe Rhone Auto", "Particulier"}
    assert all(row.make for row in rows)

    martin = next(row for row in rows if row.source_id == "69123456789")
    assert martin.seller_name == "Garage Martin"
    assert martin.seller_type is SellerType.PRO


def test_the_fiscal_horsepower_is_not_the_engine_power(lacentrale_html):
    """`power: {din: 90, fiscal: 6}`. Prendre 6 ch classerait une Clio en 2CV."""
    rows = extract_listings_from_state(lacentrale_html, base_url=LC_URL, source="lacentrale")
    assert sorted(row.power_hp for row in rows) == [83, 90, 116, 130]


# --- ce qui ne doit pas etre pris pour une annonce ------------------------


def test_filter_facets_are_not_adverts(leboncoin_html):
    """Une facette "Prix, de 500 a 100 000" a un prix et un identifiant.

    Elle n'a ni kilometrage ni annee, et c'est exactement le test.
    """
    rows = extract_listings_from_state(leboncoin_html, base_url=LBC_URL, source="leboncoin")
    assert all(row.source_id.startswith("24567890") for row in rows)
    assert not any(row.title in ("Prix", "Kilometrage", "Bretagne") for row in rows)


def test_a_financing_widget_is_not_a_car():
    assert not looks_like_vehicle(
        {"id": "financing", "price": 189, "period": "mois", "duration": 48}
    )


def test_a_container_does_not_swallow_the_adverts_it_holds():
    """Un bandeau de resultats porte le prix et le kilometrage de la recherche.

    Il passe donc le test "ressemble a une annonce". S'il etait retenu, les
    vingt annonces qu'il contient disparaissaient avec lui - une page pleine
    lue comme une page vide, sans la moindre erreur.
    """
    state = {
        "search": {
            "id": "srch-9912",
            "price": 15000,
            "mileage": 90000,
            "results": [
                {"id": 1, "url": "/ad/voitures/1", "price": 9000, "mileage": 120000,
                 "regdate": 2016, "subject": "Clio IV"},
                {"id": 2, "url": "/ad/voitures/2", "price": 11000, "mileage": 80000,
                 "regdate": 2018, "subject": "208"},
            ],
        }
    }
    records = find_vehicle_records(state)
    assert [record["id"] for record in records] == [1, 2]


# --- les autres formes d'etat --------------------------------------------


def test_a_nuxt_style_assignment_is_read():
    payload = {"data": [{"ads": [
        {"id": "a-77", "url": "/vo/a-77", "prix": 8900, "kilometrage": 143000,
         "annee": 2014, "titre": "Clio III 1.5 dCi 90"},
    ]}]}
    html = (
        "<html><body><script>window.__NUXT__="
        + json.dumps(payload)
        + ";</script></body></html>"
    )
    rows = extract_listings_from_state(html, base_url="https://site.fr/", source="test")
    assert len(rows) == 1
    assert rows[0].price == 8900
    assert rows[0].km == 143000
    assert rows[0].title == "Clio III 1.5 dCi 90"


def test_an_initial_state_assignment_is_read():
    payload = {"listings": [
        {"adId": 4242, "link": "/annonce/4242", "amount": 7500, "odometer": 98000,
         "modelYear": 2015, "headline": "Golf VI"},
    ]}
    html = f"<script>window.__INITIAL_STATE__ = {json.dumps(payload)};</script>"
    rows = extract_listings_from_state(html, base_url="https://site.fr/", source="test")
    assert [(row.price, row.km, row.year) for row in rows] == [(7500, 98000, 2015)]


def test_a_page_without_any_state_yields_nothing():
    assert embedded_states("") == [] or list(embedded_states("")) == []
    assert extract_listings_from_state(
        "<html><body><p>Rien ici</p></body></html>",
        base_url="https://site.fr/", source="test",
    ) == []


def test_malformed_state_does_not_raise():
    """Une page tronquee en cours de transfert ne doit pas arreter un scan."""
    html = '<script id="__NEXT_DATA__" type="application/json">{"props": {"ads": [{</script>'
    assert extract_listings_from_state(html, base_url="https://site.fr/", source="test") == []


# --- branchement dans la chaine existante ---------------------------------


def test_the_search_extractor_falls_through_to_the_embedded_state(leboncoin_html):
    """`extract_listings_from_search` sans ItemList doit descendre d'un cran."""
    rows = extract_listings_from_search(
        leboncoin_html, base_url=LBC_URL, source="leboncoin"
    )
    assert len(rows) == 6
    assert all(row.price and row.km for row in rows)


def test_a_thin_schema_org_node_is_completed_by_the_embedded_state():
    """Le cas courant: un `Car` avec un prix, et le kilometrage ailleurs.

    La page d'annonce fait foi (invariant 14); encore faut-il lire tout ce
    qu'elle publie, pas seulement ce qu'elle publie pour Google.
    """
    url = "https://www.lacentrale.fr/auto-occasion-annonce-69123456789.html"
    jsonld = {
        "@context": "https://schema.org", "@type": "Car",
        "name": "RENAULT Clio V 1.0 TCe 90 Intens",
        "offers": {"@type": "Offer", "price": "18990", "priceCurrency": "EUR"},
    }
    state = {"props": {"pageProps": {"classified": {
        "classifiedId": "69123456789",
        "url": "/auto-occasion-annonce-69123456789.html",
        "customerPrice": 18990,
        "vehicle": {"make": "RENAULT", "model": "Clio", "mileage": 45000,
                    "firstRegistrationDate": "2021-06-15", "energy": "Essence",
                    "power": {"din": 90, "fiscal": 4}},
        "seller": {"type": "pro", "name": "Garage Martin", "city": "Lyon"},
    }}}}
    html = (
        '<script type="application/ld+json">' + json.dumps(jsonld) + "</script>"
        '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(state) + "</script>"
    )

    listing = extract_from_page(html, url=url, source="lacentrale")

    assert listing.price == 18990          # deja connu, non ecrase
    assert listing.title == "RENAULT Clio V 1.0 TCe 90 Intens"
    assert listing.km == 45000             # comble par l'etat embarque
    assert listing.year == 2021
    assert listing.city == "Lyon"
    assert listing.seller_type is SellerType.PRO


def test_the_advert_page_wins_over_its_similar_cars_rail():
    """Une page d'annonce porte aussi les annonces du bandeau "vehicules
    similaires". C'est celle dont l'adresse correspond qui est la bonne."""
    url = "https://www.leboncoin.fr/ad/voitures/2456789012"
    state = {"ads": [
        {"list_id": 9999999999, "url": "/ad/voitures/9999999999", "subject": "Autre voiture",
         "price": [4000], "attributes": [{"key": "mileage", "value": "250000"},
                                         {"key": "regdate", "value": "2008"}]},
        {"list_id": 2456789012, "url": "/ad/voitures/2456789012", "subject": "Peugeot 308",
         "price": [12500], "attributes": [{"key": "mileage", "value": "84000"},
                                          {"key": "regdate", "value": "2019"}]},
    ]}
    html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(state)}</script>'

    listing = extract_listing_from_state(html, url=url, source="leboncoin")

    assert listing.source_id == "2456789012"
    assert listing.price == 12500


# --- le palier navigateur -------------------------------------------------


def test_a_page_that_already_holds_adverts_does_not_need_a_browser(leboncoin_html):
    """Le test d'escalade. S'il se trompe, on paie un navigateur pour rien."""
    assert page_carries_adverts(leboncoin_html, url=LBC_URL) is True


def test_an_empty_shell_needs_a_browser():
    shell = "<html><head><title>Annonces</title></head><body><div id='app'></div>" + (
        "<script>window.config={api:'/v1'}</script>" * 40
    ) + "</body></html>"
    assert page_carries_adverts(shell) is False


def test_a_challenge_page_is_recognised_rather_than_fought():
    html = (
        "<html><head><title>leboncoin.fr</title></head><body>"
        "<script src='https://geo.captcha-delivery.com/captcha/?initialCid=AHrl'></script>"
        "</body></html>"
    )
    assert "captcha-delivery" in detect_challenge(html, 403)
    # Un refus reste un refus meme sans marqueur reconnu.
    assert detect_challenge("<html><body>Forbidden</body></html>", 403) == "HTTP 403"
    assert detect_challenge("<html><body>Une vraie page</body></html>", 200) is None


def test_a_real_page_carrying_the_word_datadome_is_not_a_challenge(leboncoin_html):
    """Une page de resultats complete cite parfois son prestataire anti-bot.

    La longueur tranche: une page de defi est courte, une page pleine ne
    l'est pas. Sans cette borne, la source la plus riche se coupe d'elle-meme.
    """
    assert len(leboncoin_html) > 15_000  # une page de defi ne fait pas cette taille
    assert detect_challenge(leboncoin_html + "<!-- datadome -->", 200) is None


class _StubBrowser(BrowserFetcher):
    """Un BrowserFetcher dont le rendu est un double: pas de Playwright ici.

    Invariant 15: un chemin conditionne par une dependance externe doit avoir
    son test avec un double, sinon il casse sans que rien ne le dise.
    """

    def __init__(self, plain: str, rendered: str, **kwargs):
        super().__init__(**kwargs)
        self._plain = plain
        self._rendered = rendered
        self.rendered_urls: list[str] = []

    def _plain_transport(self, url: str) -> FetchResult:
        return FetchResult(url=url, status=200, text=self._plain)

    def _render(self, url: str) -> FetchResult:
        self.rendered_urls.append(url)
        self.renders += 1
        return FetchResult(url=url, status=200, text=self._rendered, rendered=True)


def test_the_browser_stays_closed_when_plain_http_suffices(leboncoin_html, monkeypatch):
    monkeypatch.setattr(PoliteFetcher, "_transport", _StubBrowser._plain_transport)
    fetcher = _StubBrowser(leboncoin_html, "<html>rendu</html>",
                           respect_robots=False, delay=0)

    result = fetcher.get(LBC_URL, use_cache=False)

    assert fetcher.renders == 0
    assert result.rendered is False
    assert len(extract_listings_from_state(
        result.text, base_url=LBC_URL, source="leboncoin")) == 6


def test_the_browser_starts_only_when_the_page_comes_back_empty(
    leboncoin_html, monkeypatch, tmp_path
):
    monkeypatch.setenv("CAREXPERT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(PoliteFetcher, "_transport", _StubBrowser._plain_transport)
    fetcher = _StubBrowser("<html><body><div id='app'></div></body></html>",
                           leboncoin_html, respect_robots=False, delay=0)

    result = fetcher.get(LBC_URL, use_cache=False)

    assert fetcher.renders == 1
    assert result.rendered is True
    assert len(extract_listings_from_state(
        result.text, base_url=LBC_URL, source="leboncoin")) == 6


def test_the_yaml_decides_which_fetcher_a_source_gets():
    plain = fetcher_for({"name": "ouestfrance-auto", "requires_js": False})
    rendered = fetcher_for({"name": "leboncoin", "requires_js": True,
                            "render": {"wait_ms": 4000}})
    forced_off = fetcher_for({"name": "leboncoin", "requires_js": True}, browser=False)

    assert type(plain) is PoliteFetcher
    assert isinstance(rendered, BrowserFetcher)
    assert rendered.wait_ms == 4000
    assert rendered.escalate is True
    assert type(forced_off) is PoliteFetcher

    for fetcher in (plain, rendered, forced_off):
        fetcher.close()


# --- le chemin complet, contre un vrai serveur ----------------------------


def test_the_real_leboncoin_config_reads_adverts_without_a_browser(tmp_path, monkeypatch):
    """Le parcours entier, avec la configuration du depot et une vraie requete.

    Invariant 15: la passe profonde avait casse parce qu'aucun test n'allait
    jusqu'au bout du chemin. Celui-ci prend le YAML leboncoin tel qu'il est
    livre, `requires_js: true` compris, et verifie qu'une page servie sous la
    forme du site rend ses annonces sans qu'un navigateur demarre.
    """
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlparse

    from carexpert.sources.configured import ConfiguredSource, load_site_configs

    page_one = (FIXTURES / "leboncoin_search.html").read_bytes()
    empty = b"<html><body><div id='app'></div></body></html>"
    served: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            served.append(self.path)
            if parsed.path == "/robots.txt":
                body = b"User-agent: *\nAllow: /\n"
            else:
                number = int(parse_qs(parsed.query).get("page", ["1"])[0])
                body = page_one if number == 1 else empty
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # pragma: no cover - silence
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host = f"http://127.0.0.1:{server.server_address[1]}"

    monkeypatch.setenv("CAREXPERT_CACHE_DIR", str(tmp_path / "cache"))
    from carexpert import config

    config.get_settings.cache_clear()
    try:
        site = dict(load_site_configs()["leboncoin"])
        assert site["requires_js"] is True  # le navigateur est autorise...
        site["request_delay"] = 0.01
        source = ConfiguredSource(site)

        listings = list(source.search_url(f"{host}/recherche?category=2", limit=50))

        assert len(listings) == 6
        assert source._fetcher.renders == 0  # ...et pourtant jamais demarre
        assert {row.make for row in listings} >= {"Peugeot", "Renault", "Volkswagen"}
        assert all(row.price and row.km and row.year for row in listings)
        # La pagination s'arrete des que la page n'apporte rien de nouveau.
        assert sum(1 for path in served if path.startswith("/recherche")) == 2
        source.close()
    finally:
        server.shutdown()
        config.get_settings.cache_clear()


def test_the_diagnostic_says_which_tier_answered(leboncoin_html):
    """Une source qui se tarit ne se repare que si on sait par ou elle lisait.

    "6 annonces via schema.org" et "6 annonces via etat embarque" demandent
    deux corrections differentes le jour ou le compte tombe a zero.
    """
    from carexpert.sources.diagnose import diagnose_search

    class _Fetcher(PoliteFetcher):
        def _transport(self, url: str) -> FetchResult:
            return FetchResult(url=url, status=200, text=leboncoin_html)

    fetcher = _Fetcher(respect_robots=False, delay=0)
    report = diagnose_search(
        "https://www.leboncoin.fr/recherche?category=2",
        source="leboncoin", pattern=r"/ad/voitures/\d+", fetcher=fetcher,
    )

    assert report.verdict()[0] == "liste"
    assert report.extraction_tier == "etat embarque"
    assert report.rendered is False
    assert report.results_listings == 6
    assert report.results_complete == 6
    assert report.links_found == 0  # et pourtant six annonces
    assert any("embedded.py" in action for action in report.actions())


def test_a_protected_site_produces_a_verdict_not_a_traceback():
    """Un diagnostic dont le seul travail est de dire quoi faire ne plante pas.

    Sans ce chemin, `carexpert diagnose -s leboncoin --browser` repondait par
    une trace Python le jour ou le site repond par un defi, c'est-a-dire
    exactement le jour ou l'utilisateur a besoin d'une reponse lisible.
    """
    from carexpert.sources.browser import BotProtection
    from carexpert.sources.diagnose import diagnose_search

    class _Challenged(PoliteFetcher):
        def get(self, url: str, *, use_cache: bool = True) -> FetchResult:
            raise BotProtection(
                f"{url}: le site repond par une protection anti-bot (datadome). "
                "La collecte s'arrete la, volontairement.",
                protection="datadome",
            )

    report = diagnose_search(
        "https://www.leboncoin.fr/recherche?category=2",
        source="leboncoin",
        fetcher=_Challenged(respect_robots=False, delay=0),
    )

    level, phrase = report.verdict()
    # Un refus leve pendant le rendu reste un refus, pas une panne.
    assert level == "bloque"
    assert "datadome" in phrase
    assert report.protection == "datadome"
    actions = report.actions()
    assert any("Ne pas insister" in action for action in actions)
    assert any("leparking" in action for action in actions)
    assert not any("verified: true" in a for a in actions if a.startswith("Passer"))


def test_a_failed_diagnosis_never_declares_the_source_usable():
    """Le fourre-tout de `actions()` avalait le niveau "echec".

    Mesure sur une vraie sortie: "SITE INJOIGNABLE" suivi de "Source
    exploitable. Passer `verified: true`". Marquer verifiee une source dont
    pas une annonce n'est sortie, c'est ce que le drapeau existe pour
    empecher.
    """
    from carexpert.sources.diagnose import DiagnosticReport

    unreachable = DiagnosticReport(url="https://site.fr/x", source="leboncoin",
                                   error="echec de recuperation: 403 Forbidden")
    refused = DiagnosticReport(url="https://site.fr/x", source="leboncoin", status=403)

    for report in (unreachable, refused):
        actions = report.actions()
        # Injoignable et refuse sont deux echecs distincts; aucun des deux
        # n'autorise a declarer la source exploitable.
        assert report.verdict()[0] in ("echec", "bloque")
        # La phrase du fourre-tout, mot pour mot: c'est elle qui sortait.
        assert not any("Source exploitable" in action for action in actions), actions
        assert not any(
            action.startswith("Source exploitable") or action.startswith("Passer `verified")
            for action in actions
        ), actions

    # Un refus se nomme pour ce qu'il est, sans accuser le site a tort: un
    # proxy sortant repond 403 a sa place plus souvent qu'on ne croit.
    assert any("protection" in a for a in refused.actions())
    assert any("proxy" in a for a in refused.actions())
    # Un site qui ne repond pas du tout n'est pas un site qui refuse, et le
    # diagnostic le dit au lieu de conseiller de marquer la source verifiee.
    assert any("pas repondu" in a for a in unreachable.actions())
    assert any("Ne pas passer `verified: true`" in a for a in unreachable.actions())


def test_an_install_hint_survives_the_terminal(capsys):
    """`pip install -e ".[browser]"` affiche `pip install -e "."`.

    Rich lit `[browser]` comme une balise de style et l'efface. Le message
    qui sort du terminal est alors une commande fausse, qui s'installe sans
    erreur et sans navigateur. Tout texte venant d'une exception doit etre
    echappe avant d'etre rendu.
    """
    from rich.console import Console
    from rich.markup import escape
    from rich.panel import Panel

    from carexpert.sources.browser import INSTALL_HINT

    console = Console(width=100)
    console.print(Panel(escape(INSTALL_HINT)))
    assert '".[browser]"' in capsys.readouterr().out


def test_a_missing_browser_does_not_throw_away_the_page_already_fetched(leboncoin_html):
    """La requete simple est deja faite, et le site l'a deja payee.

    La jeter parce que Playwright manque faisait dire au diagnostic "le site
    n'a pas repondu - HTTP 0, 0 octets" alors que le site avait repondu. Le
    fetcher rend ce qu'il a, et dit pourquoi il n'est pas alle plus loin.
    """
    from carexpert.sources.browser import BrowserFetcher, BrowserUnavailable

    shell = "<html><body><div id='app'></div></body></html>" + "<!-- x -->" * 100

    class _NoPlaywright(BrowserFetcher):
        def _plain(self, url: str) -> FetchResult:
            return FetchResult(url=url, status=200, text=shell)

        def _render(self, url: str) -> FetchResult:
            raise BrowserUnavailable('pip install -e ".[browser]"')

    fetcher = _NoPlaywright(respect_robots=False, delay=0)
    original = PoliteFetcher._transport
    PoliteFetcher._transport = _NoPlaywright._plain  # type: ignore[method-assign]
    try:
        result = fetcher.get("https://site.fr/recherche", use_cache=False)
    finally:
        PoliteFetcher._transport = original  # type: ignore[method-assign]

    assert result.status == 200
    assert result.text == shell
    assert result.rendered is False
    assert "browser" in fetcher.escalation_blocked


def test_a_refusal_is_named_not_reported_as_a_breakdown():
    """Mesure sur leboncoin: HTTP 403, 1004 octets, en 0,1 seconde.

    "SITE INJOIGNABLE - le site repond HTTP 403" se lit comme une panne, et
    une panne, on la relance. Un refus, non: c'est une decision du site, et
    la seule suite correcte est de ne pas insister. Les deux ne se corrigent
    pas pareil, donc ils ne s'affichent pas pareil.
    """
    from carexpert.sources.diagnose import diagnose_search

    interstitial = (
        "<html><head><title>leboncoin.fr</title></head><body>"
        "<script src='https://geo.captcha-delivery.com/captcha/?initialCid=AHrl'></script>"
        "</body></html>"
    )

    class _Refusing(PoliteFetcher):
        def _transport(self, url: str) -> FetchResult:
            return FetchResult(url=url, status=403, text=interstitial)

    report = diagnose_search(
        "https://www.leboncoin.fr/recherche?category=2",
        source="leboncoin", fetcher=_Refusing(respect_robots=False, delay=0),
    )

    level, phrase = report.verdict()
    assert level == "bloque"
    assert "captcha-delivery" in report.protection
    assert "refuse la requete" in phrase
    assert "captcha-delivery" in phrase          # la protection est nommee
    assert str(report.page_bytes) in phrase      # la taille dit l'interstitiel
    assert any("Ne pas insister" in action for action in report.actions())
    assert not any("verified: true" in a for a in report.actions() if a.startswith("Passer"))


def test_a_plain_error_without_a_marker_stays_a_breakdown():
    """Un 500, ou un 404 sur une URL mal recopiee, n'est pas un refus."""
    from carexpert.sources.diagnose import diagnose_search

    class _Broken(PoliteFetcher):
        def _transport(self, url: str) -> FetchResult:
            return FetchResult(url=url, status=500, text="<html>Erreur serveur</html>")

    report = diagnose_search("https://site.fr/x", source="test",
                             fetcher=_Broken(respect_robots=False, delay=0))
    assert report.verdict()[0] == "echec"
    assert report.protection == ""
    assert "500" in report.verdict()[1]
