"""Read a page the user is already looking at, instead of fetching it.

Three of the five sites on the list render their results in JavaScript, and
the best protected of them is exactly the one where private sellers post. A
crawler faces, there, a choice between failing and working around a
protection this project has decided not to work around.

A page the browser has already rendered has neither problem: the JavaScript
has run, the session is the user's own, and reading it costs the site
nothing at all - the request had already been made, by a human, for himself.
What arrives here is that page's HTML. The only job left is to recognise
what it is.

Nothing in this module touches the network. That is the point of it.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from ..schemas import ListingData
from . import source_for_url
from .configured import load_site_configs
from .structured import extract_from_page, extract_listings_from_search

log = logging.getLogger(__name__)

#: Un site europeen sert le meme catalogue sous une dizaine de domaines. Le
#: pays d'une annonce se lit donc dans le domaine visite, pas dans la config.
TLD_COUNTRIES = {
    "fr": "FR", "de": "DE", "it": "IT", "es": "ES", "nl": "NL", "be": "BE",
    "at": "AT", "lu": "LU", "pl": "PL", "ch": "CH", "pt": "PT", "uk": "GB",
}


@dataclass(slots=True)
class CapturedPage:
    """One page sent by the browser, once we know what it holds."""

    url: str
    kind: str = "unknown"          # search | listing | unknown
    source: str | None = None
    country: str = "FR"
    listings: list[ListingData] = field(default_factory=list)
    #: Pourquoi la page n'a rien donne, en clair, pour l'afficher tel quel.
    reason: str = ""


def read_page(html: str, url: str, *, source: str | None = None) -> CapturedPage:
    """Turn the HTML of a rendered page into the adverts it shows."""
    if not url.startswith(("http://", "https://")):
        return CapturedPage(url=url, reason="Adresse de page inattendue.")

    source = source or source_for_url(url)
    if source is None:
        return CapturedPage(url=url, reason="Ce site n'est pas encore connu de CarExpert.")

    config = load_site_configs().get(source, {})
    country = _country(url, config)
    page = CapturedPage(url=url, source=source, country=country)

    # Une fiche d'annonce publie souvent, en plus de sa voiture, une liste de
    # vehicules similaires. La lire comme une page de resultats ferait perdre
    # l'annonce regardee au profit de six voisines. L'adresse tranche: les
    # sites decrivent la forme de leurs URL d'annonce dans leur YAML.
    readers = (_as_listing, _as_search) if _is_advert_url(url, config) else (_as_search, _as_listing)
    for reader in readers:
        if reader(page, html, config):
            return page

    page.reason = (
        "Cette page ne publie aucune annonce lisible. Ouvrez une page de "
        "resultats ou une annonce du site."
    )
    return page


def _as_search(page: CapturedPage, html: str, _config: dict[str, Any]) -> bool:
    listings = extract_listings_from_search(
        html, base_url=page.url, source=page.source or "", country=page.country
    )
    if not listings:
        return False
    page.kind, page.listings = "search", listings
    return True


def _as_listing(page: CapturedPage, html: str, config: dict[str, Any]) -> bool:
    listing = extract_from_page(
        html,
        url=page.url,
        source=page.source or "",
        country=page.country,
        selectors=config.get("selectors") or {},
    )
    if listing is None or not listing.title:
        return False
    page.kind, page.listings = "listing", [listing]
    return True


def _is_advert_url(url: str, config: dict[str, Any]) -> bool:
    pattern = config.get("listing_link_pattern")
    if not pattern:
        return False
    parsed = urlparse(url)
    target = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    try:
        return bool(re.search(pattern, target))
    except re.error:  # pragma: no cover - a broken YAML pattern is a config bug
        log.warning("motif d'annonce illisible pour %s", config.get("name"))
        return False


def _country(url: str, config: dict[str, Any]) -> str:
    host = (urlparse(url).netloc or "").lower().split(":")[0]
    tld = host.rsplit(".", 1)[-1] if "." in host else ""
    return TLD_COUNTRIES.get(tld) or config.get("default_country") or "FR"
