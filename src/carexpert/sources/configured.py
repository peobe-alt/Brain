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
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import yaml

from ..config import get_settings
from ..normalize import enrich
from ..schemas import ListingData, SearchQuery
from .base import SourceAdapter, SourceInfo
from .fetcher import FetchError, PoliteFetcher, RobotsDisallowed
from .structured import (
    _listing_id,
    extract_from_page,
    extract_listing_links,
    extract_listings_from_search,
)

log = logging.getLogger(__name__)

SITES_DIR = Path(__file__).parent / "sites"


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
        url = self.build_search_url(query, 1)
        if not url:
            log.warning("%s: aucun modele d'URL de recherche configure", self.name)
            return
        yield from self.search_url(url, limit=query.limit)

    def search_url(self, url: str, limit: int = 100) -> Iterator[ListingData]:
        """Crawl a pasted search URL, following its pagination.

        A pasted URL is one page of results, and one page is twenty adverts
        out of the eighteen hundred the search actually matches. So we walk
        the pages ourselves, by setting the site's page parameter, and stop
        as soon as a page brings nothing new.
        """
        seen: set[str] = set()
        pages = min(self.config.get("max_pages", 3), get_settings().max_pages_per_search)
        # L'URL collee n'est pas retouchee au-dela du numero de page. Sur une
        # URL de recherche, `sort` et `atype` ne sont pas du tracage: ce sont
        # les filtres que l'utilisateur a choisis dans l'interface du site.
        start = _page_number(url, self.page_param)

        for offset in range(pages):
            target = url if offset == 0 else _with_page(url, self.page_param, start + offset)
            fresh = 0
            for listing in self._search_one_page(target, limit - len(seen), seen):
                if listing.source_id in seen:
                    continue
                seen.add(listing.source_id)
                fresh += 1
                yield listing
                if len(seen) >= limit:
                    return
            if fresh == 0:
                # Page vide, page repetee, ou fin des resultats: dans les trois
                # cas continuer ne ferait que couter des requetes au site.
                if offset:
                    log.info("%s: fin des resultats a la page %s", self.name, start + offset)
                return

    @property
    def page_param(self) -> str:
        return self.config.get("page_param", "page")

    def _search_one_page(
        self, url: str, limit: int, seen: set[str]
    ) -> Iterator[ListingData]:
        """One results page, read whole.

        Two ways in, tried in that order:

        1. the page's own `ItemList` (schema.org), which carries the whole
           page of adverts. One request for twenty cars, and it keeps working
           on sites whose advert links only exist after JavaScript runs;
        2. failing that, harvest the advert links and open each one.

        The first path returns adverts without their description or their
        full photo set: enough to value and rank, not enough for the expert
        pass, which re-opens the shortlist through `fetch_detail`.
        """
        if limit <= 0:
            return
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

        pattern = self.config.get("listing_link_pattern", r"/\d{5,}")
        links = extract_listing_links(page.text, url, pattern)
        if not links:
            log.warning(
                "%s: aucune annonce trouvee sur %s. Le site rend probablement ses "
                "resultats en JavaScript, ou le motif de lien a change.",
                self.name, url,
            )
        taken = 0
        for link in links:
            if taken >= limit:
                return
            # Une annonce deja vue a la page precedente ne se repaie pas.
            if _listing_id(link) in seen:
                continue
            listing = self.fetch_listing(link)
            if listing is not None:
                taken += 1
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


def _page_number(url: str, param: str) -> int:
    """Which page a pasted URL is already on."""
    for key, value in parse_qsl(urlparse(url).query):
        if key.lower() == param and value.strip().isdigit():
            return max(1, int(value.strip()))
    return 1


def _with_page(url: str, param: str, page: int) -> str:
    parsed = urlparse(url)
    kept = [(k, v) for k, v in parse_qsl(parsed.query) if k.lower() != param]
    kept.append((param, str(page)))
    return urlunparse(parsed._replace(query=urlencode(kept)))


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
