"""Getting adverts into the database, once, with their price history."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable

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
        created = row is None
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

        # Une annonce qui affiche son ancien prix nous donne son historique
        # d'avance. Sans ca, une baisse de prix n'est visible qu'au deuxieme
        # passage, c'est-a-dire souvent le lendemain, c'est-a-dire trop tard.
        _seed_previous_price(session, row, listing, created=created)

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


def _empty(value: Any) -> bool:
    """Un champ que la page en cours ne porte pas."""
    if value in (None, "", []):
        return True
    return getattr(value, "value", value) == "unknown"


def _fill(row: Listing, name: str, value: Any) -> None:
    """Ecrire, sauf pour effacer ce qu'on sait deja.

    La meme annonce arrive tantot par la page de resultats, tantot par sa
    fiche, et les deux ne portent pas les memes champs: la liste ignore le
    descriptif, la fiche ignore souvent le code postal. Ecraser l'un par
    l'autre perd de l'information dans les deux sens.
    """
    if _empty(value):
        return
    setattr(row, name, value)


def _apply(row: Listing, listing: ListingData) -> None:
    # Toujours: ce que toute page porte, et ce qui doit suivre le vendeur.
    row.fingerprint = fingerprint(listing)
    row.url = listing.url
    row.price_eur = listing.price_eur
    row.currency = listing.currency
    row.country = listing.country
    row.last_seen = datetime.utcnow()
    row.active = True

    # Le reste ne s'ecrase pas avec du vide. Mesure: apres avoir ouvert trois
    # annonces pour en lire le descriptif, un simple retour sur la page de
    # resultats remettait les trois descriptifs a zero - donc les pieges
    # qu'ils contenaient. Vingt annonces en base, zero descriptif.
    for name, value in (
        ("title", listing.title),
        ("description", listing.description),
        ("make", listing.make),
        ("model", listing.model),
        ("version", listing.version),
        ("year", listing.year),
        ("km", listing.km),
        ("fuel", listing.fuel.value),
        ("gearbox", listing.gearbox.value),
        ("power_hp", listing.power_hp),
        ("body", listing.body.value),
        ("owners", listing.owners),
        ("color", listing.color),
        ("seller_type", listing.seller_type.value),
        ("seller_name", listing.seller_name),
        ("city", listing.city),
        ("postcode", listing.postcode),
        ("posted_at", listing.posted_at),
    ):
        _fill(row, name, value)

    # Photos et options: on garde la version la plus riche des deux.
    photos = [photo.model_dump() for photo in listing.photos]
    if len(photos) >= len(row.photos or []):
        row.photos = photos
    row.options = sorted(set(row.options or []) | set(listing.options))
    row.raw = {**(row.raw or {}), **listing.extra,
               "version": listing.version or (row.raw or {}).get("version")}


def _seed_previous_price(
    session: Session, row: Listing, listing: ListingData, *, created: bool
) -> None:
    """Record the price the advert says it used to ask, once."""
    previous = listing.extra.get("previous_price")
    if not created or not previous or not listing.price_eur:
        return
    try:
        previous = float(previous)
    except (TypeError, ValueError):
        return
    if previous <= listing.price_eur:
        return
    session.add(
        Pricepoint(
            listing_id=row.id,
            price_eur=previous,
            seen_at=listing.scraped_at - timedelta(seconds=1),
        )
    )
    log.info(
        "%s: baisse de prix annoncee sur l'annonce, %.0f -> %.0f EUR",
        listing.source, previous, listing.price_eur,
    )


#: Champs qu'une page d'annonce peut legitimement ne pas repeter alors que
#: la page de resultats, elle, les portait.
def complete(base: ListingData, detail: ListingData) -> ListingData:
    """La page d'annonce fait foi, la liste bouche ses trous.

    Une page de resultats donne souvent le code postal et la date de mise en
    circulation que la page d'annonce n'expose pas en schema.org. Ecraser
    l'une par l'autre perd de l'information dans les deux sens; on garde donc
    la plus riche des deux valeurs, champ par champ.
    """
    merged = detail.model_copy(deep=True)
    for name, value in base.model_dump().items():
        if name in ("scraped_at", "extra", "photos", "options"):
            continue
        if value in (None, "", []) :
            continue
        current = getattr(merged, name, None)
        if current in (None, "", []) or getattr(current, "value", current) == "unknown":
            setattr(merged, name, getattr(base, name))
    if len(base.photos) > len(merged.photos):
        merged.photos = list(base.photos)
    merged.options = sorted(set(base.options) | set(merged.options))
    merged.extra = {**base.extra, **detail.extra}
    return merged


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
        postcode=row.postcode,
        photos=[Photo(**photo) for photo in (row.photos or []) if isinstance(photo, dict)],
        posted_at=row.posted_at,
        extra=dict(row.raw or {}),
    )
