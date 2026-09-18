"""Extract a vehicle advert from a page without hand-written CSS selectors.

Classified sites publish `schema.org/Car` (or `Vehicle` / `Product`) JSON-LD
for SEO. That markup is far more stable than their CSS classes: it survives
redesigns, it is the same shape across countries, and reading it means we
parse the data the site itself chose to expose publicly.

Order of preference: JSON-LD -> microdata -> OpenGraph -> per-site CSS
selectors declared in `sites/*.yaml` (last resort, expected to drift).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterable
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from ..normalize.text import parse_km, parse_power_hp, parse_price, parse_registration, parse_year
from ..schemas import ListingData, Photo, SellerType

log = logging.getLogger(__name__)

VEHICLE_TYPES = {"car", "vehicle", "motorizedvehicle", "product", "offer", "motorcycle"}

#: schema.org dit deja si le vendeur est un professionnel. S'en servir evite
#: de deviner a partir du nom du vendeur, qui ment souvent.
SELLER_TYPES = {
    "autodealer": SellerType.PRO,
    "organization": SellerType.PRO,
    "localbusiness": SellerType.PRO,
    "store": SellerType.PRO,
    "corporation": SellerType.PRO,
    "person": SellerType.PRIVATE,
    "individual": SellerType.PRIVATE,
}

FUEL_HINTS = {
    "diesel": "diesel", "gasoline": "essence", "petrol": "essence", "benzin": "essence",
    "electric": "electrique", "hybrid": "hybride", "lpg": "gpl", "cng": "gnv",
}


def _soup(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:  # pragma: no cover - lxml missing in minimal installs
        return BeautifulSoup(html, "html.parser")


def extract_jsonld(html: str) -> list[dict[str, Any]]:
    """Return every JSON-LD node in the page, graphs flattened."""
    nodes: list[dict[str, Any]] = []
    for script in _soup(html).find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            # Some sites emit trailing commas or concatenated objects.
            cleaned = re.sub(r",\s*([}\]])", r"\1", raw)
            try:
                payload = json.loads(cleaned)
            except json.JSONDecodeError:
                continue
        nodes.extend(_flatten_jsonld(payload))
    return nodes


def _flatten_jsonld(payload: Any) -> Iterable[dict[str, Any]]:
    if isinstance(payload, list):
        for item in payload:
            yield from _flatten_jsonld(item)
    elif isinstance(payload, dict):
        if "@graph" in payload:
            yield from _flatten_jsonld(payload["@graph"])
        yield payload


def _types(node: dict[str, Any]) -> set[str]:
    value = node.get("@type") or node.get("type") or ""
    values = value if isinstance(value, list) else [value]
    return {str(v).split("/")[-1].lower() for v in values}


def find_vehicle_node(nodes: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick the JSON-LD node that actually describes the car."""
    best: dict[str, Any] | None = None
    best_score = 0
    for node in nodes:
        types = _types(node)
        if not types & VEHICLE_TYPES:
            continue
        score = 0
        score += 4 if types & {"car", "vehicle", "motorizedvehicle"} else 0
        score += 2 if node.get("offers") else 0
        score += sum(
            1
            for key in ("mileageFromOdometer", "vehicleTransmission", "fuelType",
                        "vehicleModelDate", "modelDate", "brand", "model")
            if node.get(key)
        )
        if score > best_score:
            best, best_score = node, score
    return best


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        for key in ("name", "value", "@value", "text"):
            if key in value:
                return _text(value[key])
        return None
    if isinstance(value, list):
        for item in value:
            text = _text(item)
            if text:
                return text
        return None
    text = str(value).strip()
    return text or None


def _quantity(value: Any) -> float | None:
    """schema.org QuantitativeValue -> number (handles unitCode SMI = miles)."""
    if value is None:
        return None
    if isinstance(value, dict):
        amount = parse_price(_text(value.get("value")))
        unit = (str(value.get("unitCode") or value.get("unitText") or "")).upper()
        if amount is not None and unit in ("SMI", "MI", "MILE", "MILES"):
            amount *= 1.60934
        return amount
    return parse_price(_text(value))


