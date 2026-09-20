"""Every test runs against its own throwaway database."""

from __future__ import annotations

import re

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


def plain_class_selectors(css: str) -> list[str]:
    """Les selecteurs d'une seule classe, hors @media.

    Une regle sous @media redefinit volontairement une classe pour un petit
    ecran: ce n'est pas une collision, c'est le but.
    """
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)

    found: list[str] = []
    depth = 0
    skip_until = None
    for header, brace in re.findall(r"([^{}]*)([{}])", css):
        if brace == "}":
            depth -= 1
            if skip_until is not None and depth <= skip_until:
                skip_until = None
            continue
        depth += 1
        header = header.strip()
        if header.startswith("@"):
            if skip_until is None:
                skip_until = depth - 1
            continue
        if skip_until is not None:
            continue
        for selector in header.split(","):
            selector = selector.strip()
            if re.fullmatch(r"\.[a-z][\w-]*", selector):
                found.append(selector)
    return found


def classes_defined_twice(css: str) -> list[str]:
    """Class names a stylesheet styles in two separate rules."""
    selectors = plain_class_selectors(css)
    return sorted({name for name in selectors if selectors.count(name) > 1})
