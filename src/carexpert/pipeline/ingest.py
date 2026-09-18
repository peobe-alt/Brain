"""Getting adverts into the database, once, with their price history."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import Listing, Pricepoint
from ..normalize import enrich
from ..schemas import ListingData
from .dedupe import fingerprint

log = logging.getLogger(__name__)


@dataclass
class IngestStats:
    seen: int = 0
    created: int = 0
    updated: int = 0
    price_drops: list[tuple[str, float, float]] = field(default_factory=list)
    skipped: int = 0
    #: Rows that are new or whose price moved: they must be re-valued now,
    #: whatever the freshness rule says.
    touched_ids: set[int] = field(default_factory=set)

    def summary(self) -> str:
        drops = len(self.price_drops)
        return (
            f"{self.seen} annonces vues, {self.created} nouvelles, "
            f"{self.updated} mises a jour, {drops} baisses de prix, "
            f"{self.skipped} ignorees"
        )


def ingest(session: Session, listings: Iterable[ListingData]) -> IngestStats:
    """Upsert adverts, recording every price change we observe."""
    stats = IngestStats()
    for listing in listings:
        stats.seen += 1
        enrich(listing)
        if not listing.price_eur or not listing.title:
            stats.skipped += 1
            continue

        row = session.execute(
            select(Listing).where(
                Listing.source == listing.source, Listing.source_id == listing.source_id
            )
        ).scalar_one_or_none()

        price_moved = False
        if row is None:
            row = Listing(
                fingerprint=fingerprint(listing),
                source=listing.source,
                source_id=listing.source_id,
                url=listing.url,
                country=listing.country,
                first_seen=listing.scraped_at,
            )
            session.add(row)
            stats.created += 1
            price_moved = True
        else:
            stats.updated += 1
            if row.price_eur and listing.price_eur and abs(listing.price_eur - row.price_eur) > 1:
                price_moved = True
                if listing.price_eur < row.price_eur:
                    stats.price_drops.append((listing.url, row.price_eur, listing.price_eur))

        _apply(row, listing)
        if price_moved:
            row.price_changed_at = datetime.utcnow()
        session.flush()
        if price_moved:
            stats.touched_ids.add(row.id)

        last_price = session.execute(
            select(Pricepoint)
            .where(Pricepoint.listing_id == row.id)
            .order_by(Pricepoint.seen_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if last_price is None or abs(last_price.price_eur - (listing.price_eur or 0)) > 1:
            session.add(Pricepoint(listing_id=row.id, price_eur=listing.price_eur or 0))

    session.flush()
    return stats


def _apply(row: Listing, listing: ListingData) -> None:
    row.fingerprint = fingerprint(listing)
    row.url = listing.url
    row.title = listing.title
    row.description = listing.description
    row.price_eur = listing.price_eur
    row.currency = listing.currency
    row.make = listing.make
    row.model = listing.model
    row.version = listing.version
    row.year = listing.year
    row.km = listing.km
    row.fuel = listing.fuel.value
    row.gearbox = listing.gearbox.value
    row.power_hp = listing.power_hp
    row.body = listing.body.value
    row.owners = listing.owners
    row.color = listing.color
    row.seller_type = listing.seller_type.value
    row.seller_name = listing.seller_name
    row.city = listing.city
    row.country = listing.country
    row.photos = [photo.model_dump() for photo in listing.photos]
    row.options = list(listing.options)
    row.raw = {**listing.extra, "version": listing.version}
    row.posted_at = listing.posted_at
    row.last_seen = datetime.utcnow()
    row.active = True


def mark_stale(session: Session, source: str, older_than: datetime) -> int:
    """Flag adverts we stopped seeing: usually sold, sometimes withdrawn."""
    rows = session.execute(
        select(Listing).where(
            Listing.source == source, Listing.last_seen < older_than, Listing.active.is_(True)
        )
    ).scalars().all()
    for row in rows:
        row.active = False
    return len(rows)


def from_row(row: Listing) -> ListingData:
    """Database row back to the canonical listing the analysers speak."""
    from ..schemas import BodyType, Fuel, Gearbox, Photo, SellerType

    return ListingData(
        source=row.source,
        source_id=row.source_id,
        url=row.url,
        country=row.country,
        title=row.title,
        description=row.description,
        price=row.price_eur,
        currency=row.currency or "EUR",
        price_eur=row.price_eur,
        make=row.make,
        model=row.model,
        version=(row.raw or {}).get("version"),
        year=row.year,
        km=row.km,
        fuel=Fuel(row.fuel),
        gearbox=Gearbox(row.gearbox),
        power_hp=row.power_hp,
        body=BodyType(row.body),
        color=row.color,
        owners=row.owners,
        options=list(row.options or []),
        seller_type=SellerType(row.seller_type),
        seller_name=row.seller_name,
        city=row.city,
        photos=[Photo(**photo) for photo in (row.photos or []) if isinstance(photo, dict)],
        posted_at=row.posted_at,
        extra=dict(row.raw or {}),
    )