def _images(node: dict[str, Any], base_url: str) -> list[Photo]:
    raw = node.get("image") or node.get("photo") or []
    if isinstance(raw, (str, dict)):
        raw = [raw]
    photos: list[Photo] = []
    seen: set[str] = set()
    for item in raw:
        url = _text(item.get("url") if isinstance(item, dict) else item)
        if not url:
            continue
        url = urljoin(base_url, url)
        if url in seen:
            continue
        seen.add(url)
        photos.append(Photo(url=url))
    return photos


def _offer(node: dict[str, Any]) -> dict[str, Any]:
    offers = node.get("offers")
    if isinstance(offers, list):
        offers = offers[0] if offers else None
    return offers if isinstance(offers, dict) else {}


def listing_from_jsonld(
    node: dict[str, Any], *, url: str, source: str, country: str = "FR"
) -> ListingData:
    """Map one schema.org vehicle node onto our canonical listing."""
    offer = _offer(node)
    seller = offer.get("seller") if isinstance(offer.get("seller"), dict) else {}
    address = (seller or {}).get("address") if isinstance(seller, dict) else None
    if not isinstance(address, dict):
        address = node.get("address") if isinstance(node.get("address"), dict) else {}

    title = _text(node.get("name")) or _text(node.get("headline")) or ""
    brand = _text(node.get("brand")) or _text(node.get("manufacturer"))
    model = _text(node.get("model"))

    price = parse_price(_text(offer.get("price")) or _text(node.get("price")))
    currency = (_text(offer.get("priceCurrency")) or "EUR").upper()[:3]

    km = _quantity(node.get("mileageFromOdometer") or node.get("mileage"))
    power = _quantity(
        (node.get("vehicleEngine") or {}).get("enginePower")
        if isinstance(node.get("vehicleEngine"), dict)
        else None
    )

    registration = parse_registration(
        _text(node.get("dateVehicleFirstRegistered"))
        or _text(node.get("vehicleModelDate"))
        or _text(node.get("modelDate"))
        or _text(node.get("productionDate"))
    )

    fuel_text = _text(node.get("fuelType"))
    if fuel_text:
        fuel_text = FUEL_HINTS.get(fuel_text.strip().lower(), fuel_text)

    seller_type = SellerType.UNKNOWN
    if isinstance(seller, dict):
        for name in _types(seller):
            if name in SELLER_TYPES:
                seller_type = SELLER_TYPES[name]
                break

    listing = ListingData(
        source=source,
        source_id=_text(node.get("sku")) or _text(node.get("productID")) or _listing_id(url),
        url=url,
        country=country,
        title=title or " ".join(x for x in (brand, model) if x) or "Annonce",
        description=_text(node.get("description")),
        price=price,
        currency=currency,
        make=brand,
        model=model,
        version=_text(node.get("vehicleConfiguration")) or _text(node.get("trim")),
        year=parse_year(_text(node.get("vehicleModelDate")) or _text(node.get("modelDate"))),
        first_registration=registration,
        km=parse_km(f"{km} km") if km is not None else None,
        power_hp=parse_power_hp(str(power)) if power is not None else None,
        color=_text(node.get("color")),
        doors=int(_quantity(node.get("numberOfDoors")) or 0) or None,
        seats=int(_quantity(node.get("seatingCapacity")) or 0) or None,
        seller_type=seller_type,
        seller_name=_text(seller) if seller else None,
        city=_text((address or {}).get("addressLocality")),
        postcode=_text((address or {}).get("postalCode")),
        region=_text((address or {}).get("addressRegion")),
        photos=_images(node, url),
        extra={
            "jsonld_type": sorted(_types(node)),
            "fuel_text": fuel_text,
            "transmission_text": _text(node.get("vehicleTransmission")),
            "vin": _text(node.get("vehicleIdentificationNumber")),
            "condition": _text(node.get("itemCondition")),
        },
    )
    # Fuel/gearbox wording goes through the normaliser, which speaks every
    # language the sites do.
    if fuel_text:
        listing.title = f"{listing.title} {fuel_text}"
    if listing.extra.get("transmission_text"):
        listing.title = f"{listing.title} {listing.extra['transmission_text']}"
    listing.title = listing.title.strip()
    return listing


#: Advert identifiers, in the order sites actually use them.
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
ID_PARAMS = ("id", "adid", "listingid", "annonceid", "offerid", "vehicleid")

