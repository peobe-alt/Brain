"""schema.org extraction, the backbone of source-agnostic scraping."""

from __future__ import annotations

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
