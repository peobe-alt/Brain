"""Lire une page de resultats sans ouvrir les annonces.

Le fichier de reference, `fixtures/autoscout24_search.html`, est un extrait
reel d'AutoScout24. Il documente un piege qui ne se voit pas dans les logs:
les liens des annonces n'existent pas dans le HTML, le site les ajoute en
JavaScript. Un collecteur qui suit les liens renvoie zero annonce sur une
page qui en affiche quatorze, sans lever la moindre erreur.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from carexpert.normalize import enrich
from carexpert.schemas import BodyType, Fuel, Gearbox, SellerType
from carexpert.sources.configured import ConfiguredSource, load_site_configs
from carexpert.sources.fetcher import FetchResult
from carexpert.sources.structured import (
    extract_listing_links,
    extract_listings_from_search,
    find_item_list,
    extract_jsonld,
)

FIXTURE = Path(__file__).parent / "fixtures" / "autoscout24_search.html"
SEARCH_URL = "https://www.autoscout24.fr/lst/volvo/v70?atype=C&cy=F&sort=standard"
AS24_PATTERN = (
    r"/offres/[^/?#]+-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)


@pytest.fixture(scope="module")
def search_html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def rows(search_html: str):
    return extract_listings_from_search(
        search_html, base_url=SEARCH_URL, source="autoscout24"
    )


# --- le piege -------------------------------------------------------------


def test_the_advert_links_are_absent_from_the_html(search_html):
    """La raison d'etre de tout ce module: zero lien, quatorze annonces."""
    assert extract_listing_links(search_html, SEARCH_URL, AS24_PATTERN) == []


def test_the_page_still_carries_every_advert(search_html):
    assert len(rows(search_html)) == 3
    item_list = find_item_list(extract_jsonld(search_html))
    assert item_list["numberOfItems"] == 14  # la page complete en portait 14


# --- ce qu'on en tire -----------------------------------------------------


def test_each_advert_keeps_its_own_identity(search_html):
    ids = [row.source_id for row in rows(search_html)]
    assert ids == [
        "58e07244-1f17-499f-aa31-325a482ea607",
        "7cf2f649-3e12-4b15-adf5-c446a15c7e9f",
        "1920dd58-65cb-4eaa-ac78-c0640a0f14ac",
    ]
    assert len(set(ids)) == 3


def test_advert_urls_are_absolute_and_free_of_tracking(search_html):
    for row in rows(search_html):
        assert row.url.startswith("https://www.autoscout24.fr/offres/")
        assert "sort=" not in row.url and "atype=" not in row.url


def test_the_first_advert_is_read_in_full(search_html):
    row = enrich(rows(search_html)[0])
    assert row.price_eur == 10100
    assert row.km == 99350
    assert row.make == "Volvo" and row.model == "V70"
    assert row.gearbox is Gearbox.MANUAL
    assert row.seller_type is SellerType.PRO
    assert row.seller_name == "MAVINO"
    assert row.city == "MONTAUBAN"
    assert len(row.photos) == 2


def test_a_flexfuel_car_is_not_filed_as_lpg(search_html):
    """GPL et superethanol E85 ne sont ni le meme carburant ni la meme cote."""
    row = enrich(rows(search_html)[0])
    assert row.fuel is Fuel.ETHANOL


def test_the_card_supplies_what_the_json_ld_omits(search_html):
    """Le JSON-LD ne dit ni l'annee ni le code postal; la carte, si."""
    first = enrich(rows(search_html)[0])
    assert first.year == 2009
    assert first.first_registration.month == 1
    assert first.postcode == "82000"


def test_the_card_also_rescues_an_advert_without_price(search_html):
    """Troisieme annonce: offre vide dans le JSON-LD, tout dans la carte."""
    third = enrich(rows(search_html)[2])
    assert third.price_eur == 7250
    assert third.km == 212400
    assert third.year == 2008
    assert third.postcode == "33000"


def test_a_private_seller_is_not_taken_for_a_dealer(search_html):
    second = enrich(rows(search_html)[1])
    assert second.seller_type is SellerType.PRIVATE
    assert second.gearbox is Gearbox.AUTOMATIC
    assert second.fuel is Fuel.DIESEL


# --- l'adaptateur ---------------------------------------------------------


class OnePageFetcher:
    """Sert la page de resultats et compte les requetes."""

    def __init__(self, html: str) -> None:
        self.html = html
        self.calls: list[str] = []

    def get(self, url: str, use_cache: bool = True) -> FetchResult:
        self.calls.append(url)
        return FetchResult(url=url, status=200, text=self.html)

    def close(self) -> None:
        return None