#: Query parameters that identify where a click came from, not what it points
#: at. Two links to the same advert differ only by these.
TRACKING_PARAMS = {
    "ipc", "ipl", "source", "source_otp", "position", "ap_tier", "boost_level",
    "applied_boost_level", "relevance_adjustment", "boosting_product", "ref",
    "referrer", "cid", "gclid", "fbclid", "msclkid", "utm_source", "utm_medium",
    "utm_campaign", "utm_term", "utm_content", "search_id", "sort", "atype",
}


def _listing_id(url: str) -> str:
    """The site's own identifier for this advert.

    Getting this wrong is expensive and silent: the identifier is half of the
    database's unique key, so two adverts that collide overwrite each other.
    A naive "last long number in the path" reads `cat_ma73mo2079` on
    AutoScout24 and hands back the *model* id, which every Volvo V70 on the
    site shares - the comparables pool would then hold exactly one V70.
    """
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")

    match = UUID_RE.search(path)
    if match:
        return match.group(0).lower()

    query = parse_qs(parsed.query)
    for key, values in query.items():
        if key.lower() in ID_PARAMS and values and values[0].strip():
            return values[0].strip()[:120]

    tail = path.rsplit("/", 1)[-1] if path else url
    # A model id sits inside a `cat_ma..mo..` segment: never read it as the
    # advert id.
    cleaned = re.sub(r"cat_ma\d+mo\d+", "", tail, flags=re.I)
    digits = re.findall(r"\d{5,}", cleaned)
    if digits:
        return max(digits, key=len)
    return (tail or url)[:120]


def canonical_url(url: str) -> str:
    """Same advert, same string: drops the parameters that only track clicks.

    For advert URLs only. On a *search* URL, `sort` and `atype` are not
    tracking: they are the filters the user picked in the site's own
    interface, and dropping them silently changes the search.
    """
    parsed = urlparse(url)
    kept = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=False)
        if key.lower() not in TRACKING_PARAMS
    ]
    return urlunparse(parsed._replace(query=urlencode(kept), fragment=""))


def extract_opengraph(html: str) -> dict[str, str]:
    data: dict[str, str] = {}
    for meta in _soup(html).find_all("meta"):
        key = meta.get("property") or meta.get("name")
        content = meta.get("content")
        if key and content and (key.startswith("og:") or key.startswith("product:")):
            data.setdefault(key, content.strip())
    return data


def extract_microdata(html: str) -> dict[str, str]:
    """Minimal itemprop reader, for pages using microdata instead of JSON-LD."""
    data: dict[str, str] = {}
    for element in _soup(html).select("[itemprop]"):
        name = element.get("itemprop")
        if not name or name in data:
            continue
        value = (
            element.get("content")
            or element.get("datetime")
            or element.get("href")
            or element.get_text(" ", strip=True)
        )
        if value:
            data[name] = value.strip()
    return data


def select_field(html: str, selector: str, attr: str = "text") -> str | None:
    """Read one value with a CSS selector (per-site fallback)."""
    element = _soup(html).select_one(selector)
    if element is None:
        return None
    if attr == "text":
        return element.get_text(" ", strip=True) or None
    return (element.get(attr) or "").strip() or None


def extract_listing_links(html: str, base_url: str, pattern: str) -> list[str]:
    """Collect advert URLs from a search-results page.

    Deduplicated on the advert itself, not on the link: a results page links
    the same car from a card, a title and a photo, each carrying different
    tracking parameters. Without this the crawler fetches one advert three
    times and spends its politeness budget on nothing.
    """
    regex = re.compile(pattern)
    links: list[str] = []
    seen: set[str] = set()
    for anchor in _soup(html).find_all("a", href=True):
        href = canonical_url(urljoin(base_url, anchor["href"]))
        if not regex.search(href):
            continue
        key = _listing_id(href)
        if key in seen:
            continue
        seen.add(key)
        links.append(href)
    return links


#: Le code vendeur porte par la carte quand le JSON-LD ne dit rien.
#: Un code inconnu laisse le champ a UNKNOWN: mieux vaut ne pas savoir que
#: classer un particulier en professionnel (la cote n'est pas la meme).
ARTICLE_SELLER_TYPES = {
    "d": SellerType.PRO, "dealer": SellerType.PRO, "pro": SellerType.PRO,
    "p": SellerType.PRIVATE, "private": SellerType.PRIVATE, "priv": SellerType.PRIVATE,
}

