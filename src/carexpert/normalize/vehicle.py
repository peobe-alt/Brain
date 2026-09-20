"""Vehicle-level normalisation: makes, models, fuel, gearbox, body, options.

Adverts are written in French, German, Dutch, Italian, Spanish and English,
and the *same* car is called `Serie 3`, `3er` or `3 Series` depending on the
country. Comparables only work if all of that collapses onto one label.
"""

from __future__ import annotations

import re

from ..schemas import BodyType, Fuel, Gearbox, ListingData, SellerType
from .text import clean_text, parse_km, parse_power_hp, parse_year, strip_accents

# --- Makes -----------------------------------------------------------------

MAKE_ALIASES: dict[str, str] = {
    "vw": "Volkswagen", "volkswagen": "Volkswagen",
    "mercedes": "Mercedes-Benz", "mercedes-benz": "Mercedes-Benz", "mercedes benz": "Mercedes-Benz",
    "mb": "Mercedes-Benz",
    "bmw": "BMW", "audi": "Audi", "porsche": "Porsche", "mini": "Mini", "opel": "Opel",
    "vauxhall": "Opel", "skoda": "Skoda", "seat": "Seat", "cupra": "Cupra",
    "peugeot": "Peugeot", "renault": "Renault", "dacia": "Dacia", "citroen": "Citroen",
    "ds": "DS", "ds automobiles": "DS", "alpine": "Alpine",
    "fiat": "Fiat", "abarth": "Abarth", "alfa": "Alfa Romeo", "alfa romeo": "Alfa Romeo",
    "lancia": "Lancia", "maserati": "Maserati", "ferrari": "Ferrari", "lamborghini": "Lamborghini",
    "toyota": "Toyota", "lexus": "Lexus", "honda": "Honda", "nissan": "Nissan", "mazda": "Mazda",
    "mitsubishi": "Mitsubishi", "subaru": "Subaru", "suzuki": "Suzuki",
    "hyundai": "Hyundai", "kia": "Kia", "ssangyong": "SsangYong",
    "volvo": "Volvo", "polestar": "Polestar", "saab": "Saab",
    "ford": "Ford", "jaguar": "Jaguar", "land rover": "Land Rover", "landrover": "Land Rover",
    "range rover": "Land Rover", "jeep": "Jeep", "chrysler": "Chrysler", "dodge": "Dodge",
    "tesla": "Tesla", "byd": "BYD", "mg": "MG", "smart": "Smart",
    "iveco": "Iveco", "man": "MAN",
}

#: Makes whose residual value holds better / worse than the mainstream curve.
PREMIUM_MAKES = {
    "BMW", "Mercedes-Benz", "Audi", "Porsche", "Jaguar", "Land Rover", "Lexus",
    "Maserati", "Ferrari", "Lamborghini", "Alpine", "Tesla", "Polestar", "Volvo", "DS",
}

# --- Model aliases (cross-language) ----------------------------------------

MODEL_RULES: dict[str, list[tuple[str, str]]] = {
    "BMW": [
        (r"\b(?:serie\s*|series\s*|)([1-8])(?:er|\s*series|\s*serie)\b", r"Serie \1"),
        (r"\b([1-8])\d{2}\s*(?:d|i|e|xd|xi|td)\b", r"Serie \1"),
        (r"\b(x[1-7]|z[34]|i[3-8]|ix[1-3]|ix)\b", None),
    ],
    "Mercedes-Benz": [
        (r"\b(?:classe|klasse|class)\s*([a-gs])\b", r"Classe \1"),
        (r"\b([a-gs])\s*[- ]?klasse\b", r"Classe \1"),
        (r"\b([a-gs])\s?\d{3}\b", r"Classe \1"),
        (r"\b(gla|glb|glc|gle|gls|cla|cls|slk|slc|sl|amg\s?gt|eqa|eqb|eqc|eqe|eqs|vito|viano|sprinter|citan)\b", None),
    ],
    "Audi": [(r"\b([aqers]\s?\d|tt|rs\s?\d|e-tron|q\d\s?e-tron)\b", None)],
    "Volkswagen": [(r"\b(golf|polo|passat|tiguan|touran|t-roc|t-cross|touareg|arteon|sharan|caddy|transporter|up|id\.?\s?[3-7]|scirocco|beetle|coccinelle)\b", None)],
    "Renault": [(r"\b(clio|megane|captur|kadjar|scenic|espace|twingo|zoe|austral|arkana|talisman|laguna|kangoo|trafic|master|modus)\b", None)],
    "Peugeot": [(r"\b(\d{3,4})\b", None), (r"\b(rifter|partner|expert|boxer|traveller)\b", None)],
    "Citroen": [(r"\b(c\d|c\d\s?aircross|c\d\s?picasso|berlingo|jumpy|jumper|ds\d|spacetourer|ami)\b", None)],
    "Toyota": [(r"\b(yaris|corolla|auris|rav4|c-hr|chr|prius|aygo|proace|hilux|land cruiser|camry|verso)\b", None)],
    "Ford": [(r"\b(fiesta|focus|puma|kuga|mondeo|mustang|ranger|transit|ecosport|s-max|c-max|explorer)\b", None)],
    "Tesla": [(r"\b(model\s?[3sxy])\b", None)],
    "Polestar": [(r"\b(polestar\s?\d|[1-4])\b", None)],
    "Opel": [(r"\b(corsa|astra|insignia|mokka|crossland|grandland|zafira|meriva|adam|combo|vivaro)\b", None)],
}

