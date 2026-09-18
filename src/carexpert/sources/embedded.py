"""Read adverts from the JSON state a site ships inside its own HTML.

Why this module exists
----------------------

`structured.py` reads `schema.org` markup, which is what a site publishes
*for search engines*. Three of the four French sources publish nothing of the
sort: leboncoin, La Centrale and leparking render their results from
JavaScript. The usual conclusion - "it needs a headless browser" - is wrong
often enough to be worth this file.

A React site does not fetch its first page of results from the browser: that
would cost a round trip and lose the SEO race. It **serialises the server's
answer into the HTML** and hands it to the client for hydration. The advert
list is right there in the first response, under one of a handful of
well-known names:

* `<script id="__NEXT_DATA__">` - Next.js pages router (La Centrale);
* `self.__next_f.push([1, "..."])` - Next.js app router flight stream, the
  shape leboncoin serves today;
* `window.__NUXT__` / `__NUXT_DATA__` - Nuxt;
* `__INITIAL_STATE__`, `__PRELOADED_STATE__`, `__APOLLO_STATE__`,
  `__remixContext` - the rest of the field.

So the order of preference becomes: schema.org, then this, and only then a
real browser. Measured on a leboncoin results page: the adverts are in the
first HTTP response, nine times cheaper than rendering it.

How the records are found
-------------------------

Not by a path. `props.pageProps.searchData.ads[0]` is exactly as brittle as
the CSS selectors this project already refuses to write, and it breaks on a
deploy nobody announces. Instead `find_vehicle_records` walks the whole tree
and keeps the objects that *look like* a car advert: an identity, a price,
and a mileage or a year. A site can rename its routes, move its state around
or re-shape its props; an advert still looks like an advert.

Key names then map through one multilingual table, because the same field is
`mileage`, `kilometrage`, `km` or `odometer` depending on who wrote it.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..normalize.text import parse_km, parse_power_hp, parse_price, parse_registration, parse_year
from ..schemas import ListingData, Photo, SellerType

log = logging.getLogger(__name__)


# --- finding the state blobs ---------------------------------------------

#: Scripts whose entire body is the page state, addressed by `id`.
JSON_SCRIPT_IDS = (
    "__NEXT_DATA__",
    "__NUXT_DATA__",
    "__UNIVERSAL_DATA_FOR_REHYDRATION__",
    "serverApp-state",
    "ng-state",
)

#: `window.X = {...}` assignments carrying the same thing. The trailing
#: characters differ (`;`, `</script>`, a newline), so the object is read by
#: brace balance rather than by a regex that would have to guess the end.
STATE_ASSIGNMENTS = (
    "__NUXT__",
    "__INITIAL_STATE__",
    "__PRELOADED_STATE__",
    "__APOLLO_STATE__",
    "__remixContext",
    "__INITIAL_DATA__",
    "__STATE__",
    "INITIAL_STATE",
)

#: A flight chunk can be tens of thousands of characters and there are
#: hundreds of them; without a ceiling a pathological page turns a scan into
#: a memory incident.
MAX_STATE_CHARS = 12_000_000


def embedded_states(html: str) -> Iterator[Any]:
    """Yield every JSON application state found in the page.

    Order matters: the named script tags first, because they are the cheapest
    to parse and the most likely to hold the results, then the assignments,
    then the flight stream which needs reassembling.
    """
    if not html:
        return

    soup = _soup(html)

    for script in soup.find_all("script", attrs={"type": "application/json"}):
        raw = (script.string or script.get_text() or "").strip()
        if not raw:
            continue
        parsed = _loads(raw)
        if parsed is not None:
            yield parsed

    for identifier in JSON_SCRIPT_IDS:
        for script in soup.find_all("script", attrs={"id": identifier}):
            raw = (script.string or script.get_text() or "").strip()
            parsed = _loads(raw)
            if parsed is not None:
                yield parsed

    for name in STATE_ASSIGNMENTS:
        for blob in _assigned_objects(html, name):
            parsed = _loads(blob)
            if parsed is not None:
                yield parsed

    flight = _flight_payload(html)
    if flight:
        yield from _json_objects(flight)


def _soup(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:  # pragma: no cover - lxml missing in minimal installs
        return BeautifulSoup(html, "html.parser")


def _loads(raw: str | None) -> Any | None:
    if not raw or len(raw) > MAX_STATE_CHARS:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None


def _assigned_objects(html: str, name: str) -> Iterator[str]:
    """Every `... name = {...}` object body in the page, as raw JSON text.

    Nuxt writes `window.__NUXT__=(function(a,b){return {...}}(1,2))`, which is
    not JSON at all. Balance-scanning from the first `{` after the `=` returns
    the object anyway when there is one, and returns nothing harmful when
    there is not: `_loads` simply refuses it.
    """
    for match in re.finditer(rf"\b{re.escape(name)}\s*=\s*", html):
        start = html.find("{", match.end(), match.end() + 200)
        if start == -1:
            continue
        end = _matching_brace(html, start)
        if end is not None:
            yield html[start : end + 1]


def _flight_payload(html: str) -> str:
    """Reassemble a Next.js app-router flight stream into one text.

    The server streams the page as `self.__next_f.push([1, "<chunk>"])` calls
    whose second element is a JSON string. Concatenated, the chunks make up
    the RSC payload, and the advert objects sit inside it as plain JSON.
    """
    chunks: list[str] = []
    total = 0
    for match in re.finditer(r"__next_f\.push\(", html):
        start = html.find("[", match.end(), match.end() + 8)
        if start == -1:
            continue
        end = _matching_brace(html, start, opening="[", closing="]")
        if end is None:
            continue
        payload = _loads(html[start : end + 1])
        if not isinstance(payload, list) or len(payload) < 2:
            continue
        piece = payload[1]
        if not isinstance(piece, str):
            continue
        chunks.append(piece)
        total += len(piece)
        if total > MAX_STATE_CHARS:
            log.debug("flight payload truncated at %s characters", total)
            break
    return "".join(chunks)


def _json_objects(text: str) -> Iterator[Any]:
    """Every balanced JSON object embedded in a larger text.

    Used on the flight stream, which is not JSON as a whole: it interleaves
    JSON fragments with row markers like `2:I[...]`. Scanning for objects is
    what turns it back into something walkable.
    """
    index = 0
    length = len(text)
    while index < length:
        start = text.find("{", index)
        if start == -1:
            return
        end = _matching_brace(text, start)
        if end is None:
            return
        parsed = _loads(text[start : end + 1])
        if parsed is not None:
            yield parsed
            index = end + 1
        else:
            # Not valid on its own: step past this brace rather than past the
            # whole span, so a nested object that *is* valid still gets seen.
            index = start + 1


def _matching_brace(
    text: str, start: int, *, opening: str = "{", closing: str = "}"
) -> int | None:
    """Index of the bracket closing the one at `start`, string-aware."""
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, min(len(text), start + MAX_STATE_CHARS)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return index
    return None


# --- recognising an advert in a tree -------------------------------------

#: Containers whose contents belong to the advert that holds them. Flattened
#: into the parent so `vehicle.mileage` and `mileage` read the same.
MERGE_KEYS = {
    "vehicle", "car", "voiture", "auto", "location", "place", "owner", "seller",
    "vendeur", "dealer", "characteristics", "caracteristiques", "specifications",
    "specs", "technicaldata", "criteria", "critères", "criteres", "params",
    "parameters", "properties", "details", "detail", "data", "infos", "info",
    "pricing", "prices", "attributes", "attributs", "fields", "options",
    "node", "item", "offer", "listing", "classified", "annonce", "ad", "summary",
    "content", "values", "additionalinfo", "extra", "power", "puissance",
    "engine", "motor", "moteur", "images", "photos", "media",
}

#: Key/value pair shapes. leboncoin describes a car as a list of
#: `{"key": "mileage", "value": "120000", "value_label": "120 000 km"}`, and
#: it is not alone: the same idea appears as `{name, value}` elsewhere.
#: Containers describing whoever is selling. Their `name`, `id` and `type`
#: are the seller's, so they are reachable only by their full path: promoted
#: bare, `seller.name` would become the advert's title.
SELLER_CONTAINERS = {"owner", "seller", "vendeur", "dealer", "store", "shop", "pro"}
SELLER_PRIVATE_KEYS = {"name", "title", "subject", "id", "url", "type", "description",
                       "label", "reference"}

PAIR_KEY_FIELDS = ("key", "name", "code", "label", "id", "slug", "type", "criteria")
PAIR_VALUE_FIELDS = ("value_label", "valuelabel", "label_value", "value", "values", "text")

#: How deep to flatten. Three levels reach `props > pageProps > vehicle`
#: without walking an entire component tree on every candidate.
FLATTEN_DEPTH = 3

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "id": ("listid", "classifiedid", "adid", "advertid", "offerid", "idannonce",
           "reference", "uuid", "id"),
    "url": ("url", "seourl", "detailurl", "canonicalurl", "permalink", "link",
            "href", "urlannonce", "path"),
    "title": ("subject", "title", "headline", "titre", "libelle", "name"),
    "description": ("body", "description", "commentaire", "comment", "text"),
    "price": ("customerprice", "priceeur", "sellingprice", "pricevalue", "price",
              "prix", "amount"),
    "price_cents": ("pricecents", "pricecent", "amountcents"),
    "km": ("mileage", "mileagekm", "kilometrage", "kilometers", "odometer", "km"),
    "year": ("regdate", "modelyear", "vehiclemodeldate", "annee", "year"),
    "registration": ("firstregistrationdate", "firstregistration", "registrationdate",
                     "misecirculation", "datemisecirculation", "dateofcirculation"),
    "make": ("makename", "brandname", "ucarbrand", "manufacturer", "make", "brand",
             "marque"),
    "model": ("modelname", "ucarmodel", "model", "modele"),
    "version": ("version", "trim", "finition", "variant", "grade"),
    "fuel": ("fueltype", "energytype", "carburant", "energie", "energy", "fuel"),
    "gearbox": ("gearboxtype", "transmissiontype", "gearbox", "transmission",
                "boitevitesse", "boite"),
    "power": ("horsepower", "horsepowerdin", "powerdin", "dinpower", "puissancedin",
              "power.din", "din", "power", "puissance"),
    "doors": ("numberofdoors", "nbdoors", "doors", "nombreportes", "portes"),
    "seats": ("numberofseats", "nbseats", "seats", "places"),
    "color": ("exteriorcolor", "vehiclecolor", "colour", "color", "couleur"),
    "owners": ("numberofowners", "previousowners", "nbowners", "owners",
               "proprietaires"),
    "city": ("citylabel", "cityname", "city", "ville", "locality", "commune"),
    "postcode": ("postalcode", "zipcode", "zip", "codepostal", "cp"),
    "region": ("regionname", "region", "departmentname", "departement", "department"),
    "seller_name": ("storename", "dealername", "sellername", "ownername",
                    "nomvendeur", "shopname", "owner.name", "seller.name",
                    "dealer.name", "store.name", "owner.storename"),
    "posted_at": ("firstpublicationdate", "publicationdate", "datepublication",
                  "publishedat", "createdat", "indexdate", "date"),
}

#: Seller type is read only from keys that say whose type it is: a bare
#: `type` on an advert means the advert's kind ("offer"), never the seller's.
SELLER_TYPE_KEYS = (
    "ownertype", "owner.type", "sellertype", "seller.type", "vendeur.type",
    "customertype", "accounttype", "professional", "ispro", "isprofessional",
    "proaccount", "dealer.type",
)

SELLER_TYPE_VALUES = {
    "pro": SellerType.PRO, "professional": SellerType.PRO, "dealer": SellerType.PRO,
    "professionnel": SellerType.PRO, "company": SellerType.PRO, "store": SellerType.PRO,
    "garage": SellerType.PRO, "true": SellerType.PRO, "1": SellerType.PRO,
    "private": SellerType.PRIVATE, "particulier": SellerType.PRIVATE,
    "individual": SellerType.PRIVATE, "person": SellerType.PRIVATE,
    "false": SellerType.PRIVATE, "0": SellerType.PRIVATE,
}

PHOTO_KEYS = ("urlslarge", "urls", "imageurls", "images", "photos", "pictures",
              "visuals", "medias", "thumbnails", "gallery")
PHOTO_URL_FIELDS = ("urllarge", "large", "url", "src", "href", "original", "big",
                    "thumb", "thumburl")

#: How many levels of the state tree to walk looking for adverts. A Next.js
#: payload nests deeply; an advert is rarely past fifteen.
MAX_WALK_DEPTH = 16


def normalize_key(key: str) -> str:
    """`u_car_brand`, `uCarBrand` and `U-Car-Brand` are the same field."""
    return re.sub(r"[^a-z0-9]+", "", str(key).lower())


def _flatten(record: dict[str, Any]) -> dict[str, Any]:
    """A record and its sub-objects as one flat, normalised mapping.

    Breadth-first on purpose: the shallowest occurrence of a name wins, so a
    `price` on the advert is not overwritten by the `price` of some nested
    financing offer.
    """
    flat: dict[str, Any] = {}
    queue: list[tuple[dict[str, Any], str, int, bool]] = [(record, "", 0, False)]

    while queue:
        node, prefix, depth, in_seller = queue.pop(0)
        for key, value in node.items():
            name = normalize_key(key)
            if not name:
                continue
            path = f"{prefix}{name}"
            if isinstance(value, dict):
                flat.setdefault(path, value)
                if depth < FLATTEN_DEPTH and (name in MERGE_KEYS or not prefix):
                    queue.append(
                        (value, f"{path}.", depth + 1, in_seller or name in SELLER_CONTAINERS)
                    )
                continue
            if isinstance(value, list):
                flat.setdefault(path, value)
                if prefix and not (in_seller and name in SELLER_PRIVATE_KEYS):
                    flat.setdefault(name, value)
                for pair_key, pair_value in _pairs_from_list(value).items():
                    flat.setdefault(pair_key, pair_value)
                continue
            flat.setdefault(path, value)
            # A nested field is also reachable by its bare name, so that
            # `vehicle.mileage` answers to `mileage`. The exception is the
            # seller's own identity: promoted bare, `seller.name` would be
            # read as the advert's title, and every La Centrale advert would
            # be titled after its garage.
            if prefix and not (in_seller and name in SELLER_PRIVATE_KEYS):
                flat.setdefault(name, value)
    return flat


def _pairs_from_list(values: list[Any]) -> dict[str, Any]:
    """Turn a list of `{key, value}` descriptors into a mapping.

    `value_label` is preferred over `value` because sites store codes there:
    leboncoin's fuel is `"1"` in `value` and `"Diesel"` in `value_label`, and
    a code we cannot decode is worse than the label we can read.
    """
    pairs: dict[str, Any] = {}
    for item in values:
        if not isinstance(item, dict):
            continue
        name = next(
            (normalize_key(item[f]) for f in PAIR_KEY_FIELDS
             if isinstance(item.get(f), str) and item[f].strip()),
            None,
        )
        if not name:
            continue
        value = next(
            (item[f] for f in PAIR_VALUE_FIELDS
             if item.get(f) not in (None, "", [], {})),
            None,
        )
        if value is None:
            continue
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value if isinstance(v, (str, int, float)))
        if isinstance(value, (str, int, float, bool)):
            pairs.setdefault(name, value)
    return pairs


def _pick(flat: dict[str, Any], field: str) -> Any:
    for alias in FIELD_ALIASES.get(field, ()):
        if alias in flat:
            value = flat[alias]
            if value not in (None, "", [], {}):
                return value
    return None


def _scalar(value: Any) -> Any:
    """Sites wrap single values in lists more often than you would expect.

    leboncoin's own API answers `"price": [12500]`; reading that as a list
    loses every price on the page.
    """
    if isinstance(value, list):
        for item in value:
            if item not in (None, "", [], {}):
                return _scalar(item)
        return None
    if isinstance(value, dict):
        for key in ("value", "amount", "raw", "text", "label"):
            if key in value:
                return _scalar(value[key])
        return None
    return value


def looks_like_vehicle(record: Any) -> bool:
    """Whether a JSON object is an advert rather than a piece of furniture.

    The test is deliberately about content, not about where the object sits:
    an identity, a price, and something that dates or measures the car. A
    filter facet has a label and a count but no mileage; a breadcrumb has a
    url but no price; a financing widget has a price but no car.
    """
    if not isinstance(record, dict) or len(record) < 3:
        return False
    flat = _flatten(record)
    if _scalar(_pick(flat, "price")) is None and _pick(flat, "price_cents") is None:
        return False
    if _pick(flat, "id") is None and _pick(flat, "url") is None:
        return False
    dated = _pick(flat, "year") or _pick(flat, "registration")
    measured = _pick(flat, "km")
    return bool(dated or measured)


def _holds_vehicles(record: dict[str, Any]) -> bool:
    """Whether a matching object is really a container of adverts.

    A results page wraps its list in an object that carries the search's own
    price, mileage and identifier - enough to pass `looks_like_vehicle` and,
    without this check, to swallow the twenty adverts underneath it. If a
    node holds a list of advert-shaped objects, it is the shelf, not the car.
    """
    for value in record.values():
        if isinstance(value, list) and any(
            isinstance(item, dict) and looks_like_vehicle(item) for item in value[:5]
        ):
            return True
    return False


def find_vehicle_records(state: Any, *, limit: int = 500) -> list[dict[str, Any]]:
    """Every advert-shaped object in a state tree, outermost first.

    A match is not descended into: the sub-objects of an advert (its seller,
    its photos) are parts of it, not adverts of their own.
    """
    found: list[dict[str, Any]] = []
    seen: set[int] = set()
    queue: list[tuple[Any, int]] = [(state, 0)]

    while queue and len(found) < limit:
        node, depth = queue.pop(0)
        if depth > MAX_WALK_DEPTH:
            continue
        if isinstance(node, dict):
            if id(node) in seen:
                continue
            seen.add(id(node))
            if looks_like_vehicle(node) and not _holds_vehicles(node):
                found.append(node)
                continue
            queue.extend((value, depth + 1) for value in node.values()
                         if isinstance(value, (dict, list)))
        elif isinstance(node, list):
            queue.extend((value, depth + 1) for value in node
                         if isinstance(value, (dict, list)))
    return found


# --- turning a record into a listing --------------------------------------


def listing_from_record(
    record: dict[str, Any], *, base_url: str, source: str, country: str = "FR"
) -> ListingData | None:
    """Map one advert-shaped object onto the project's vocabulary."""
    from .structured import _listing_id, canonical_url

    flat = _flatten(record)

    raw_url = _scalar(_pick(flat, "url"))
    identifier = _scalar(_pick(flat, "id"))
    if raw_url:
        url = canonical_url(urljoin(base_url, str(raw_url)))
    elif identifier is not None:
        # No link in the payload: the identity is still known, and the caller
        # can rebuild the address from the site's advert URL shape.
        url = urljoin(base_url, f"#{identifier}")
    else:
        return None

    source_id = str(identifier) if identifier not in (None, "") else _listing_id(url)

    make = _text(_scalar(_pick(flat, "make")))
    model = _text(_scalar(_pick(flat, "model")))
    version = _text(_scalar(_pick(flat, "version")))
    title = _text(_scalar(_pick(flat, "title")))
    if not title:
        title = " ".join(part for part in (make, model, version) if part) or None
    if not title:
        return None

    price = parse_price(_scalar(_pick(flat, "price")))
    if price is None:
        cents = _scalar(_pick(flat, "price_cents"))
        if cents is not None:
            price = parse_price(cents)
            price = price / 100 if price else None

    listing = ListingData(
        source=source,
        source_id=source_id,
        url=url,
        country=country,
        title=title,
        description=_text(_scalar(_pick(flat, "description"))),
        price=price,
        make=make,
        model=model,
        version=version,
        year=parse_year(_scalar(_pick(flat, "year"))),
        first_registration=parse_registration(_str_or_none(_pick(flat, "registration"))),
        km=parse_km(_scalar(_pick(flat, "km"))),
        power_hp=parse_power_hp(_scalar(_pick(flat, "power"))),
        doors=_as_int(_scalar(_pick(flat, "doors"))),
        seats=_as_int(_scalar(_pick(flat, "seats"))),
        color=_text(_scalar(_pick(flat, "color"))),
        owners=_as_int(_scalar(_pick(flat, "owners"))),
        seller_type=_seller_type(flat),
        seller_name=_text(_scalar(_pick(flat, "seller_name"))),
        city=_text(_scalar(_pick(flat, "city"))),
        region=_text(_scalar(_pick(flat, "region"))),
        postcode=_postcode(_scalar(_pick(flat, "postcode"))),
        photos=_photos(flat, base_url),
    )

    # Fuel and gearbox travel as site codes as often as words; the shared
    # detectors read the words, and `enrich` will retry on the title later.
    from ..normalize.vehicle import detect_fuel, detect_gearbox

    listing.fuel = detect_fuel(_text(_scalar(_pick(flat, "fuel"))))
    listing.gearbox = detect_gearbox(_text(_scalar(_pick(flat, "gearbox"))))

    if listing.year is None and listing.first_registration is not None:
        listing.year = listing.first_registration.year

    listing.fill_price_eur()
    return listing


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, (list, dict)):
        return None
    text = str(value).strip()
    return text or None


