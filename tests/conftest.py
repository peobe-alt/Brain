"""Every test runs against its own throwaway database."""

from __future__ import annotations

import pytest


@pytest.fixture()
def session(tmp_path, monkeypatch):
    monkeypatch.setenv("CAREXPERT_DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("CAREXPERT_CACHE_DIR", str(tmp_path / "cache"))

    from carexpert import config, db

    config.get_settings.cache_clear()
    db.reset_engine()
    db.init_db()
    with db.session_scope() as active:
        yield active
    config.get_settings.cache_clear()
    db.reset_engine()


@pytest.fixture()
def demo_market(session):
    """A few hundred synthetic adverts, already ingested."""
    from carexpert.pipeline.ingest import ingest
    from carexpert.schemas import SearchQuery
    from carexpert.sources.demo import DemoSource

    listings = list(
        DemoSource(seed=11, size=260).search(
            SearchQuery(limit=260, countries=["FR", "DE", "IT", "BE", "ES", "NL"])
        )
    )
    ingest(session, listings)
    session.flush()
    return session
