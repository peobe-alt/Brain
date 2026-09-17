"""Standing searches: what you are hunting for, and when to be told."""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import Alert, Listing, Watchlist
from ..schemas import SearchQuery

log = logging.getLogger(__name__)


def create_watchlist(
    session: Session,
    name: str,
    query: SearchQuery,
    *,
    sources: list[str] | None = None,
    min_score: int = 75,
    channels: list[str] | None = None,
) -> Watchlist:
    existing = session.execute(
        select(Watchlist).where(Watchlist.name == name)
    ).scalar_one_or_none()
    payload = query.model_dump(mode="json")
    if existing:
        existing.query = payload
        existing.sources = sources or existing.sources
        existing.min_score = min_score
        existing.channels = channels or existing.channels
        existing.active = True
        return existing
    watchlist = Watchlist(
        name=name,
        query=payload,
        sources=sources or ["demo"],
        min_score=min_score,
        channels=channels or ["console"],
    )
    session.add(watchlist)
    session.flush()
    return watchlist


def matches(watchlist: Watchlist, listing: Listing) -> bool:
    """Does this listing belong to that hunt?"""
    query = SearchQuery(**watchlist.query)
    if query.make and (listing.make or "").lower() != query.make.lower():
        return False
    if query.model and (listing.model or "").lower() != query.model.lower():
        return False
    if query.price_max and (listing.price_eur or 0) > query.price_max:
        return False
    if query.price_min and (listing.price_eur or 0) < query.price_min:
        return False
    if query.year_min and (listing.year or 0) < query.year_min:
        return False
    if query.km_max and (listing.km or 0) > query.km_max:
        return False
    if query.fuel and listing.fuel != query.fuel.value:
        return False
    if query.countries and listing.country not in query.countries:
        return False
    if watchlist.sources and listing.source not in watchlist.sources:
        return False
    return True


def already_alerted(session: Session, watchlist_id: int, listing_id: int) -> bool:
    return session.execute(
        select(Alert.id).where(
            Alert.watchlist_id == watchlist_id, Alert.listing_id == listing_id
        )
    ).first() is not None


def record_alert(
    session: Session, watchlist: Watchlist, listing: Listing, score: int,
    channels: list[str], payload: dict,
) -> Alert:
    alert = Alert(
        watchlist_id=watchlist.id,
        listing_id=listing.id,
        score=score,
        channels=channels,
        payload=payload,
    )
    session.add(alert)
    watchlist.last_run_at = datetime.utcnow()
    session.flush()
    return alert
