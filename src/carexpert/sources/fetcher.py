"""A deliberately polite HTTP client.

Everything this project does to a third-party site goes through here, so the
rules live in one place:

* robots.txt is fetched once per host and honoured (disable knowingly, per
  source, only where you have permission to);
* one request every `request_delay` seconds *per host*, never in parallel;
* responses are cached on disk so re-running a scan does not re-hit the site;
* 429/503 back off exponentially and respect `Retry-After`.

If a source needs more than this to be collected (rendered JS, aggressive
bot protection), that is a signal to look for an official API or a data
partnership rather than to fight the protection.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import time
import urllib.robotparser
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from ..config import get_settings

log = logging.getLogger(__name__)


class RobotsDisallowed(RuntimeError):
    """Raised when robots.txt forbids the URL we were about to fetch."""


class FetchError(RuntimeError):
    """Raised when a URL could not be retrieved after every retry."""


@dataclass(slots=True)
class FetchResult:
    url: str
    status: int
    text: str
    from_cache: bool = False

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class PoliteFetcher:
    """Rate-limited, cached, robots-aware HTTP getter."""

    def __init__(
        self,
        *,
        user_agent: str | None = None,
        delay: float | None = None,
        respect_robots: bool | None = None,
        cache_dir: Path | None = None,
        cache_ttl_hours: int | None = None,
    ) -> None:
        settings = get_settings()
        self.user_agent = user_agent or settings.user_agent
        self.delay = settings.request_delay if delay is None else delay
        self.respect_robots = (
            settings.respect_robots if respect_robots is None else respect_robots
        )
        self.cache_dir = cache_dir or settings.cache_dir
        self.cache_ttl = (
            settings.cache_ttl_hours if cache_ttl_hours is None else cache_ttl_hours
        ) * 3600
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._client = httpx.Client(
            follow_redirects=True,
            timeout=settings.request_timeout,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8,de;q=0.7",
            },
        )
        self.max_retries = settings.max_retries

    # -- cache -------------------------------------------------------------

    def _cache_path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
        return self.cache_dir / f"{digest}.json"

    def _read_cache(self, url: str) -> FetchResult | None:
        path = self._cache_path(url)
        if not path.exists():
            return None
        if self.cache_ttl and (time.time() - path.stat().st_mtime) > self.cache_ttl:
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return FetchResult(url=url, status=payload["status"], text=payload["text"], from_cache=True)

    def _write_cache(self, result: FetchResult) -> None:
        try:
            self._cache_path(result.url).write_text(
                json.dumps({"status": result.status, "text": result.text}),
                encoding="utf-8",
            )
        except OSError as exc:  # pragma: no cover - disk full, read-only fs...
            log.debug("cache write failed for %s: %s", result.url, exc)

    # -- robots ------------------------------------------------------------

    def _robots_for(self, url: str) -> urllib.robotparser.RobotFileParser | None:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin in self._robots:
            return self._robots[origin]
        parser: urllib.robotparser.RobotFileParser | None = urllib.robotparser.RobotFileParser()
        assert parser is not None
        parser.set_url(urljoin(origin, "/robots.txt"))
        try:
            response = self._client.get(urljoin(origin, "/robots.txt"))
            if response.status_code >= 400:
                parser = None
            else:
                parser.parse(response.text.splitlines())
        except httpx.HTTPError as exc:
            log.debug("robots.txt unreachable for %s: %s", origin, exc)
            parser = None
        self._robots[origin] = parser
        return parser

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parser = self._robots_for(url)
        if parser is None:  # no robots.txt published: nothing forbids us
            return True
        return parser.can_fetch(self.user_agent, url)

    def crawl_delay(self, url: str) -> float:
        parser = self._robots_for(url) if self.respect_robots else None
        if parser is not None:
            declared = parser.crawl_delay(self.user_agent)
            if declared:
                return max(float(declared), self.delay)
        return self.delay

    # -- fetching ----------------------------------------------------------

    def _throttle(self, url: str) -> None:
        host = urlparse(url).netloc
        wait_for = self.crawl_delay(url)
        last = self._last_request.get(host)
        if last is not None:
            elapsed = time.monotonic() - last
            if elapsed < wait_for:
                # A little jitter so we never look like a metronome.
                time.sleep(wait_for - elapsed + random.uniform(0, 0.4))
        self._last_request[host] = time.monotonic()

    def get(self, url: str, *, use_cache: bool = True) -> FetchResult:
        if use_cache:
            cached = self._read_cache(url)
            if cached is not None:
                return cached
        if not self.allowed(url):
            raise RobotsDisallowed(f"robots.txt interdit {url}")

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._throttle(url)
            try:
                response = self._client.get(url)
            except httpx.HTTPError as exc:
                last_error = exc
                time.sleep(2**attempt)
                continue
            if response.status_code in (429, 503):
                retry_after = response.headers.get("Retry-After")
                pause = float(retry_after) if (retry_after or "").isdigit() else 2 ** (attempt + 2)
                log.warning("%s renvoie %s, pause de %.0fs", url, response.status_code, pause)
                time.sleep(min(pause, 120))
                last_error = FetchError(f"HTTP {response.status_code}")
                continue
            if response.status_code >= 500:
                last_error = FetchError(f"HTTP {response.status_code}")
                time.sleep(2**attempt)
                continue
            result = FetchResult(url=str(response.url), status=response.status_code,
                                 text=response.text)
            if result.ok:
                self._write_cache(result)
            return result
        raise FetchError(f"echec de recuperation de {url}: {last_error}")

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "PoliteFetcher":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