def _str_or_none(value: Any) -> str | None:
    scalar = _scalar(value)
    return None if scalar is None else str(scalar)


def _as_int(value: Any) -> int | None:
    try:
        number = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None
    return number if 0 < number < 100 else None


def _postcode(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    digits = re.sub(r"\D", "", text)
    return digits[:5] or None


def _seller_type(flat: dict[str, Any]) -> SellerType:
    for key in SELLER_TYPE_KEYS:
        if key not in flat:
            continue
        value = _scalar(flat[key])
        if value is None:
            continue
        mapped = SELLER_TYPE_VALUES.get(str(value).strip().lower())
        if mapped is not None:
            return mapped
    return SellerType.UNKNOWN


def _photos(flat: dict[str, Any], base_url: str) -> list[Photo]:
    """Photo URLs, from whichever of a dozen shapes the site chose."""
    urls: list[str] = []
    for key in PHOTO_KEYS:
        value = flat.get(key)
        if value is None:
            continue
        for candidate in _photo_urls(value):
            absolute = urljoin(base_url, candidate)
            if absolute not in urls:
                urls.append(absolute)
        if urls:
            break
    return [Photo(url=url) for url in urls[:20]]


def _photo_urls(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        if value.startswith(("http", "//", "/")):
            yield value
        return
    if isinstance(value, list):
        for item in value:
            yield from _photo_urls(item)
        return
    if isinstance(value, dict):
        for field in PHOTO_URL_FIELDS:
            if isinstance(value.get(field), str):
                yield from _photo_urls(value[field])
                return
        for nested in value.values():
            if isinstance(nested, (list, dict, str)):
                yield from _photo_urls(nested)


# --- the two entry points -------------------------------------------------


def extract_listings_from_state(
    html: str, *, base_url: str, source: str, country: str = "FR", limit: int = 500
) -> list[ListingData]:
    """Every advert a results page carries in its embedded state."""
    listings: list[ListingData] = []
    seen: set[str] = set()

    for state in embedded_states(html):
        for record in find_vehicle_records(state, limit=limit):
            listing = listing_from_record(
                record, base_url=base_url, source=source, country=country
            )
            if listing is None or listing.source_id in seen:
                continue
            seen.add(listing.source_id)
            listings.append(listing)
            if len(listings) >= limit:
                return listings
    return listings


def extract_listing_from_state(
    html: str, *, url: str, source: str, country: str = "FR"
) -> ListingData | None:
    """The advert an advert page is about, from its embedded state.

    A detail page also carries the adverts of its "similar cars" rail, so the
    one matching the page's own address wins; the richest record is the
    fallback, since the page is about a car either way.
    """
    from .structured import _listing_id

    wanted = _listing_id(url)
    candidates = extract_listings_from_state(
        html, base_url=url, source=source, country=country, limit=60
    )
    if not candidates:
        return None
    for listing in candidates:
        if listing.source_id == wanted or listing.url.rstrip("/") == url.rstrip("/"):
            return listing
    return max(candidates, key=_completeness)


def _completeness(listing: ListingData) -> int:
    fields = (listing.price, listing.km, listing.year, listing.make, listing.model,
              listing.description, listing.city)
    return sum(1 for field in fields if field not in (None, "")) + len(listing.photos)
