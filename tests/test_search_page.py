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
from carexpert.schemas import Fuel, Gearbox, SellerType
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
    # Une seule requete: c'est tout l'interet, pour le site comme pour nous.
    assert fetcher.calls == [SEARCH_URL]
    assert all(row.source == "autoscout24" for row in collected)
    assert all(row.country == "FR" for row in collected)


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
    assert fetcher.calls[1:] == ["https://site.fr/ad/1", "https://site.fr/ad/2"]


# --- Sources dont la recherche reste dans le navigateur ---------------------


def test_an_empty_link_pattern_does_not_collect_every_link():
    """Un motif vide est une regex qui matche tout: `/aide` devient une annonce."""
    from carexpert.sources.configured import DEFAULT_LINK_PATTERN, link_pattern

    assert link_pattern({"listing_link_pattern": ""}) == DEFAULT_LINK_PATTERN
    assert link_pattern({}) == DEFAULT_LINK_PATTERN
    assert link_pattern({"listing_link_pattern": r"/ad/\d+"}) == r"/ad/\d+"


def test_a_pasted_hashbang_url_is_stripped_and_flagged(caplog):
    """Sinon le scan parcourt la page d'accueil et annonce "0 annonce"."""
    import logging

    fetcher = OnePageFetcher("<html><body>accueil</body></html>")
    source = ConfiguredSource({"name": "theparking"}, fetcher=fetcher)

    with caplog.at_level(logging.WARNING):
        list(source.search_url("https://www.theparking.eu/#!/used-cars/V70.html"))

    assert fetcher.calls == ["https://www.theparking.eu/"]
    assert "`#`" in caplog.text and "/used-cars/V70.html" in caplog.text


def test_theparking_declares_no_unverified_search_template():
    """Un gabarit hashbang rejouerait la page d'accueil a chaque page."""
    config = load_site_configs()["theparking"]

    assert config["verified"] is False
    assert config.get("search_url") is None
    assert config["requires_js"] is True


# Annonce reelle du site, relevee a la main. Le reste du fichier en depend:
# elle est la seule preuve que les pages d'annonce sont servies par le
# serveur, la recherche restant derriere son `#!`.
THEPARKING_AD = (
    "https://www.theparking.eu/used-cars-detail/volvo-v70-d4"
    "/volvo-v70-2-0-d4-summum-leer-nette-auto-goed-onderhouden-apk"
    "/4V8NK9AT.html"
)


def test_the_generic_pattern_finds_nothing_on_theparking():
    r"""Pourquoi ce site a besoin de son propre motif.

    L'identifiant est `4V8NK9AT`, pas un nombre: le defaut `/\d{5,}` ne
    trouve aucune annonce sur une page qui en est pleine, et le dit sans
    erreur.
    """
    from carexpert.sources.configured import DEFAULT_LINK_PATTERN

    html = f'<html><body><a href="{THEPARKING_AD}">annonce</a></body></html>'
    assert extract_listing_links(html, "https://www.theparking.eu/",
                                 DEFAULT_LINK_PATTERN) == []


def test_theparking_pattern_keeps_adverts_and_drops_the_rest():
    from carexpert.sources.configured import link_pattern

    pattern = link_pattern(load_site_configs()["theparking"])
    html = f"""<html><body>
      <a href="{THEPARKING_AD}">annonce</a>
      <a href="/used-cars/V70.html">recherche</a>
      <a href="/used-cars-detail/volvo-v70-d4/">categorie du modele</a>
      <a href="/used-cars-detail/volvo-v70-d4/volvo-v70/index.html">index</a>
      <a href="/fr/aide.html">aide</a>
    </body></html>"""

    assert extract_listing_links(html, "https://www.theparking.eu/", pattern) == [
        THEPARKING_AD
    ]


THEPARKING_AD_PAGE = """<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@type":"Car",
 "name":"Volvo V70 2.0 D4 Summum","brand":{"@type":"Brand","name":"Volvo"},
 "model":"V70","vehicleModelDate":"2014","fuelType":"diesel",
 "mileageFromOdometer":{"@type":"QuantitativeValue","value":"196000","unitCode":"KMT"},
 "offers":{"@type":"Offer","price":"11950","priceCurrency":"EUR"}}
</script></head><body>annonce</body></html>"""


def test_a_theparking_advert_comes_out_with_the_site_own_id():
    """Bout en bout sur la forme d'URL reelle, avec la config du YAML.

    Le balisage, lui, est le notre: le site n'a pas pu etre joint. Ce test
    verifie donc notre moitie du contrat (URL, identifiant, extraction), pas
    ce que TheParking publie.
    """
    fetcher = OnePageFetcher(THEPARKING_AD_PAGE)
    source = ConfiguredSource(load_site_configs()["theparking"], fetcher=fetcher)

    listing = source.fetch_listing(THEPARKING_AD)

    assert listing is not None
    assert listing.source == "theparking"
    assert listing.source_id == "4V8NK9AT"      # ni le slug du modele, ni `.html`
    assert listing.price_eur == 11950
    assert listing.km == 196000
