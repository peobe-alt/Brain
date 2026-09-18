"""Finding the right comparables, and knowing when there aren't any.

The estimate is only as good as the sample it rests on, so selection runs in
tiers: start strict (same version, same year, similar mileage, same country)
and widen only as far as needed to reach a usable sample. The tier that was
actually used is reported, because `same model, any country, +/-3 years` is a
much weaker statement than `same trim, same year` and the score must know it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Sequence

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from ..db import Listing
from ..normalize.text import strip_accents
from ..schemas import BodyType, Fuel, Gearbox, ListingData, SellerType


@dataclass(slots=True)
class Facts:
    """The handful of attributes valuation actually needs."""

    make: str | None
    model: str | None
    year: int | None
    age_years: float | None
    km: int | None
    fuel: Fuel
    gearbox: Gearbox
    body: str
    options: list[str]
    price_eur: float | None
    country: str
    seller_type: SellerType
    #: Engine output and finish label: what separates two cars that share a
    #: model name and nothing else about their price.
    power_hp: int | None = None
    version: str | None = None
    listing_id: int | None = None
    url: str = ""
    title: str = ""
    #: Identity of the physical vehicle, shared across sites and reposts.
    fingerprint: str = ""


def to_facts(obj: ListingData | Listing) -> Facts:
    from ..pipeline.dedupe import fingerprint as compute_fingerprint

    if isinstance(obj, ListingData):
        return Facts(
            make=obj.make, model=obj.model, year=obj.year, age_years=obj.age_years,
            km=obj.km, fuel=obj.fuel, gearbox=obj.gearbox, body=obj.body.value,
            options=list(obj.options), price_eur=obj.price_eur, country=obj.country,
            seller_type=obj.seller_type, power_hp=obj.power_hp, version=obj.version,
            url=obj.url, title=obj.title,
            fingerprint=compute_fingerprint(obj),
        )
    age = None
    if obj.year:
        age = max(0.0, date.today().year - obj.year + 0.5)
    return Facts(
        make=obj.make, model=obj.model, year=obj.year, age_years=age, km=obj.km,
        fuel=Fuel(obj.fuel), gearbox=Gearbox(obj.gearbox), body=obj.body,
        options=list(obj.options or []), price_eur=obj.price_eur, country=obj.country,
        seller_type=SellerType(obj.seller_type), power_hp=obj.power_hp,
        version=obj.version, listing_id=obj.id, url=obj.url,
        title=obj.title, fingerprint=obj.fingerprint or "",
    )


@dataclass(frozen=True, slots=True)
class Tier:
    """One rung of the ladder, and what standing on it is worth."""

    name: str
    confidence: float
    year_tol: int
    km_tol: float
    #: Relative tolerance on engine output. `None` drops the constraint.
    power_tol: float | None
    match_fuel: bool
    match_gearbox: bool
    match_body: bool
    match_trim: bool
    same_country: bool


#: The widening order matters as much as the widths.
#:
#: Geography is given up early because `country_factor` already restates a
#: foreign price onto the local market: a German comparable is a real one,
#: merely converted. The engine, the body and the finish are held as long as
#: possible, because nothing restates a car that is simply not the same car.
#: A 110 hp Scenic and a 160 hp Scenic share a badge and little else, and a
#: break is not a hatchback at any price.
TIERS: list[Tier] = [
    #    nom                conf   an   km   puiss  carb   boite  carr   finit  pays
    Tier("strict",          1.00,  1, 0.30,  0.12,  True,  True,  True,  True,  True),
    Tier("finition",        0.92,  2, 0.50,  0.20,  True,  False, True,  True,  True),
    Tier("motorisation",    0.85,  3, 0.60,  0.25,  True,  False, True,  False, True),
    Tier("motorisation_eu", 0.72,  3, 0.70,  0.25,  True,  False, True,  False, False),
    Tier("modele",          0.55,  3, 0.70,  0.40,  False, False, False, False, False),
    Tier("modele_large",    0.40,  5, 1.00,  0.55,  False, False, False, False, False),
]


#: Words that sit in a version string without naming the finish level: body
#: shapes, engine badges, gearboxes, drivetrains. Without stripping them,
#: every BlueHDi would count as the same trim as every other BlueHDi.
TRIM_NOISE = frozenset({
    "sw", "break", "touring", "avant", "variant", "estate", "kombi", "sportback",
    "coupe", "cabriolet", "cabrio", "berline", "hatchback", "limousine", "tourer",
    "tdi", "tsi", "tfsi", "hdi", "bluehdi", "dci", "cdi", "crdi", "cdti", "tdci",
    "puretech", "tce", "sce", "thp", "vti", "multijet", "jtd", "ecoboost",
    "skyactiv", "vvt-i", "vvti", "mpi", "gdi", "ecog",
    "hybrid", "hybride", "phev", "bev", "etech", "e-tech", "blue", "eco-g",
    "bva", "bvm", "dsg", "edc", "eat6", "eat8", "cvt", "dct", "tiptronic",
    "xdrive", "quattro", "4matic", "awd", "4x4", "2wd", "fwd",
    "km", "ch", "cv", "kw", "occasion",
})


def trim_tokens(version: str | None) -> frozenset[str]:
    """The words of a version that actually name the finish level."""
    if not version:
        return frozenset()
    words = re.findall(r"[a-z0-9-]+", strip_accents(version.lower()))
    return frozenset(
        word
        for word in words
        if len(word) > 1 and word not in TRIM_NOISE and not word.replace("-", "").isdigit()
    )


def same_trim(target: frozenset[str], candidate: frozenset[str]) -> bool:
    """Do these two versions name the same finish?

    Silence is not disagreement: a great many adverts carry no version at
    all, and discarding them would cost far more comparables than the
    occasional Allure counted against a GT.
    """
    return not target or not candidate or bool(target & candidate)


def find_comparables(
    session: Session,
    target: Facts,
    *,
    min_count: int = 8,
    max_age_days: int = 120,
    exclude_ids: Sequence[int] = (),
) -> tuple[list[Facts], str]:
    """Return the tightest comparable set that reaches `min_count`."""
    if not target.make or not target.model:
        return [], "aucun"

    best: list[Facts] = []
    best_tier = "aucun"
    seen_since = datetime.utcnow() - timedelta(days=max_age_days)

    target_trim = trim_tokens(target.version)

    for tier in TIERS:
        conditions: list[Any] = [
            Listing.make == target.make,
            Listing.model == target.model,
            Listing.price_eur.is_not(None),
            Listing.price_eur > 500,
            Listing.last_seen >= seen_since,
        ]
        if target.listing_id:
            conditions.append(Listing.id != target.listing_id)
        if exclude_ids:
            conditions.append(Listing.id.not_in(list(exclude_ids)))
        if target.year:
            conditions.append(
                Listing.year.between(target.year - tier.year_tol, target.year + tier.year_tol)
            )
        if target.km:
            low = int(target.km * (1 - tier.km_tol))
            high = int(target.km * (1 + tier.km_tol)) + 15_000
            conditions.append(Listing.km.between(low, high))
        if tier.match_fuel and target.fuel is not Fuel.UNKNOWN:
            conditions.append(Listing.fuel == target.fuel.value)
        if tier.match_gearbox and target.gearbox is not Gearbox.UNKNOWN:
            conditions.append(Listing.gearbox == target.gearbox.value)
        if tier.match_body and target.body != BodyType.UNKNOWN.value:
            # An advert whose body we could not read is not evidence that it
            # differs: a blank is silence, not a contradiction.
            conditions.append(
                or_(Listing.body == BodyType.UNKNOWN.value, Listing.body == target.body)
            )
        if tier.power_tol is not None and target.power_hp:
            # Absolute floor: 12% of a 65 hp city car is 8 hp, which would
            # separate two adverts for the same engine rounded from kW.
            margin = max(10.0, target.power_hp * tier.power_tol)
            conditions.append(
                or_(
                    Listing.power_hp.is_(None),
                    Listing.power_hp.between(
                        int(target.power_hp - margin), int(target.power_hp + margin)
                    ),
                )
            )
        if tier.same_country:
            conditions.append(Listing.country == target.country)

        rows = session.execute(select(Listing).where(and_(*conditions)).limit(400)).scalars().all()
        found = [to_facts(row) for row in rows]
        if tier.match_trim and target_trim:
            found = [fact for fact in found if same_trim(target_trim, trim_tokens(fact.version))]
        candidates = deduplicate(found, exclude_fingerprint=target.fingerprint)
        if len(candidates) >= min_count:
            return candidates, tier.name
        if len(candidates) > len(best):
            best, best_tier = candidates, tier.name

    return best, best_tier if best else "aucun"


def deduplicate(candidates: list[Facts], *, exclude_fingerprint: str = "") -> list[Facts]:
    """One physical vehicle, one vote.

    The same car is routinely posted on three sites at three prices, and
    dealers cross-post far more than private sellers. Counting each copy
    would let a handful of vehicles dominate the sample, and since dealer
    prices are the higher ones the bias is systematically upward: every
    advert would look like a better deal than it is. Where a vehicle appears
    several times, the cheapest listing wins, because that is the price at
    which it can actually be bought.
    """
    best: dict[str, Facts] = {}
    unidentified: list[Facts] = []
    for candidate in candidates:
        key = candidate.fingerprint
        if not key:
            unidentified.append(candidate)
            continue
        if exclude_fingerprint and key == exclude_fingerprint:
            continue            # the target advertised elsewhere is not a comparable
        current = best.get(key)
        if current is None or (candidate.price_eur or 0) < (current.price_eur or 0):
            best[key] = candidate
    return list(best.values()) + unidentified


#: Read off TIERS, so a rung's confidence can never drift from its width.
_TIER_CONFIDENCE: dict[str, float] = {tier.name: tier.confidence for tier in TIERS}


def tier_confidence(tier: str) -> float:
    """How much the selection tier itself deserves to be trusted."""
    if tier == "aucun":
        return 0.0
    return _TIER_CONFIDENCE.get(tier, 0.5)
