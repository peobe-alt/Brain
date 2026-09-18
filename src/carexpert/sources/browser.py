"""Render a page with a real browser, when nothing cheaper works.

This is the third tier, and it is deliberately the last one. `structured.py`
reads the schema.org markup a site publishes for search engines; `embedded.py`
reads the JSON state it ships for its own hydration. Both cost one HTTP
request. A rendered page costs a browser launch, a network round of thirty to
sixty sub-requests, and a few seconds of the site's CPU as well as ours.

So the default here is *escalation*: fetch over plain HTTP, look at what came
back, and pay for a browser only when the page really does hold no adverts.
On a source that turns out not to need it, the browser never starts.

Where the line is
-----------------

This renders a page with a real browser, at the same one-request-every-N
-seconds rhythm as the rest of the project, with robots.txt honoured. That is
all it does.

It does not solve captchas, forge or replay bot-protection tokens, rotate
addresses, or dress itself up as somebody else's browser to slip past a
check. When a site answers with a challenge page, `detect_challenge` sees it
and the fetch stops with a readable reason: a site that challenges us has
said no, and the answer to no is to ask for a data agreement, not to try
harder. See `docs/02-sources-et-legal.md`.

The user agent follows from the same principle: Chromium's own string, plus
this project's token so the site can identify and contact whoever is reading
it. `CAREXPERT_USER_AGENT` overrides it, and what you put there is on you.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from ..config import get_settings
from .fetcher import FetchResult, PoliteFetcher, TransportError

log = logging.getLogger(__name__)


class BrowserUnavailable(RuntimeError):
    """Playwright, or its browser, is not installed."""


class BotProtection(RuntimeError):
    """The site answered with a challenge instead of its page.

    Raised, never worked around. The caller reports it and moves on.
    """


INSTALL_HINT = (
    "Le rendu navigateur demande Playwright. A installer une fois:\n"
    "    pip install -e \".[browser]\"\n"
    "    python -m playwright install chromium"
)

#: Markers of a challenge page rather than the page asked for. Matched on
#: short, unambiguous strings: a false positive here costs a whole source.
CHALLENGE_MARKERS = (
    "captcha-delivery.com",
    "geo.captcha-delivery",
    "datadome",
    "/cdn-cgi/challenge-platform",
    "cf-browser-verification",
    "px-captcha",
    "_pxhd",
    "perimeterx",
    "incapsula_resource",
    "distil_r_captcha",
    "are you a human",
    "verifying you are human",
)

#: A challenge page is a few kilobytes: a title, a script, nothing else. A
#: real results page carries twenty adverts and is an order of magnitude
#: bigger, even when it happens to name its own anti-bot provider in an
#: analytics payload. Reading the marker without this bound would make the
#: richest source cut itself off.
CHALLENGE_MAX_CHARS = 15_000

#: Subresources a price scanner never reads. Blocking them is not an
#: optimisation for us so much as courtesy to the site: one results page
#: pulls dozens of photos we would throw away.
BLOCKED_RESOURCES = ("image", "media", "font")


def page_carries_adverts(html: str, *, url: str = "https://example.invalid/") -> bool:
    """Whether a plain HTTP response already holds what we came for.

    The escalation test. Kept here rather than in the extractors so the two
    cheap tiers stay free of any notion that a browser exists.
    """
    if not html or len(html) < 500:
        return False
    from .structured import extract_jsonld, find_item_list, find_vehicle_node
    from .embedded import extract_listings_from_state

    nodes = extract_jsonld(html)
    if find_item_list(nodes) or find_vehicle_node(nodes):
        return True
    return bool(extract_listings_from_state(html, base_url=url, source="probe", limit=1))


def detect_challenge(html: str, status: int) -> str | None:
    """Name the protection that answered, or None if the page is the page.

    A refusal is a refusal whatever it carries, so a 401, 403 or 429 always
    counts; a 200 counts only when the body is both short and marked, which
    is what an interstitial looks like.
    """
    lowered = html.lower() if len(html) < CHALLENGE_MAX_CHARS else ""
    named = next((marker for marker in CHALLENGE_MARKERS if marker in lowered), None)
    if status in (401, 403, 429):
        return named or f"HTTP {status}"
    return named


class BrowserFetcher(PoliteFetcher):
    """A `PoliteFetcher` that can render, and would rather not.

    Everything that makes the parent polite - robots.txt, one request per
    host per delay, the disk cache, the back-off - is inherited untouched.
    Only the transport changes.
    """

    def __init__(
        self,
        *,
        escalate: bool = True,
        wait_until: str = "domcontentloaded",
        wait_ms: int = 2500,
        wait_selector: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        #: False forces the browser on every page; True (the default) reaches
        #: for it only once plain HTTP has come back without adverts.
        self.escalate = escalate
        self.wait_until = wait_until
        self.wait_ms = wait_ms
        self.wait_selector = wait_selector
        self.renders = 0
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None

    # -- transport ---------------------------------------------------------

    def _transport(self, url: str) -> FetchResult:
        if self.escalate:
            plain = super()._transport(url)
            if plain.ok and page_carries_adverts(plain.text, url=url):
                return plain
            reason = "aucune annonce dans la reponse" if plain.ok else f"HTTP {plain.status}"
            log.info("%s: %s, passage au rendu navigateur", url, reason)
        return self._render(url)

    def _render(self, url: str) -> FetchResult:
        context = self._ensure_context()
        page = context.new_page()
        try:
            response = page.goto(url, wait_until=self.wait_until,
                                 timeout=int(get_settings().request_timeout * 1000))
            if self.wait_selector:
                try:
                    page.wait_for_selector(self.wait_selector, timeout=self.wait_ms)
                except Exception:
                    # Hydration can finish without that selector ever existing;
                    # the page is still worth reading.
                    log.debug("%s: selecteur %s absent", url, self.wait_selector)
            elif self.wait_ms:
                page.wait_for_timeout(self.wait_ms)
            html = page.content()
            status = response.status if response is not None else 0
            final_url = page.url or url
        except Exception as exc:  # playwright raises its own hierarchy
            raise TransportError(f"rendu impossible: {exc}") from exc
        finally:
            page.close()

        self.renders += 1
        challenge = detect_challenge(html, status)
        if challenge:
            raise BotProtection(
                f"{url}: le site repond par une protection anti-bot ({challenge}). "
                "La collecte s'arrete la, volontairement."
            )
        return FetchResult(url=final_url, status=status or 200, text=html, rendered=True)

    # -- browser lifecycle -------------------------------------------------

    def _ensure_context(self) -> Any:
        if self._context is not None:
            return self._context
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise BrowserUnavailable(INSTALL_HINT) from exc

        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=True)
        except Exception as exc:  # pragma: no cover - browser not downloaded
            raise BrowserUnavailable(f"{INSTALL_HINT}\n({exc})") from exc

        self._context = self._browser.new_context(
            locale="fr-FR",
            timezone_id="Europe/Paris",
            user_agent=self._user_agent(),
        )
        self._context.set_default_timeout(int(get_settings().request_timeout * 1000))
        self._context.route(
            "**/*",
            lambda route: (
                route.abort()
                if route.request.resource_type in BLOCKED_RESOURCES
                else route.continue_()
            ),
        )
        return self._context

    def _user_agent(self) -> str:
        """Chromium's own string, with this project's token appended.

        Identifying the crawler is the point: a site that wants to talk to
        whoever is reading it should be able to. Stripping the token to look
        like an ordinary visitor is evasion, which is the one thing this file
        will not do on its own.
        """
        base = ""
        try:
            page = self._browser.new_page()
            base = page.evaluate("() => navigator.userAgent") or ""
            page.close()
        except Exception:  # pragma: no cover - fall back to the bot string
            base = ""
        base = re.sub(r"\bHeadless", "", base).strip()
        token = get_settings().user_agent
        return f"{base} {token}".strip() if base else token

    def close(self) -> None:
        for resource in (self._context, self._browser, self._playwright):
            if resource is None:
                continue
            try:
                (resource.stop if hasattr(resource, "stop") else resource.close)()
            except Exception:  # pragma: no cover - teardown is best-effort
                pass
        self._context = self._browser = self._playwright = None
        super().close()


def fetcher_for(config: dict[str, Any], *, browser: bool | None = None) -> PoliteFetcher:
    """The right fetcher for a source, from its YAML and the user's choice.

    `browser=None` follows the site's `requires_js`; True and False are the
    `--browser` / `--no-browser` flags and win over it. Even when a browser
    is allowed, escalation means it only starts if the page needs it.
    """
    wanted = config.get("requires_js", False) if browser is None else browser
    delay = config.get("request_delay")
    respect = config.get("respect_robots")
    if not wanted:
        return PoliteFetcher(delay=delay, respect_robots=respect)
    render = config.get("render", {}) or {}
    return BrowserFetcher(
        delay=delay,
        respect_robots=respect,
        escalate=bool(render.get("escalate", True)),
        wait_until=render.get("wait_until", "domcontentloaded"),
        wait_ms=int(render.get("wait_ms", 2500)),
        wait_selector=render.get("wait_selector"),
    )
