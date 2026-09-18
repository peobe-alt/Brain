"""schema.org extraction, the backbone of source-agnostic scraping."""

from __future__ import annotations

import pytest

from carexpert.sources.structured import (
    extract_from_page,
    extract_jsonld,
    extract_listing_links,
)

PAGE = """<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Car","name":"Peugeot 308 SW BlueHDi 130",
 "brand":{"@type":"Brand","name":"Peugeot"},"model":"308 SW","vehicleModelDate":"2019",
 "dateVehicleFirstRegistered":"2019-04-15","fuelType":"diesel","vehicleTransmission":"Automatique",
 "mileageFromOdometer":{"@type":"QuantitativeValue","value":"98500","unitCode":"KMT"},
 "vehicleEngine":{"@type":"EngineSpecification","enginePower":{"value":"130","unitCode":"BHP"}},
 "color":"Gris","numberOfDoors":5,"description":"Carnet d'entretien complet.",
 "image":["/img/1.jpg","/img/2.jpg"],
 "offers":{"@type":"Offer","price":"15990","priceCurrency":"EUR",
   "seller":{"@type":"AutoDealer","name":"Garage Martin",
     "address":{"addressLocality":"Lyon","postalCode":"69003"}}}}
</script></head><body>
<a href="/annonce/voiture-peugeot-308-123456.html">annonce</a>
<a href="/aide/contact">aide</a>
</body></html>"""


def test_jsonld_is_found():
    assert len(extract_jsonld(PAGE)) == 1


def test_extraction_reads_every_field():
    listing = extract_from_page(PAGE, url="https://x.fr/annonce/a-123456.html", source="test")
    assert listing is not None
    assert listing.make == "Peugeot"
    assert listing.price_eur == 15990
    assert listing.km == 98_500
    assert listing.power_hp == 130
    assert listing.first_registration.year == 2019
    assert listing.seller_name == "Garage Martin"
    assert listing.city == "Lyon"
    assert [p.url for p in listing.photos] == [
        "https://x.fr/img/1.jpg", "https://x.fr/img/2.jpg",
    ]


def test_mileage_in_miles_is_converted():
    page = PAGE.replace('"unitCode":"KMT"', '"unitCode":"SMI"')
    listing = extract_from_page(page, url="https://x.fr/a-1.html", source="test")
    assert listing.km > 150_000


def test_listing_links_are_filtered_by_pattern():
    links = extract_listing_links(PAGE, "https://x.fr", r"/annonce/.*-\d+\.html")
    assert links == ["https://x.fr/annonce/voiture-peugeot-308-123456.html"]


def test_unparseable_page_returns_none():
    assert extract_from_page("<html><body>rien</body></html>", url="u", source="t") is None


# --- Identifiants d'annonce, sur des URL reelles ---------------------------

AUTOSCOUT_URLS = [
    ("https://www.autoscout24.fr/offres/volvo-v70-2-4-d5-edition-ii-diesel-bleu"
     "-cat_ma73mo2079-7cf2f649-3e12-4b15-adf5-c446a15c7e9f"
     "?ipc=recommendation&ipl=homepage-bestresult-listings&position=3",
     "7cf2f649-3e12-4b15-adf5-c446a15c7e9f"),
    ("https://www.autoscout24.fr/offres/audi-q3-35-tfsi-150-s-tronic-business-line"
     "-1ere-main-francaise-cuir-essence-gris-cat_ma9mo19715"
     "-1920dd58-65cb-4eaa-ac78-c0640a0f14ac?source_otp=t30&position=7",
     "1920dd58-65cb-4eaa-ac78-c0640a0f14ac"),
    ("https://www.autoscout24.fr/offres/porsche-cayman-s-allemagne-autres-noir"
     "-cat_ma57mo18684-2157f4f8-d523-4d42-9f65-d1a2ed724445?source_otp=t30&position=12",
     "2157f4f8-d523-4d42-9f65-d1a2ed724445"),
]


@pytest.mark.parametrize("url,expected", AUTOSCOUT_URLS)
def test_the_advert_id_is_the_uuid_not_the_model_id(url, expected):
    """`cat_ma73mo2079` carries the *model* id, shared by every Volvo V70.

    Reading it as the advert id makes all V70 adverts collide on the database
    unique key, so the base keeps exactly one of them - silently.
    """
    from carexpert.sources.structured import _listing_id

    assert _listing_id(url) == expected


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://suchen.mobile.de/fahrzeuge/details.html?id=428391234", "428391234"),
        ("https://www.lacentrale.fr/auto-occasion-annonce-69108123456.html", "69108123456"),
        ("https://www.leboncoin.fr/ad/voitures/2891234567", "2891234567"),
    ],
)
def test_advert_ids_from_other_european_sites(url, expected):
    """mobile.de puts its id in the query, not the path."""
    from carexpert.sources.structured import _listing_id

    assert _listing_id(url) == expected


