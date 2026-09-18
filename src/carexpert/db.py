"""SQLAlchemy models and session helpers.

SQLite by default so the tool runs with zero infrastructure; point
`CAREXPERT_DATABASE_URL` at Postgres when the crawl volume grows.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    inspect,
    select,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from .config import get_settings

log = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


class Listing(Base):
    """A vehicle advert as last seen on a source site."""

    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_source_listing"),
        # Comparables are always looked up by make and model together.
        Index("ix_listings_make_model", "make", "model"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)

    source: Mapped[str] = mapped_column(String(40), index=True)
    source_id: Mapped[str] = mapped_column(String(120))
    url: Mapped[str] = mapped_column(Text)
    country: Mapped[str] = mapped_column(String(2), default="FR", index=True)

    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    price_eur: Mapped[float | None] = mapped_column(Float, index=True, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")

    make: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    model: Mapped[str | None] = mapped_column(String(60), index=True, nullable=True)
    version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    km: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    fuel: Mapped[str] = mapped_column(String(12), default="unknown", index=True)
    gearbox: Mapped[str] = mapped_column(String(12), default="unknown")
    power_hp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    body: Mapped[str] = mapped_column(String(12), default="unknown")
    owners: Mapped[int | None] = mapped_column(Integer, nullable=True)
    color: Mapped[str | None] = mapped_column(String(30), nullable=True)

    seller_type: Mapped[str] = mapped_column(String(10), default="unknown")
    seller_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    city: Mapped[str | None] = mapped_column(String(80), nullable=True)
    postcode: Mapped[str | None] = mapped_column(String(12), nullable=True)

    photos: Mapped[list] = mapped_column(JSON, default=list)
    options: Mapped[list] = mapped_column(JSON, default=list)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)

    posted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    #: Set whenever the asking price changes. Compared against `analyzed_at`
    #: to decide if a valuation is stale, which keeps the rule correct however
    #: the advert got into the database.
    price_changed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    # Denormalised results, kept on the row so the dashboard can sort cheaply.
    score: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    fair_price_eur: Mapped[float | None] = mapped_column(Float, nullable=True)
    delta_pct: Mapped[float | None] = mapped_column(Float, index=True, nullable=True)
    verdict: Mapped[str | None] = mapped_column(String(10), nullable=True)
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    prices: Mapped[list["Pricepoint"]] = relationship(
        back_populates="listing", cascade="all, delete-orphan"
    )
    valuation: Mapped["Valuation | None"] = relationship(
        back_populates="listing", cascade="all, delete-orphan", uselist=False
    )
    analysis: Mapped["Analysis | None"] = relationship(
        back_populates="listing", cascade="all, delete-orphan", uselist=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<Listing {self.source}:{self.source_id} {self.title!r} {self.price_eur}EUR>"


class Pricepoint(Base):
    """Price history: a drop is one of the strongest buy signals there is."""

    __tablename__ = "price_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"), index=True)
    price_eur: Mapped[float] = mapped_column(Float)
    seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    listing: Mapped[Listing] = relationship(back_populates="prices")


class Valuation(Base):
    """Market estimate produced from comparable listings."""

    __tablename__ = "valuations"

    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), index=True, unique=True
    )
    fair_price_eur: Mapped[float] = mapped_column(Float)
    low_eur: Mapped[float] = mapped_column(Float)
    high_eur: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    comps_count: Mapped[int] = mapped_column(Integer)
    method: Mapped[str] = mapped_column(String(40))
    delta_eur: Mapped[float] = mapped_column(Float)
    delta_pct: Mapped[float] = mapped_column(Float)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    listing: Mapped[Listing] = relationship(back_populates="valuation")


class Analysis(Base):
    """Claude's expert report on one advert."""

    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), index=True, unique=True
    )
    model: Mapped[str] = mapped_column(String(60))
    report: Mapped[dict] = mapped_column(JSON)
    condition_score: Mapped[int] = mapped_column(Integer)
    risk_score: Mapped[int] = mapped_column(Integer)
    estimated_repairs_eur: Mapped[int] = mapped_column(Integer, default=0)
    verdict: Mapped[str] = mapped_column(String(10))
    photos_analyzed: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    listing: Mapped[Listing] = relationship(back_populates="analysis")


class Watchlist(Base):
    """A standing search: criteria + the score above which we get pinged."""

    __tablename__ = "watchlists"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    query: Mapped[dict] = mapped_column(JSON)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    min_score: Mapped[int] = mapped_column(Integer, default=75)
    channels: Mapped[list] = mapped_column(JSON, default=lambda: ["console"])
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Alert(Base):
    """One notification actually delivered, so we never ping twice."""

    __tablename__ = "alerts"
    __table_args__ = (UniqueConstraint("watchlist_id", "listing_id", name="uq_alert_once"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    watchlist_id: Mapped[int] = mapped_column(ForeignKey("watchlists.id", ondelete="CASCADE"))
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"))
    score: Mapped[int] = mapped_column(Integer)
    channels: Mapped[list] = mapped_column(JSON, default=list)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


_engine = None
_SessionFactory: sessionmaker[Session] | None = None


def get_engine():
    global _engine
    if _engine is None:
        url = get_settings().database_url
        kwargs: dict[str, Any] = {"future": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        _engine = create_engine(url, **kwargs)
    return _engine


#: Colonnes ajoutees apres coup. `create_all` cree les tables manquantes mais
#: jamais les colonnes manquantes d'une table existante: sans ce rattrapage,
#: une base deja constituee casse au premier scan qui ecrit le champ.
LATE_COLUMNS: dict[str, dict[str, str]] = {
    "listings": {"postcode": "VARCHAR(12)"},
}


def init_db() -> None:
    engine = get_engine()
    Base.metadata.create_all(engine)
    _add_late_columns(engine)


def _add_late_columns(engine) -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    with engine.begin() as connection:
        for table, columns in LATE_COLUMNS.items():
            if table not in tables:
                continue
            existing = {column["name"] for column in inspector.get_columns(table)}
            for name, sql_type in columns.items():
                if name in existing:
                    continue
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))
                log.info("base de donnees: colonne %s.%s ajoutee", table, name)


def get_session_factory() -> sessionmaker[Session]:
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return _SessionFactory


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commit on success, roll back on error."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine() -> None:
    """Drop cached engine/session factory (used by tests switching databases)."""
    global _engine, _SessionFactory
    _engine = None
    _SessionFactory = None


__all__ = [
    "Alert",
    "Analysis",
    "Base",
    "Listing",
    "Pricepoint",
    "Valuation",
    "Watchlist",
    "get_engine",
    "init_db",
    "reset_engine",
    "select",
    "session_scope",
]