#: Ce que la carte n'ecrit nulle part en `data-*` et qu'il faut lire dans son
#: texte. Les noms de classe des sites en CSS modules se terminent par un hash
#: qui change a chaque deploiement (`ListItemPill_text__Cr6mq`); le prefixe est
#: le nom du composant et bouge beaucoup plus rarement, donc on ne cible que
#: lui. Un selecteur qui ne trouve rien ne coute rien.
CARD_SELECTORS: dict[str, tuple[str, ...]] = {
    "previous_price": ('[class*="PreviousPrice_price"]', "[data-previous-price]",
                       ".previous-price", ".old-price"),
    "power": ('[class*="ListItemPill_text"]', '[class*="VehicleDetails"]',
              ".vehicle-details", ".listing-power"),
}

#: `(\d+) ch` mais pas `24.2 kwh`: la borne de droite exclut le `h` de kWh.
POWER_TEXT_RE = re.compile(r"(\d{2,4})\s*(?:ch|cv|hp|ps)(?![a-z0-9])", re.I)

#: Code carburant porte par les cartes AutoScout24, releve dans la taxonomie
#: que le site publie lui-meme dans sa page. Ne sert que si le schema.org ne
#: dit rien: un code inconnu ne devine pas.
ARTICLE_FUEL_TEXT = {
    "b": "essence", "d": "diesel", "e": "electrique", "c": "gnv",
    "h": "hydrogene", "l": "gpl", "m": "ethanol",
    "2": "electrique/essence", "3": "electrique/diesel",
}

#: Attributs `data-*` d'une carte de resultat, par champ de `ListingData`.
#: Plusieurs noms par champ: les sites ne s'accordent pas sur le vocabulaire.
ARTICLE_FIELDS: dict[str, tuple[str, ...]] = {
    "price": ("data-price", "data-price-value", "data-amount"),
    "km": ("data-mileage", "data-km", "data-kilometers"),
    "registration": ("data-first-registration", "data-registration", "data-firstregistration"),
    "postcode": ("data-listing-zip-code", "data-zip-code", "data-zipcode", "data-postcode"),
    "make": ("data-make", "data-brand"),
    "model": ("data-model",),
    "seller": ("data-seller-type", "data-sellertype"),
    "fuel": ("data-fuel-type", "data-fuel"),
    "gearbox": ("data-transmission-type", "data-transmission", "data-gearbox"),
    "power": ("data-power", "data-power-hp"),
}


