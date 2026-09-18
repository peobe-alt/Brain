"""Recognising the same car twice.

The same vehicle is routinely posted on three sites at three prices, and a
dealer will repost it every few weeks with a new advert id. Both cases must
collapse onto one car, otherwise the comparables pool counts one vehicle
several times and the whole market estimate drifts.
"""

from __future__ import annotations

import hashlib

from ..schemas import ListingData

def fingerprint(listing: ListingData) -> str:
    """Stable identity for a physical vehicle, across sites and reposts.

    The odometer reading is taken exactly, not bucketed. Bucketing looked
    safer and is in fact the dangerous choice: on a market of two hundred
    identical models the mileages are dense, so any tolerance merges cars
    that merely resemble each other. Dropping a real comparable costs more
    than missing a cross-post, because it silently shrinks the sample the
    whole estimate rests on.

    Two genuinely different cars can still collide (round mileages cluster
    at 100 000 km), but a collision requires identical make, model, year,
    fuel, power and odometer: such cars are worth nearly the same, so
    keeping only the cheaper one barely moves the median.
    """
    parts = [
        (listing.make or "?").lower(),
        (listing.model or "?").lower(),
        str(listing.year or "?"),
        str(listing.km if listing.km is not None else "?"),
        listing.fuel.value,
        str(listing.power_hp or "?"),
    ]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:24]


def looks_like_same_car(a: ListingData, b: ListingData) -> bool:
    """Fuzzier check used when fingerprints disagree by one field."""
    if (a.make, a.model, a.year) != (b.make, b.model, b.year):
        return False
    if a.km is not None and b.km is not None and abs(a.km - b.km) > 3_000:
        return False
    if a.power_hp and b.power_hp and a.power_hp != b.power_hp:
        return False
    if a.price_eur and b.price_eur:
        cheaper = min(a.price_eur, b.price_eur)
        if cheaper and abs(a.price_eur - b.price_eur) / cheaper > 0.15:
            return False
    return True


def group_by_vehicle(rows: list) -> list[tuple[object, list[object]]]:
    """Group database rows by physical vehicle.

    Returns `(best, others)` pairs, best first by score then by price. The
    same car on three sites is one entry in a results list, not three, and
    the price spread between those sites is itself worth knowing: it says
    where to buy, and how much room the seller has.
    """
    groups: dict[str, list] = {}
    for row in rows:
        key = getattr(row, "fingerprint", None) or f"id:{getattr(row, 'id', id(row))}"
        groups.setdefault(key, []).append(row)

    output: list[tuple[object, list[object]]] = []
    for members in groups.values():
        members.sort(key=lambda r: (-(r.score or 0), r.price_eur or float("inf")))
        output.append((members[0], members[1:]))
    output.sort(key=lambda pair: -(pair[0].score or 0))
    return output
