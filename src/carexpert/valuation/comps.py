"""Finding the right comparables, and knowing when there aren't any.

The estimate is only as good as the sample it rests on, so selection runs in
tiers: start strict (same version, same year, similar mileage, same country)
and widen only as far as needed to reach a usable sample. The tier that was
actually used is reported, because `same model, any country, +/-3 years` is a
much weaker statement than `same trim, same year` and the score must know it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Sequence

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from ..db import Listing
from ..schemas import Fuel, Gearbox, ListingData, SellerType


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
    listing_id: int | None = None
    url: str = ""
    title: str = ""


def to_facts(obj: ListingData | Listing) -> Facts:
    if isinstance(obj, ListingData):
        return Facts(
            make=obj.make, model=obj.model, year=obj.year, age_years=obj.age_years,
            km=obj.km, fuel=obj.fuel, gearbox=obj.gearbox, body=obj.body.value,
            options=list(obj.options), price_eur=obj.price_eur, country=obj.country,
            seller_type=obj.seller_type, url=obj.url, title=obj.title,
        )
    age = None
    if obj.year:
        age = max(0.0, date.today().year - obj.year + 0.5)
    return Facts(
        make=obj.make, model=obj.model, year=obj.year, age_years=age, km=obj.km,
        fuel=Fuel(obj.fuel), gearbox=Gearbox(obj.gearbox), body=obj.body,
        options=list(obj.options or []), price_eur=obj.price_eur, country=obj.country,
        seller_type=SellerType(obj.seller_type), listing_id=obj.id, url=obj.url,
        title=obj.title,
    )


#: (tier name, year tolerance, mileage tolerance, match fuel, match gearbox, same country)
TIERS: list[tuple[str, int, float, bool, bool, bool]] = [
    ("strict",          1, 0.30, True,  True,  True),
    ("modele_carburant", 2, 0.50, True,  False, True),
    ("modele",          3, 0.70, False, False, True),
    ("modele_europe",   3, 0.70, False, False, False),
    ("modele_large",    5, 1.00, False, False, False),
]


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

    for tier, year_tol, km_tol, match_fuel, match_gearbox, same_country in TIERS:
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
            conditions.append(Listing.year.between(target.year - year_tol, target.year + year_tol))
        if target.km:
            low = int(target.km * (1 - km_tol))
            high = int(target.km * (1 + km_tol)) + 15_000
            conditions.append(Listing.km.between(low, high))
        if match_fuel and target.fuel is not Fuel.UNKNOWN:
            conditions.append(Listing.fuel == target.fuel.value)
        if match_gearbox and target.gearbox is not Gearbox.UNKNOWN:
            conditions.append(Listing.gearbox == target.gearbox.value)
        if same_country:
            conditions.append(Listing.country == target.country)

        rows = session.execute(select(Listing).where(and_(*conditions)).limit(400)).scalars().all()
        candidates = [to_facts(row) for row in rows]
        if len(candidates) >= min_count:
            return candidates, tier
        if len(candidates) > len(best):
            best, best_tier = candidates, tier

    return best, best_tier if best else "aucun"


def tier_confidence(tier: str) -> float:
    """How much the selection tier itself deserves to be trusted."""
    return {
        "strict": 1.0,
        "modele_carburant": 0.9,
        "modele": 0.75,
        "modele_europe": 0.6,
        "modele_large": 0.4,
        "aucun": 0.0,
    }.get(tier, 0.5)
