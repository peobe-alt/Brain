"""Read pages the user captured from their own browser.

Why this exists
---------------

leboncoin and La Centrale refuse automated requests, measured twice: HTTP 403
with a DataDome interstitial to a plain request, and a captcha to a real
headless Chromium. That is a no, and the project does not argue with a no.

But there is a difference the anti-bot debate keeps missing. A crawler asks a
site for pages nobody requested. A person searching for a Twingo is *already
looking at the page*: the site served it to them, deliberately, as a visitor.
Reading what is on their screen is not collection, it is not automated
extraction, and no protection is being worked around - the site never had to
decide anything, because a human did the browsing.

So: the person searches on leboncoin in their own browser, with their own
session, at human speed. They save the page, or push it with the bookmarklet.
CarExpert reads it. `embedded.py` already knows how - leboncoin's adverts sit
in the flight payload of that very page, complete with price, mileage, fuel
and postcode.

What this is not
----------------

This does not fetch anything. It has no network code and cannot acquire a
page by itself. If nobody opened the page, there is nothing here to read.
That is the whole point, and it is why this path stays open when the
automated ones are closed.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterator

from ..normalize import enrich
from ..schemas import ListingData
from .structured import (
    extract_from_page,
    extract_listings_from_search,
    extract_opengraph,
    select_field,
)

log = logging.getLogger(__name__)

#: Ce qu'un navigateur ecrit dans la page pour dire d'ou elle vient. Un
#: fichier sauvegarde perd son adresse; la page, elle, la porte encore.
ORIGIN_SELECTORS = (
    ("link[rel=canonical]", "href"),
    ("meta[property='og:url']", "content"),
    ("base", "href"),
)

CAPTURE_SUFFIXES = (".html", ".htm", ".xhtml")


def page_origin(html: str) -> str | None:
    """The address a saved page still remembers.

    A file on disk has no URL, but the page kept its canonical link for
    search engines. Without it, relative advert links resolve to nothing and
    every listing loses its address.
    """
    for selector, attribute in ORIGIN_SELECTORS:
        value = select_field(html, selector, attribute)
        if value and value.startswith("http"):
            return value
    return extract_opengraph(html).get("og:url") or None


def source_of(
    html: str, *, url: str | None = None, fallback: str | None = None
) -> str | None:
    """Which configured source a captured page belongs to.

    Asked of the page rather than of the user: they saved it from the site,
    the site wrote its own address in it, and one less thing to get wrong.
    An address given by the caller wins over the page's canonical link: the
    extension reads the browser's own address bar, which is where the person
    actually is, while a canonical link can point at a tidier page.
    """
    from . import source_for_url

    for candidate in (url, page_origin(html)):
        if candidate:
            name = source_for_url(candidate)
            if name:
                return name
    return fallback


def read_capture(
    html: str, *, source: str | None = None, url: str | None = None
) -> list[ListingData]:
    """Every advert a captured page holds, results page or single advert.

    The same three tiers as a crawl - schema.org, embedded state, microdata -
    because a page is a page: what changes is who asked for it.
    """
    if not html or not html.strip():
        return []

    name = source or source_of(html, url=url) or "capture"
    origin = url or page_origin(html) or f"https://{name}.invalid/"

    rows = extract_listings_from_search(html, base_url=origin, source=name)
    if rows:
        return [enrich(row) for row in rows]

    single = extract_from_page(html, url=origin, source=name)
    return [enrich(single)] if single is not None else []


def capture_files(path: Path) -> Iterator[Path]:
    """The HTML files at a path: one file, or every page in a folder."""
    if path.is_dir():
        for suffix in CAPTURE_SUFFIXES:
            yield from sorted(path.glob(f"*{suffix}"))
        return
    if path.suffix.lower() in CAPTURE_SUFFIXES or path.is_file():
        yield path


def read_captures(
    paths: list[Path], *, source: str | None = None
) -> tuple[list[ListingData], list[str]]:
    """Read every capture, returning the adverts and what could not be read.

    Failures are returned rather than raised: importing twenty pages must not
    stop on the one that was saved half-written.
    """
    listings: list[ListingData] = []
    problems: list[str] = []
    seen: set[tuple[str, str]] = set()

    for path in paths:
        for file in capture_files(path):
            try:
                html = file.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                problems.append(f"{file.name}: illisible ({exc})")
                continue
            rows = read_capture(html, source=source)
            if not rows:
                problems.append(
                    f"{file.name}: aucune annonce reconnue "
                    f"({len(html) // 1024} Ko, source {source_of(html) or 'inconnue'})"
                )
                continue
            for row in rows:
                key = (row.source, row.source_id)
                if key in seen:
                    continue
                seen.add(key)
                listings.append(row)
            log.info("%s: %s annonces", file.name, len(rows))
    return listings, problems
