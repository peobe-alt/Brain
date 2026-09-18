"""A source adapter entirely described by a YAML file.

Adding a country or a site should not mean writing Python. A file in
`sites/` declares the search URL shape, how advert links look, and optional
CSS fallbacks; extraction itself is the generic schema.org reader.

Two ways to search:

* `search(query)` builds a URL from the site's template;
* `search_url("<any search URL you built in the site's own UI>")` - by far
  the most reliable path, and the one to prefer when a site's filters are
  complex.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Iterator

import yaml

from ..normalize import enrich
from ..schemas import ListingData, SearchQuery
from .base import SourceAdapter, SourceInfo
from .fetcher import FetchError, PoliteFetcher, RobotsDisallowed
from .structured import (
    extract_from_page,
    extract_listing_links,
    extract_listings_from_search,
    split_fragment_route,
)

log = logging.getLogger(__name__)

SITES_DIR = Path(__file__).parent / "sites"

#: Motif de lien applique quand la source n'en declare pas.
DEFAULT_LINK_PATTERN = r"/\d{5,}"


def link_pattern(config: dict[str, Any]) -> str:
    """The source's advert-link pattern, never empty.

    An empty regex matches every link: a site file carrying
    `listing_link_pattern: ""` would harvest `/aide` and `/cgu` as adverts,
    then fetch them one by one. Absent and empty both mean "not established
    yet", so both fall back to the generic default.
    """
    return config.get("listing_link_pattern") or DEFAULT_LINK_PATTERN


class ConfiguredSource(SourceAdapter):
    def __init__(self, config: dict[str, Any], fetcher: PoliteFetcher | None = None) -> None:
        self.config = config
        self.info = SourceInfo(
            name=config["name"],
            label=config.get("label", config["name"]),
            countries=config.get("countries", []),
            requires_js=bool(config.get("requires_js", False)),
            notes=config.get("notes", ""),
        )
        self._fetcher = fetcher or PoliteFetcher(
            delay=config.get("request_delay"),
            respect_robots=config.get("respect_robots"),
        )
        self._owns_fetcher = fetcher is None

    # -- URL building ------------------------------------------------------

    def build_search_url(self, query: SearchQuery, page: int = 1) -> str | None:
        template = self.config.get("search_url")
        if not template:
            return None
        # Les sites utilisent rarement les codes ISO: la correspondance est
        # declaree par source dans son YAML.
        wanted = (query.countries or ["FR"])[0].upper()
        codes = self.config.get("country_codes") or {}
        values = {
            "country": codes.get(wanted, wanted),
            "make": _slug(query.make),
            "model": _slug(query.model),
            "keywords": query.keywords or "",
            "price_min": query.price_min or "",
            "price_max": query.price_max or "",
            "year_min": query.year_min or "",
            "year_max": query.year_max or "",
            "km_max": query.km_max or "",
            "postcode": query.postcode or "",
            "radius": query.radius_km or "",
            "page": page,
        }
        url = template.format_map(_Missing(values))
        # Drop query params left empty, then tidy the separators they leave.
        url = re.sub(r"([?&])[\w\[\]%.-]+=(?=&|$)", r"\1", url)
        while "?&" in url or "&&" in url:
            url = url.replace("?&", "?").replace("&&", "&")
        url = re.sub(r"(?<!:)//+", "/", url.replace("://", "\x00"))
        url = re.sub(r"/+\?", "?", url.replace("\x00", "://"))
        return url.rstrip("?&/")

    # -- crawling ----------------------------------------------------------

    def search(self, query: SearchQuery) -> Iterator[ListingData]:
        from ..config import get_settings

        pages = min(self.config.get("max_pages", 3), get_settings().max_pages_per_search)
        seen = 0
        for page in range(1, pages + 1):
            url = self.build_search_url(query, page)
            if not url:
                log.warning("%s: aucun modele d'URL de recherche configure", self.name)
                return
            for listing in self.search_url(url, limit=query.limit - seen):
                yield listing
                seen += 1
                if seen >= query.limit:
                    return

    def search_url(self, url: str, limit: int = 100) -> Iterator[ListingData]:
        """Crawl one results page and yield the adverts it holds.

        Two ways in, tried in that order:

        1. the page's own `ItemList` (schema.org), which carries the whole
           page of adverts. One request for twenty cars, and it keeps working
           on sites whose advert links only exist after JavaScript runs;
        2. failing that, harvest the advert links and open each one.

        The first path returns adverts without their description or their
        full photo set: enough to value and rank, not enough for the expert
        pass, which re-opens the shortlist through `fetch_detail`.
        """
        url, route = split_fragment_route(url)
        if route:
            # Sans ce message, le scan rapporte "0 annonce" sur la page
            # d'accueil du site et laisse croire que la recherche est vide.
            log.warning(
                "%s: les filtres de cette URL sont derriere `#` (%s) et ne sont pas "
                "envoyes au serveur. Seul %s sera demande. Diagnostiquer avec: "
                'carexpert diagnose -s %s --url "..."',
                self.name, route, url, self.name,
            )
        try:
            page = self._fetcher.get(url)
        except (FetchError, RobotsDisallowed) as exc:
            log.warning("%s: recherche impossible (%s)", self.name, exc)
            return
        if not page.ok:
            log.warning("%s: HTTP %s sur %s", self.name, page.status, url)
            return

        if self.config.get("results_page_listings", True):
            rows = extract_listings_from_search(
                page.text,
                base_url=url,
                source=self.name,
                country=self.config.get("default_country", "FR"),
            )
            if _usable(rows):
                log.info("%s: %s annonces lues sur la page de resultats", self.name, len(rows))
                for listing in rows[:limit]:
                    yield enrich(listing)
                return
            if rows:
                log.info(
                    "%s: la page de resultats liste %s annonces mais sans prix ni "
                    "kilometrage exploitables; ouverture des annonces une a une.",
                    self.name, len(rows),
                )

        links = extract_listing_links(page.text, url, link_pattern(self.config))
        if not links:
            log.warning(
                "%s: aucune annonce trouvee sur %s. Le site rend probablement ses "
                "resultats en JavaScript, ou le motif de lien a change.",
                self.name, url,
            )
        for link in links[:limit]:
            listing = self.fetch_listing(link)
            if listing is not None:
                yield listing

    def fetch_listing(self, url: str) -> ListingData | None:
        try:
            page = self._fetcher.get(url)
        except (FetchError, RobotsDisallowed) as exc:
            log.info("%s: annonce ignoree (%s)", self.name, exc)
            return None
        if not page.ok:
            return None
        listing = extract_from_page(
            page.text,
            url=url,
            source=self.name,
            country=self.config.get("default_country", "FR"),
            selectors=self.config.get("selectors"),
        )
        if listing is None:
            log.debug("%s: page non exploitable %s", self.name, url)
            return None
        return enrich(listing)

    def fetch_detail(self, listing: ListingData) -> ListingData:
        full = self.fetch_listing(listing.url)
        return full or listing

    def close(self) -> None:
        if self._owns_fetcher:
            self._fetcher.close()


#: Part des annonces d'une page de resultats qui doivent porter un prix ET un
#: kilometrage pour que la page se suffise a elle-meme. En dessous, mieux vaut
#: payer une requete par annonce que valoriser sur du vide.
RESULTS_PAGE_QUALITY = 0.5


def _usable(listings: list[ListingData]) -> bool:
    if not listings:
        return False
    complete = sum(1 for row in listings if row.price is not None and row.km is not None)
    return complete >= max(1, int(len(listings) * RESULTS_PAGE_QUALITY))


class _Missing(dict):
    def __missing__(self, key: str) -> str:  # pragma: no cover - template safety
        return ""


def _slug(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def load_site_configs(directory: Path | None = None) -> dict[str, dict[str, Any]]:
    configs: dict[str, dict[str, Any]] = {}
    for path in sorted((directory or SITES_DIR).glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:  # pragma: no cover - malformed user file
            log.error("configuration de site illisible %s: %s", path, exc)
            continue
        if isinstance(data, dict) and data.get("name"):
            configs[data["name"]] = data
    return configs