MODEL_STOPWORDS = {
    "occasion", "vends", "vend", "vente", "voiture", "auto", "tres", "bon", "etat", "annee",
    "gebraucht", "used", "car", "km", "cv", "ch", "ps", "kw", "diesel", "essence", "benzin",
}

# --- Keyword tables --------------------------------------------------------

FUEL_KEYWORDS: list[tuple[Fuel, tuple[str, ...]]] = [
    # "Electrique/Essence" est le libelle AutoScout24 d'un hybride rechargeable.
    # Le lire comme un electrique donne un vehicule sans moteur thermique et
    # une decote de batterie qui n'a rien a voir.
    (Fuel.PHEV, ("hybride rechargeable", "plug-in", "plug in", "phev", "plugin hybrid",
                 "hybrid rechargeable", "e-hybrid", "recharge", "gte", "gse",
                 "electrique/essence", "electrique / essence", "electrique-essence",
                 "electrique/diesel", "electrique / diesel", "elektro/benzin",
                 "elettrica/benzina", "electrico/gasolina")),
    # Les mots explicites d'abord, les badges commerciaux ensuite. "Hybride"
    # passe avant "electrique" parce que le libelle leboncoin d'une hybride
    # simple est "Hybride essence/electrique": lu dans l'ordre naturel, une
    # Yaris hybride sortait en electrique, donc valorisee sur la courbe de
    # decote d'une batterie qu'elle n'a pas.
    (Fuel.HYBRID, ("hybride", "hybrid", "mhev", "hev")),
    (Fuel.ELECTRIC, ("electrique", "elektro", "elettrica", "electrico", "electric", "bev",
                     " ev ", "e-tron", "id.3", "id.4", "id.5", "zoe", "leaf", "model 3",
                     "model y", "kwh")),
    # Un badge ne dit pas le carburant a lui seul: Renault vend la Megane
    # "E-Tech electrique" et la Clio "E-Tech hybride". Place ici, il ne
    # tranche que lorsque aucun mot explicite ne l'a fait, ce qui evite de
    # valoriser une electrique de grande serie sur la courbe d'une hybride.
    (Fuel.HYBRID, ("e-tech", "e-power", "e-cvt")),
    (Fuel.ETHANOL, ("ethanol", "e85", "flexfuel", "flex fuel", "flexifuel",
                    "superethanol", "super ethanol", "bioethanol", "ffv")),
    (Fuel.LPG, ("gpl", "lpg", "autogas", "bifuel", "bi-fuel")),
    (Fuel.CNG, ("gnv", "cng", "erdgas", "metano", "tgi")),
    (Fuel.DIESEL, ("diesel", "gasoil", "gazole", "gasolio", "hdi", "tdi", "dci", "cdi",
                   "bluehdi", "crdi", "jtd", "tdci", "d4d", "bluetec", "cdti", "dtec", "tdv6",
                   "multijet", "skyactiv-d", "bitdi", "blue dci")),
    (Fuel.PETROL, ("essence", "benzin", "benzina", "gasolina", "petrol", "gasoline", "tsi",
                   "tfsi", "vti", "thp", "puretech", "mpi", "gdi", "vvt", "turbo essence",
                   "ecoboost", "firefly", "skyactiv-g", "tce", "sce", "turbo t-gdi")),
]