def find_item_list(nodes: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Find the `ItemList` a results page publishes for its own SEO.

    A search page carries the whole page of adverts in one JSON-LD block,
    usually as `SearchResultsPage -> mainEntity -> ItemList`. Reading it costs
    one request for a page of results instead of one request per advert.
    """
    for node in nodes:
        if "itemlist" in _types(node) and node.get("itemListElement"):
            return node
    for node in nodes:
        for key in ("mainEntity", "mainEntityOfPage", "about", "hasPart"):
            inner = node.get(key)
            candidates = inner if isinstance(inner, list) else [inner]
            for candidate in candidates:
                if isinstance(candidate, dict) and candidate.get("itemListElement"):
                    return candidate
    return None


def _article_data(html: str) -> dict[str, dict[str, str]]:
    """Index a results page's cards by advert id, with their `data-*` values.

    The JSON-LD of a results page is complete on price, mileage and seller but
    silent on the registration date and the postcode. The card markup carries
    both. Matching the two on the advert id yields a listing good enough to
    value without opening the advert.
    """
    index: dict[str, dict[str, str]] = {}
    for element in _soup(html).find_all(["article", "li", "div"]):
        attrs = element.attrs or {}
        data = {
            key: str(value).strip()
            for key, value in attrs.items()
            if key.startswith("data-") and isinstance(value, str) and value.strip()
        }
        if not data:
            continue
        keys = [
            str(attrs.get(name) or "")
            for name in ("id", "data-guid", "data-listing-id", "data-id", "data-article-id")
        ]
        for key in keys:
            key = key.strip().lower()
            if not key:
                continue
            index.setdefault(key, {}).update(data)
            match = UUID_RE.search(key)
            if match:
                index.setdefault(match.group(0).lower(), {}).update(data)
    return index


def _article_elements(html: str) -> dict[str, Any]:
    """The card elements themselves, indexed by advert id."""
    index: dict[str, Any] = {}
    for element in _soup(html).find_all(["article", "li", "div"]):
        attrs = element.attrs or {}
        for name in ("id", "data-guid", "data-listing-id", "data-id"):
            key = str(attrs.get(name) or "").strip().lower()
            if not key:
                continue
            index.setdefault(key, element)
            match = UUID_RE.search(key)
            if match:
                index.setdefault(match.group(0).lower(), element)
    return index


def _first(data: dict[str, str], names: tuple[str, ...]) -> str | None:
    for name in names:
        value = data.get(name)
        if value:
            return value
    return None


def _card_text(card: Any, field: str) -> str | None:
    if card is None:
        return None
    for selector in CARD_SELECTORS.get(field, ()):
        for element in card.select(selector):
            text = element.get_text(" ", strip=True)
            if not text:
                continue
            if field != "power" or POWER_TEXT_RE.search(text):
                return text
    return None


def merge_card_text(listing: ListingData, card: Any) -> None:
    """Read what the card writes as text rather than as an attribute.

    Two things live only there, and both change a verdict:

    * the power, which a results page shows as `235 kW (320 Ch)`. Without it
      a Golf R and a Golf 1.2 TSI are the same car to the valuation, and the
      Golf R comes out looking wildly overpriced.
    * the previous price, when the seller has just cut it. Reading it means
      a price drop is known on the first sight of the advert instead of two
      scans later.
    """
    if card is None:
        return

    if listing.power_hp is None:
        text = _card_text(card, "power")
        match = POWER_TEXT_RE.search(text or "")
        if match:
            listing.power_hp = parse_power_hp(match.group(0))

    previous = parse_price(_card_text(card, "previous_price"))
    if previous and listing.price and previous > listing.price:
        listing.extra["previous_price"] = previous
        listing.extra["previous_price_currency"] = listing.currency


def merge_article_data(listing: ListingData, data: dict[str, str]) -> None:
    """Fill the gaps the JSON-LD leaves, never overwrite what it stated."""
    if not data:
        return

    if listing.price is None:
        listing.price = parse_price(_first(data, ARTICLE_FIELDS["price"]))
    if listing.km is None:
        listing.km = parse_km(_first(data, ARTICLE_FIELDS["km"]) or "")
    if listing.first_registration is None:
        listing.first_registration = parse_registration(_first(data, ARTICLE_FIELDS["registration"]))
    if listing.year is None and listing.first_registration is not None:
        listing.year = listing.first_registration.year
    if not listing.postcode:
        listing.postcode = _first(data, ARTICLE_FIELDS["postcode"])
    if not listing.make:
        listing.make = _first(data, ARTICLE_FIELDS["make"])
    if not listing.model:
        listing.model = _first(data, ARTICLE_FIELDS["model"])
    if listing.power_hp is None:
        power = _first(data, ARTICLE_FIELDS["power"])
        if power:
            listing.power_hp = parse_power_hp(power)
    if listing.seller_type is SellerType.UNKNOWN:
        code = (_first(data, ARTICLE_FIELDS["seller"]) or "").lower()
        listing.seller_type = ARTICLE_SELLER_TYPES.get(code, SellerType.UNKNOWN)

    # Le carburant du schema.org fait foi. Le code de la carte ne sert que
    # quand il manque, et passe par le normaliseur comme n'importe quel
    # libelle: c'est lui qui sait que "electrique/essence" est un hybride
    # rechargeable.
    if not listing.extra.get("fuel_text"):
        code = (_first(data, ARTICLE_FIELDS["fuel"]) or "").lower()
        label = ARTICLE_FUEL_TEXT.get(code)
        if label:
            listing.extra["fuel_text"] = label
            listing.title = f"{listing.title} {label}".strip()

    # Le libelle du carburant et de la boite part au normaliseur, qui parle
    # toutes les langues des sites; les codes maison (`b`, `d`) ne lui
    # apprendraient rien et pollueraient le titre.
    listing.extra.setdefault("card_data", {k: v[:200] for k, v in list(data.items())[:30]})


def extract_listings_from_search(
    html: str, *, base_url: str, source: str, country: str = "FR"
) -> list[ListingData]:
    """Read a whole page of adverts from the results page itself.

    Returns an empty list when the page publishes no `ItemList`; the caller
    then falls back to harvesting links and opening each advert.

    This path exists because the link-harvesting one silently stops working:
    on AutoScout24 the advert titles are `<a>` tags with no `href` at all
    (JavaScript adds it on hydration), so a link harvester finds zero adverts
    on a page that plainly shows fourteen.
    """
    item_list = find_item_list(extract_jsonld(html))
    if not item_list:
        return []

    cards = _article_data(html)
    elements_by_id = _article_elements(html)
    listings: list[ListingData] = []
    seen: set[str] = set()

    elements = item_list.get("itemListElement") or []
    if not isinstance(elements, list):
        elements = [elements]

    for element in elements:
        if not isinstance(element, dict):
            continue
        node = element.get("item") if isinstance(element.get("item"), dict) else element
        if not isinstance(node, dict) or not (_types(node) & VEHICLE_TYPES):
            continue

        raw_url = _text(element.get("url")) or _text(node.get("url")) or _text(element.get("@id"))
        if not raw_url:
            continue
        url = canonical_url(urljoin(base_url, raw_url))

        listing = listing_from_jsonld(node, url=url, source=source, country=country)
        if listing.source_id in seen:
            continue
        seen.add(listing.source_id)

        merge_article_data(listing, cards.get(listing.source_id.lower(), {}))
        merge_card_text(listing, elements_by_id.get(listing.source_id.lower()))
        listing.fill_price_eur()
        listings.append(listing)

    return listings


def extract_from_page(
    html: str, *, url: str, source: str, country: str = "FR", selectors: dict | None = None
) -> ListingData | None:
    """Best-effort extraction of a single advert page."""
    node = find_vehicle_node(extract_jsonld(html))
    listing: ListingData | None = None
    if node is not None:
        listing = listing_from_jsonld(node, url=url, source=source, country=country)

    if listing is None:
        micro = extract_microdata(html)
        og = extract_opengraph(html)
        title = micro.get("name") or og.get("og:title")
        if not title:
            return None
        listing = ListingData(
            source=source,
            source_id=_listing_id(url),
            url=url,
            country=country,
            title=title,
            description=micro.get("description") or og.get("og:description"),
            price=parse_price(micro.get("price") or og.get("product:price:amount")),
            currency=(micro.get("priceCurrency") or og.get("product:price:currency") or "EUR")[:3],
            photos=[Photo(url=urljoin(url, og["og:image"]))] if og.get("og:image") else [],
        )

    _apply_selectors(listing, html, selectors or {})
    listing.fill_price_eur()
    return listing


def _apply_selectors(listing: ListingData, html: str, selectors: dict) -> None:
    """Fill gaps with per-site CSS selectors declared in YAML."""
    if not selectors:
        return
    setters = {
        "title": lambda v: setattr(listing, "title", v),
        "description": lambda v: setattr(listing, "description", v),
        "price": lambda v: setattr(listing, "price", parse_price(v)),
        "km": lambda v: setattr(listing, "km", parse_km(v)),
        "year": lambda v: setattr(listing, "year", parse_year(v)),
        "power": lambda v: setattr(listing, "power_hp", parse_power_hp(v)),
        "seller": lambda v: setattr(listing, "seller_name", v),
        "city": lambda v: setattr(listing, "city", v),
        "registration": lambda v: setattr(listing, "first_registration", parse_registration(v)),
    }
    for field, rule in selectors.items():
        setter = setters.get(field)
        if setter is None:
            continue
        current = getattr(listing, field if field != "power" else "power_hp", None)
        if current not in (None, "", []):
            continue
        selector = rule if isinstance(rule, str) else rule.get("selector", "")
        attr = "text" if isinstance(rule, str) else rule.get("attr", "text")
        if not selector:
            continue
        value = select_field(html, selector, attr)
        if value:
            try:
                setter(value)
            except Exception as exc:  # pragma: no cover - defensive
                log.debug("selector %s failed: %s", selector, exc)
