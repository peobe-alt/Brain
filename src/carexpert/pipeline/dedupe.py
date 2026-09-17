"""Recognising the same car twice.

The same vehicle is routinely posted on three sites at three prices, and a
dealer will repost it every few weeks with a new advert id. Both cases must
collapse onto one car, otherwise the comparables pool counts one vehicle
several times and the whole market estimate drifts.
"""

from __future__ import annotations

import hashlib

from ..schemas import ListingData

#: Mileage bucket, in km: two adverts within the same bucket describe the
#: same odometer reading for our purposes.
KM_BUCKET = 2_000


def fingerprint(listing: ListingData) -> str:
    """Stable identity for a physical vehicle, across sites and reposts."""
    parts = [
        (listing.make or "?").lower(),
        (listing.model or "?").lower(),
        str(listing.year or "?"),
        str((listing.km or 0) // KM_BUCKET),
        listing.fuel.value,
        str(listing.power_hp or "?"),
        (listing.color or "?").lower()[:6],
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