GEARBOX_KEYWORDS: list[tuple[Gearbox, tuple[str, ...]]] = [
    (Gearbox.AUTOMATIC, ("automatique", "automatik", "automatica", "automatic", "auto.",
                         "boite auto", "bva", "dsg", "edc", "eat6", "eat8", "eat 8", "tiptronic",
                         "s tronic", "s-tronic", "steptronic", "powershift", "cvt", "dct",
                         "multitronic", "g-tronic", "7g-tronic", "9g-tronic", "pdk", "zf8",
                         "e-cvt", "xtronic", "dualogic", "easytronic", "automaat")),
    (Gearbox.MANUAL, ("manuelle", "manuell", "manuale", "manual", "bvm", "boite mecanique",
                      "schaltgetriebe", "handgeschakeld", "mecanique", "6 vitesses",
                      "5 vitesses", "6-gang", "5-gang")),
]

BODY_KEYWORDS: list[tuple[BodyType, tuple[str, ...]]] = [
    (BodyType.CABRIO, ("cabriolet", "cabrio", "roadster", "convertible", "decapotable", "spider")),
    (BodyType.COUPE, ("coupe", "fastback")),
    (BodyType.ESTATE, ("break", "sw ", " sw", "kombi", "estate", "touring", "avant", "variant",
                       "familiale", "station wagon", "sportbrake", "shooting brake",
                       "t-modell", "t modell", "sw", "wagon", "familiare",
                       "sports tourer", "sportstourer", "tourer", "turnier")),
    (BodyType.SUV, ("suv", "4x4", "crossover", "gelandewagen", "tout-terrain", "allrad",
                    "quattro suv", "x-drive suv")),
    (BodyType.VAN, ("monospace", "minivan", "mpv", "ludospace", "van ", "combi", "utilitaire",
                    "fourgon", "transporter")),
    (BodyType.PICKUP, ("pick-up", "pickup", "pick up")),
    (BodyType.SEDAN, ("berline", "limousine", "sedan", "saloon", "tricorps")),
    (BodyType.CITY, ("citadine", "kleinwagen", "city car", "microcar")),
    (BodyType.HATCH, ("compacte", "hatchback", "5 portes", "3 portes", "fliessheck")),
]

OPTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "toit_ouvrant": ("toit ouvrant", "toit panoramique", "schiebedach", "panoramadach",
                     "sunroof", "panoramic roof", "openable roof", "tetto apribile"),
    "cuir": ("cuir", "leder", "leather", "pelle", "sellerie cuir"),
    "gps": ("gps", "navigation", "navi", "systeme de navigation"),
    "camera_recul": ("camera de recul", "camera 360", "rueckfahrkamera", "reversing camera",
                     "backup camera", "camera arriere"),
    "radar_recul": ("radar de recul", "aide au stationnement", "parking sensors", "pdc",
                    "park assist", "einparkhilfe"),
    "regulateur_adaptatif": ("regulateur adaptatif", "acc", "adaptive cruise", "tempomat adaptiv"),
    "sieges_chauffants": ("sieges chauffants", "sitzheizung", "heated seats", "sedili riscaldati"),
    "carplay": ("carplay", "android auto", "apple car play"),
    "attelage": ("attelage", "crochet", "anhangerkupplung", "tow bar", "towbar", "gancio traino"),
    "led": ("phares led", "full led", "matrix led", "xenon", "bi-xenon", "laserlight"),
    "hayon_electrique": ("hayon electrique", "coffre electrique", "elektrische heckklappe",
                         "power tailgate"),
    "keyless": ("keyless", "sans cle", "smart key", "acces mains libres"),
    "clim_auto": ("clim automatique", "climatisation automatique", "bi-zone", "quadrizone",
                  "klimaautomatik", "dual zone"),
    "jantes_alu": ("jantes alu", "jantes aluminium", "alufelgen", "alloy wheels"),
    "hud": ("affichage tete haute", "head-up", "head up display", "hud"),
    "pack_sport": ("pack sport", "s-line", "s line", "m sport", "m-sport", "amg line", "gt line",
                   "r-line", "r line", "st-line", "st line", "black edition"),
    "siege_electrique": ("sieges electriques", "siege electrique", "elektrische sitze",
                         "electric seats", "memoire de siege"),
    "attelage_elec": ("attelage electrique", "attelage retractable"),
    "suspension_pilotee": ("suspension pilotee", "amortisseurs pilotes", "air suspension",
                           "suspension pneumatique", "luftfederung", "magnetic ride"),
}