def test_the_adapter_reads_the_page_instead_of_opening_fourteen_adverts(search_html):
    fetcher = OnePageFetcher(search_html)
    source = ConfiguredSource(load_site_configs()["autoscout24"], fetcher=fetcher)

    collected = list(source.search_url(SEARCH_URL, limit=50))

    assert len(collected) == 3
    # Trois annonces, deux requetes: la page de resultats, puis la page 2 qui
    # renvoie les memes annonces et arrete la. Aucune annonce n'est ouverte.
    assert fetcher.calls == [SEARCH_URL, SEARCH_URL + "&page=2"]
    assert all(row.source == "autoscout24" for row in collected)
    assert all(row.country == "FR" for row in collected)


def test_the_pasted_url_keeps_the_filters_the_user_chose(search_html):
    """`sort` et `atype` sont des filtres du site, pas du tracage."""
    fetcher = OnePageFetcher(search_html)
    source = ConfiguredSource(load_site_configs()["autoscout24"], fetcher=fetcher)
    list(source.search_url(SEARCH_URL, limit=50))

    for call in fetcher.calls:
        assert "atype=C" in call and "sort=standard" in call and "cy=F" in call


def test_the_adapter_still_honours_the_limit(search_html):
    fetcher = OnePageFetcher(search_html)
    source = ConfiguredSource(load_site_configs()["autoscout24"], fetcher=fetcher)
    assert len(list(source.search_url(SEARCH_URL, limit=2))) == 2


THIN_LIST = """<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@type":"ItemList","itemListElement":[
 {"@type":"ListItem","url":"/ad/1","item":{"@type":"Car","name":"Golf VII"}},
 {"@type":"ListItem","url":"/ad/2","item":{"@type":"Car","name":"Golf VIII"}}]}
</script></head><body>
<a href="/ad/1">1</a><a href="/ad/2">2</a></body></html>"""

DETAIL = """<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@type":"Car","name":"Volkswagen Golf 1.6 TDI",
 "brand":{"@type":"Brand","name":"Volkswagen"},"model":"Golf","vehicleModelDate":"2018",
 "mileageFromOdometer":{"@type":"QuantitativeValue","value":"90000","unitCode":"KMT"},
 "offers":{"@type":"Offer","price":"14500","priceCurrency":"EUR"}}
</script></head><body>annonce</body></html>"""


class TwoPageFetcher:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(self, url: str, use_cache: bool = True) -> FetchResult:
        self.calls.append(url)
        html = DETAIL if "/ad/" in url else THIN_LIST
        return FetchResult(url=url, status=200, text=html)

    def close(self) -> None:
        return None


def test_a_list_without_prices_falls_back_to_opening_the_adverts():
    """La voie liste n'est un gain que si la liste porte de quoi valoriser."""
    fetcher = TwoPageFetcher()
    source = ConfiguredSource(
        {"name": "test", "listing_link_pattern": r"/ad/\d+"}, fetcher=fetcher
    )

    collected = list(source.search_url("https://site.fr/recherche"))

    assert len(collected) == 2
    assert all(row.price_eur == 14500 for row in collected)
    # Chaque annonce n'est ouverte qu'une fois, meme si la page 2 la reliste.
    assert fetcher.calls == [
        "https://site.fr/recherche",
        "https://site.fr/ad/1",
        "https://site.fr/ad/2",
        "https://site.fr/recherche?page=2",
    ]


# --- la page Volkswagen Golf (1 849 annonces, 93 pages) -------------------

GOLF_FIXTURE = Path(__file__).parent / "fixtures" / "autoscout24_search_golf.html"
GOLF_URL = "https://www.autoscout24.fr/lst/volkswagen/golf-tous?cy=F&sort=standard"


@pytest.fixture(scope="module")
def golf_html() -> str:
    return GOLF_FIXTURE.read_text(encoding="utf-8")


def golf_rows(golf_html: str):
    rows = extract_listings_from_search(
        golf_html, base_url=GOLF_URL, source="autoscout24"
    )
    return {row.source_id[:8]: enrich(row) for row in rows}


def test_the_item_list_survives_a_graph_wrapper(golf_html):
    """Cette page-la enveloppe son JSON-LD dans un @graph, pas l'autre."""
    assert extract_listing_links(golf_html, GOLF_URL, AS24_PATTERN) == []
    item_list = find_item_list(extract_jsonld(golf_html))
    assert item_list["numberOfItems"] == 20
    assert len(golf_rows(golf_html)) == 6


