"""The contract every source adapter honours."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterator

from ..schemas import ListingData, SearchQuery


@dataclass(slots=True)
class SourceInfo:
    name: str
    label: str
    countries: list[str] = field(default_factory=list)
    requires_js: bool = False
    notes: str = ""


class SourceAdapter(ABC):
    """A place adverts come from.

    Two responsibilities, deliberately separated so the crawl stays polite:
    `search()` walks result pages and yields *stubs* (enough to deduplicate
    and pre-filter), `fetch_detail()` opens one advert and returns everything.
    """

    info: SourceInfo

    @property
    def name(self) -> str:
        return self.info.name

    @abstractmethod
    def search(self, query: SearchQuery) -> Iterator[ListingData]:
        """Yield listings matching the query, cheapest-effort first."""

    def fetch_detail(self, listing: ListingData) -> ListingData:
        """Enrich one listing with its full advert page. Default: no-op."""
        return listing

    def close(self) -> None:  # pragma: no cover - overridden when needed
        return None