SELLER_PRO_HINTS = ("garage", "concession", "sarl", "sas", "gmbh", "auto", "motors", "automobile",
                    "dealer", "handler", "professionnel", "pro ", "s.r.l", "b.v.")


def _hay(*parts: str | None) -> str:
    """Lowercase, accent-free haystack built from several text fields."""
    return strip_accents(" ".join(p for p in parts if p).lower())


#: Words that turn a powertrain keyword into an equipment mention:
#: `hayon electrique` is a power tailgate, not an electric car.
EQUIPMENT_CONTEXT = (
    "hayon", "vitres", "vitre", "retroviseurs", "retroviseur", "sieges", "siege",
    "toit", "frein", "freins", "direction", "pompe", "chauffage", "cable",
    "prise", "reglage", "ouverture", "fermeture", "coffre", "attelage", "volant",
    "essuie", "antenne", "trappe", "marchepied",
)


def _kw(hay: str, keyword: str, *, not_after: tuple[str, ...] = ()) -> bool:
    """Keyword match on word boundaries, with optional context exclusion.

    Plain `in` would find `sw` inside `volkswagen` and `import` inside
    `importante`, which silently mislabels thousands of listings.
    """
    keyword = keyword.strip()
    for match in re.finditer(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", hay):
        if not_after:
            before = hay[max(0, match.start() - 30) : match.start()].split()
            if before and before[-1] in not_after:
                continue
        return True
    return False


def canonical_make(value: str | None) -> str | None:
    if not value:
        return None
    key = strip_accents(value.strip().lower())
    if key in MAKE_ALIASES:
        return MAKE_ALIASES[key]
    for alias, make in MAKE_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", key):
            return make
    return value.strip().title() or None


def detect_make(text: str | None) -> str | None:
    if not text:
        return None
    hay = _hay(text)
    # Longest alias first so "alfa romeo" wins over "alfa".
    for alias in sorted(MAKE_ALIASES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(alias)}\b", hay):
            return MAKE_ALIASES[alias]
    return None


def canonical_model(make: str | None, text: str | None) -> str | None:
    """Map a free-text model mention onto a stable, language-neutral label."""
    if not text:
        return None
    hay = _hay(text)
    for alias in sorted(MAKE_ALIASES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(alias)}\b", hay):
            hay = re.sub(rf"\b{re.escape(alias)}\b", " ", hay)
            break
    hay = hay.strip()
    for pattern, replacement in MODEL_RULES.get(make or "", []):
        match = re.search(pattern, hay)
        if match:
            label = re.sub(pattern, replacement, match.group(0)) if replacement else match.group(1)
            return label.strip().title()
    for token in hay.split():
        token = token.strip("-.,()")
        if len(token) >= 2 and token not in MODEL_STOPWORDS and not token.isdigit():
            return token.title()
        if token.isdigit() and len(token) in (3, 4):
            return token
    return None


#: German premium naming encodes the fuel in the engine badge: 320d, 220 d,
#: 330e, 320i. Checked only after the explicit keyword tables.
ENGINE_CODE_FUEL: list[tuple[str, Fuel]] = [
    (r"\b\d{3}\s?e\b", Fuel.PHEV),
    (r"\b\d{3}\s?d\b", Fuel.DIESEL),
    (r"\b\d{3}\s?i\b", Fuel.PETROL),
]


#: Fuels whose keywords also appear in equipment names.
_CONTEXT_SENSITIVE = {Fuel.ELECTRIC, Fuel.PHEV, Fuel.HYBRID}


def detect_fuel(*parts: str | None) -> Fuel:
    """Detect the powertrain, giving earlier arguments priority.

    Callers pass the title first: a title saying `1.6 TDI` outranks a
    description listing `hayon electrique` among the options.
    """
    result = Fuel.UNKNOWN
    for part in parts:
        if not part:
            continue
        found = _detect_fuel_one(_hay(part))
        if found is Fuel.UNKNOWN:
            continue
        if result is Fuel.UNKNOWN:
            result = found
        # `Hybrid 225` in a title is often a plug-in: a later, more specific
        # mention upgrades the guess rather than being ignored.
        if result is Fuel.HYBRID and found is Fuel.PHEV:
            return Fuel.PHEV
        if result is not Fuel.HYBRID:
            return result
    return result


def _detect_fuel_one(hay: str) -> Fuel:
    for fuel, keywords in FUEL_KEYWORDS:
        not_after = EQUIPMENT_CONTEXT if fuel in _CONTEXT_SENSITIVE else ()
        if any(_kw(hay, k, not_after=not_after) for k in keywords):
            return fuel
    for pattern, fuel in ENGINE_CODE_FUEL:
        if re.search(pattern, hay):
            return fuel
    return Fuel.UNKNOWN


def detect_gearbox(*parts: str | None) -> Gearbox:
    hay = _hay(*parts)
    for gearbox, keywords in GEARBOX_KEYWORDS:
        if any(_kw(hay, k) for k in keywords):
            return gearbox
    return Gearbox.UNKNOWN


def detect_body(*parts: str | None) -> BodyType:
    hay = _hay(*parts)
    for body, keywords in BODY_KEYWORDS:
        if any(_kw(hay, k) for k in keywords):
            return body
    return BodyType.UNKNOWN


def detect_options(*parts: str | None) -> list[str]:
    hay = _hay(*parts)
    return sorted(
        {code for code, keywords in OPTION_KEYWORDS.items() if any(_kw(hay, k) for k in keywords)}
    )


def detect_seller_type(name: str | None, description: str | None = None) -> SellerType:
    hay = _hay(name, description)
    if not hay:
        return SellerType.UNKNOWN
    if any(_kw(hay, hint) for hint in SELLER_PRO_HINTS):
        return SellerType.PRO
    if "particulier" in hay or "privat" in hay or "private" in hay:
        return SellerType.PRIVATE
    return SellerType.UNKNOWN


def is_premium(make: str | None) -> bool:
    return (make or "") in PREMIUM_MAKES


def enrich(listing: ListingData) -> ListingData:
    """Fill in every field we can infer from the advert's own text.

    Adapter-provided values always win; this only fills the blanks.
    """
    listing.title = clean_text(listing.title) or listing.title
    listing.description = clean_text(listing.description)
    text = f"{listing.title} {listing.description or ''}"

    listing.make = canonical_make(listing.make) or detect_make(text)
    if not listing.model:
        listing.model = canonical_model(listing.make, listing.title)
    else:
        listing.model = canonical_model(listing.make, listing.model) or listing.model

    if listing.fuel is Fuel.UNKNOWN:
        listing.fuel = detect_fuel(listing.title, listing.version, listing.description)
    if listing.gearbox is Gearbox.UNKNOWN:
        listing.gearbox = detect_gearbox(listing.title, listing.version, listing.description)
    if listing.body is BodyType.UNKNOWN:
        listing.body = detect_body(listing.title, listing.version, listing.description)
    if listing.power_hp is None:
        hay = strip_accents(text.lower())
        match = re.search(r"(\d{2,4})\s*(ch|cv|hp|ps|bhp|kw)\b", hay)
        if match:
            listing.power_hp = parse_power_hp(match.group(0))
        else:
            # "TCe 100", "TDI 190", "BlueHDi 120": the badge is followed by the power.
            badge = re.search(
                r"\b(tce|sce|tdi|hdi|bluehdi|dci|tsi|tfsi|thp|puretech|crdi|cdti|tdci"
                r"|ecoboost|multijet|jtd|d-4d|skyactiv-[gd]|etech|e-tech)\s*[- ]?(\d{2,3})\b",
                hay,
            )
            if badge:
                listing.power_hp = parse_power_hp(badge.group(2))
    if listing.km is None:
        match = re.search(r"([\d.,\s]{2,12})\s*(km|miles|mi)\b", strip_accents(text.lower()))
        if match:
            listing.km = parse_km(match.group(0))
    if listing.year is None:
        listing.year = parse_year(listing.title) or (
            listing.first_registration.year if listing.first_registration else None
        )
    if listing.first_registration is None and listing.year:
        listing.year = listing.year
    if listing.seller_type is SellerType.UNKNOWN:
        listing.seller_type = detect_seller_type(listing.seller_name, listing.description)

    detected = detect_options(listing.title, listing.version, listing.description,
                             " ".join(listing.options))
    listing.options = sorted(set(listing.options) | set(detected))
    listing.fill_price_eur()
    return listing