def test_every_advert_gets_its_power(golf_html):
    """Sans puissance, une Golf R de 320 ch se compare a une 1.2 TSI.

    La puissance n'est dans aucun attribut: elle est ecrite dans la carte,
    sous la forme `235 kW (320 Ch)`. Trois des six annonces ne la donnent
    nulle part ailleurs.
    """
    rows = golf_rows(golf_html)
    assert [rows[k].power_hp for k in ("953c73d6", "a4399361", "d7aaa9ee")] == [320, 117, 151]
    assert all(row.power_hp for row in rows.values())


def test_a_price_cut_is_seen_on_the_first_visit(golf_html):
    """L'annonce affiche son ancien prix: inutile d'attendre demain."""
    rows = golf_rows(golf_html)
    assert rows["1969de9f"].extra["previous_price"] == 7490
    assert rows["1969de9f"].price_eur == 6990
    assert rows["d7aaa9ee"].extra["previous_price"] == 16990
    assert "previous_price" not in rows["953c73d6"].extra


def test_a_plug_in_hybrid_is_not_an_electric_car(golf_html):
    """AutoScout24 dit "Electrique/Essence": c'est une GTE, pas une e-Golf."""
    rows = golf_rows(golf_html)
    assert rows["d7aaa9ee"].fuel is Fuel.PHEV
    assert rows["a4399361"].fuel is Fuel.ELECTRIC


def test_the_json_ld_wins_over_the_site_s_own_mistake(golf_html):
    """Annonce 11: le site classe une Golf 1.6 TDI en version "E-Golf".

    Son moteur declare est "E 85 kW", sa puissance est absente et sa carte
    ne montre que "-/-". Seuls le carburant du schema.org et le titre disent
    la verite; c'est eux qu'on suit.
    """
    row = golf_rows(golf_html)["307ec02b"]
    assert row.fuel is Fuel.DIESEL
    assert row.power_hp == 110       # lu dans le titre, faute de mieux
    assert row.km == 255000


def test_an_estate_is_recognised_from_the_model_name(golf_html):
    row = golf_rows(golf_html)["acf5c622"]
    assert row.body is BodyType.ESTATE
    assert row.postcode == "69330"


def test_the_advertised_cut_seeds_the_price_history(session, golf_html):
    """Le score doit voir la baisse des le premier scan, pas au second."""
    from carexpert.db import Listing, Pricepoint
    from carexpert.pipeline.ingest import ingest

    rows = extract_listings_from_search(
        golf_html, base_url=GOLF_URL, source="autoscout24"
    )
    ingest(session, rows)
    session.flush()

    row = session.query(Listing).filter(Listing.source_id.like("1969de9f%")).one()
    prices = [
        p.price_eur
        for p in session.query(Pricepoint)
        .filter(Pricepoint.listing_id == row.id)
        .order_by(Pricepoint.seen_at)
        .all()
    ]
    assert prices == [7490, 6990]

    quiet = session.query(Listing).filter(Listing.source_id.like("953c73d6%")).one()
    assert session.query(Pricepoint).filter(Pricepoint.listing_id == quiet.id).count() == 1


# --- pagination -----------------------------------------------------------

PAGE_TEMPLATE = """<html><head><script type="application/ld+json">
{{"@context":"https://schema.org","@type":"SearchResultsPage","mainEntity":{{
 "@type":"ItemList","numberOfItems":40,"itemListElement":[{items}]}}}}
</script></head><body>{cards}</body></html>"""

ITEM_TEMPLATE = """{{"@type":"ListItem","url":"/offres/golf-cat_ma74mo2084-{uuid}",
 "item":{{"@type":["Car","Product"],"name":"Volkswagen Golf 1.6 TDI 110",
  "brand":{{"@type":"Brand","name":"Volkswagen"}},"model":"Golf",
  "mileageFromOdometer":{{"@type":"QuantitativeValue","value":{km},"unitCode":"KMT"}},
  "fuelType":"Diesel","vehicleTransmission":"Boite manuelle",
  "offers":{{"@type":"Offer","price":{price},"priceCurrency":"EUR"}}}}}}"""

CARD_TEMPLATE = (
    '<article id="{uuid}" data-price="{price}" data-mileage="{km}" '
    'data-first-registration="01-2016" data-listing-zip-code="69003"></article>'
)


def _page(index: int, size: int = 20) -> str:
    items, cards = [], []
    for n in range(size):
        rank = index * size + n
        uuid = "%08x-0000-4000-8000-000000000000" % rank
        items.append(ITEM_TEMPLATE.format(uuid=uuid, km=100000 + rank, price=9000 + rank))
        cards.append(CARD_TEMPLATE.format(uuid=uuid, km=100000 + rank, price=9000 + rank))
    return PAGE_TEMPLATE.format(items=",".join(items), cards="".join(cards))