def test_two_adverts_never_share_an_id():
    from carexpert.sources.structured import _listing_id

    ids = {_listing_id(url) for url, _ in AUTOSCOUT_URLS}
    assert len(ids) == len(AUTOSCOUT_URLS)


def test_tracking_parameters_are_stripped():
    from carexpert.sources.structured import canonical_url

    base = AUTOSCOUT_URLS[0][0]
    assert "position=" not in canonical_url(base)
    assert "ipc=" not in canonical_url(base)
    assert canonical_url(base).startswith("https://www.autoscout24.fr/offres/volvo-v70")


def test_the_same_advert_linked_three_times_is_fetched_once():
    """A results page links one car from its card, its title and its photo."""
    import yaml
    from pathlib import Path

    from carexpert.sources.structured import extract_listing_links

    config = yaml.safe_load(
        (Path("src/carexpert/sources/sites/autoscout24.yaml")).read_text(encoding="utf-8")
    )
    plain = AUTOSCOUT_URLS[0][0].split("?")[0]
    html = "<html><body>" + "".join(
        f'<a href="{plain}?position={i}&source_otp=t{i}">x</a>' for i in range(3)
    ) + f'<a href="{AUTOSCOUT_URLS[1][0]}">y</a>' \
      + '<a href="/offres/voitures-occasion">categorie</a></body></html>'

    links = extract_listing_links(html, "https://www.autoscout24.fr",
                                  config["listing_link_pattern"])
    assert len(links) == 2


def test_the_configured_pattern_matches_real_adverts():
    import yaml
    from pathlib import Path
    import re

    config = yaml.safe_load(
        (Path("src/carexpert/sources/sites/autoscout24.yaml")).read_text(encoding="utf-8")
    )
    regex = re.compile(config["listing_link_pattern"])
    for url, _ in AUTOSCOUT_URLS:
        assert regex.search(url), url
    assert not regex.search("https://www.autoscout24.fr/offres/voitures-occasion")


# --- Construction des URL de recherche -------------------------------------

def _autoscout():
    from carexpert.sources import get_source

    return get_source("autoscout24")


def test_the_search_url_matches_the_shape_the_site_produces():
    """Reference relevee dans l'interface du site:
    /lst/volvo/v70?sort=standard&desc=0&ustate=N,U&cy=F&damaged_listing=exclude&atype=C
    """
    from carexpert.schemas import SearchQuery

    source = _autoscout()
    url = source.build_search_url(SearchQuery(make="Volvo", model="V70", countries=["FR"]), 1)
    source.close()
    assert url.startswith("https://www.autoscout24.fr/lst/volvo/v70?")
    for fragment in ("atype=C", "ustate=N%2CU", "cy=F", "damaged_listing=exclude", "page=1"):
        assert fragment in url


def test_country_codes_are_the_site_s_own_not_iso():
    from carexpert.schemas import SearchQuery

    source = _autoscout()
    assert "cy=D" in source.build_search_url(SearchQuery(make="Volvo", countries=["DE"]), 1)
    assert "cy=E" in source.build_search_url(SearchQuery(make="Volvo", countries=["ES"]), 1)
    source.close()


def test_unset_filters_leave_no_empty_parameters():
    from carexpert.schemas import SearchQuery

    source = _autoscout()
    url = source.build_search_url(SearchQuery(make="Volvo", model="V70", countries=["FR"]), 1)
    source.close()
    assert "priceto=&" not in url and not url.endswith("priceto=")
    assert "kmto=" not in url
    assert "fregfrom=" not in url


def test_filters_reach_the_url_when_set():
    from carexpert.schemas import SearchQuery

    source = _autoscout()
    url = source.build_search_url(
        SearchQuery(make="Volvo", model="V70", price_max=15000, km_max=160000,
                    year_min=2015, countries=["FR"]), 2,
    )
    source.close()
    assert "priceto=15000" in url
    assert "kmto=160000" in url
    assert "fregfrom=2015" in url
    assert "page=2" in url