class PagedFetcher:
    """Serves distinct pages, the way a real results page paginates."""

    def __init__(self, pages: int) -> None:
        self.pages = pages
        self.calls: list[str] = []

    def get(self, url: str, use_cache: bool = True) -> FetchResult:
        self.calls.append(url)
        import re as _re

        match = _re.search(r"[?&]page=(\d+)", url)
        number = int(match.group(1)) if match else 1
        if number > self.pages:
            return FetchResult(url=url, status=200, text=_page(0, size=0))
        return FetchResult(url=url, status=200, text=_page(number - 1))


def test_a_pasted_url_is_followed_beyond_its_first_page():
    """Une recherche colle une page; le site en a quatre-vingt-treize.

    Sans pagination, coller l'URL d'une recherche Volkswagen Golf ramene
    20 annonces sur 1 849, et l'outil croit avoir tout vu.
    """
    fetcher = PagedFetcher(pages=5)
    source = ConfiguredSource(
        {"name": "test", "max_pages": 3, "page_param": "page"}, fetcher=fetcher
    )

    collected = list(source.search_url("https://site.fr/lst/volkswagen/golf-tous?cy=F"))

    assert len(collected) == 60                       # 3 pages plafonnees par max_pages
    assert len({row.source_id for row in collected}) == 60
    assert fetcher.calls == [
        "https://site.fr/lst/volkswagen/golf-tous?cy=F",
        "https://site.fr/lst/volkswagen/golf-tous?cy=F&page=2",
        "https://site.fr/lst/volkswagen/golf-tous?cy=F&page=3",
    ]


def test_pagination_stops_as_soon_as_a_page_brings_nothing():
    fetcher = PagedFetcher(pages=2)
    source = ConfiguredSource(
        {"name": "test", "max_pages": 10, "page_param": "page"}, fetcher=fetcher
    )

    collected = list(source.search_url("https://site.fr/recherche"))

    assert len(collected) == 40
    # Deux pages pleines, une troisieme vide qui met fin au parcours.
    assert len(fetcher.calls) == 3


def test_a_url_already_on_page_two_continues_from_there():
    """L'utilisateur peut coller la page 2: on repart de la, pas du debut."""
    fetcher = PagedFetcher(pages=9)
    source = ConfiguredSource(
        {"name": "test", "max_pages": 2, "page_param": "page"}, fetcher=fetcher
    )

    list(source.search_url("https://site.fr/recherche?page=4"))

    assert fetcher.calls == [
        "https://site.fr/recherche?page=4",
        "https://site.fr/recherche?page=5",
    ]


def test_the_limit_is_honoured_across_pages():
    fetcher = PagedFetcher(pages=5)
    source = ConfiguredSource(
        {"name": "test", "max_pages": 5, "page_param": "page"}, fetcher=fetcher
    )

    collected = list(source.search_url("https://site.fr/recherche", limit=25))

    assert len(collected) == 25
    assert len(fetcher.calls) == 2


# --- contre un vrai serveur HTTP ------------------------------------------


def test_the_whole_crawl_works_against_a_real_server(tmp_path, monkeypatch):
    """Le parcours complet, robots.txt et rythme poli compris.

    Les doubles ci-dessus verifient la logique; celui-ci verifie qu'elle
    tient quand il y a vraiment une requete HTTP au bout.
    """
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlparse

    served: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            served.append(self.path)
            if parsed.path == "/robots.txt":
                body = b"User-agent: *\nAllow: /\n"
            else:
                number = int(parse_qs(parsed.query).get("page", ["1"])[0])
                body = (_page(number - 1) if number <= 4 else _page(0, size=0)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # pragma: no cover - silence
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host = f"http://127.0.0.1:{server.server_address[1]}"

    monkeypatch.setenv("CAREXPERT_CACHE_DIR", str(tmp_path / "cache"))
    from carexpert import config

    config.get_settings.cache_clear()
    try:
        source = ConfiguredSource(
            {"name": "test", "max_pages": 10, "request_delay": 0.01},
            fetcher=None,
        )
        collected = list(source.search_url(f"{host}/lst/volkswagen/golf-tous?cy=F"))
        source.close()
    finally:
        server.shutdown()
        config.get_settings.cache_clear()

    assert len(collected) == 80                         # quatre pages de vingt
    assert len({row.source_id for row in collected}) == 80
    assert all(row.price_eur and row.km and row.year for row in collected)
    assert "/robots.txt" in served                      # demande, et respecte
    assert sum(1 for path in served if "/lst/" in path) == 5
