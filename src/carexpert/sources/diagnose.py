"""Answering one question: does this site actually work, and if not, why?

The first hour spent on a new source is always the same: is it reachable,
does robots.txt allow it, does the search page even contain advert links, and
does an advert page expose usable data? Guessing that from a silent log is
slow. This module answers it in one command, and proposes the fix.

Everything here is read-only and deliberately tiny: a handful of pages at the
source's own rate, nothing more.
"""

from __future__ import annotations

import re
import time
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from ..normalize import enrich
from ..schemas import ListingData
from .browser import BotProtection, BrowserUnavailable, named_protection
from .fetcher import FetchError, PoliteFetcher, RobotsDisallowed
from .structured import (
    extract_from_page,
    extract_jsonld,
    extract_listings_from_search,
    find_vehicle_node,
)

#: Fields without which a listing is useless downstream.
CRITICAL_FIELDS = ("price_eur", "km", "year", "make")
USEFUL_FIELDS = ("model", "fuel", "gearbox", "power_hp", "photos", "description")

#: Markers left by client-side frameworks: their presence next to an empty
#: link harvest means the adverts are rendered in the browser, not served.
JS_MARKERS = (
    "__NEXT_DATA__", "window.__NUXT__", "__remixContext", "ng-version",
    "data-reactroot", "window.__INITIAL_STATE__", "__APOLLO_STATE__",
)


@dataclass
class SampleReport:
    url: str
    status: int = 0
    has_jsonld: bool = False
    jsonld_types: list[str] = field(default_factory=list)
    filled: list[str] = field(default_factory=list)
    missing_critical: list[str] = field(default_factory=list)
    photo_count: int = 0
    error: str = ""

    @property
    def usable(self) -> bool:
        return not self.error and not self.missing_critical


@dataclass
class DiagnosticReport:
    url: str
    source: str
    robots_present: bool = False
    robots_allows: bool = True
    crawl_delay: float = 0.0
    status: int = 0
    page_bytes: int = 0
    elapsed_s: float = 0.0
    links_found: int = 0
    pattern_used: str = ""
    #: Par quel palier les annonces sont sorties: "schema.org" (balisage
    #: publie pour les moteurs), "etat embarque" (le JSON que la page
    #: hydrate), "liens" (une requete par annonce). Savoir lequel a repondu
    #: dit quoi corriger quand une source se tarit.
    extraction_tier: str = ""
    #: Vrai si un navigateur a du rendre la page. Une source qui bascule
    #: silencieusement en rendu coute trente fois plus cher au site comme a
    #: nous: elle doit se voir.
    rendered: bool = False
    #: Annonces lues directement dans le JSON-LD de la page de resultats.
    results_listings: int = 0
    results_complete: int = 0
    results_missing: list[str] = field(default_factory=list)
    suggested_pattern: str | None = None
    js_suspected: bool = False
    samples: list[SampleReport] = field(default_factory=list)
    error: str = ""
    #: Ce qui a empeche d'aller plus loin sans faire echouer la lecture:
    #: typiquement un navigateur absent alors que la page en demandait un.
    note: str = ""
    #: La protection qui a repondu a la place de la page, nommee. Un refus
    #: rendu comme "HTTP 403" se lit comme une panne, alors que c'est une
    #: decision du site: les deux ne se corrigent pas pareil.
    protection: str = ""

    @property
    def usable_samples(self) -> int:
        return sum(1 for s in self.samples if s.usable)

    def verdict(self) -> tuple[str, str]:
        """(niveau, phrase): what this source is worth, in one line."""
        # Le refus leve en cours de rendu n'a ni statut ni corps a montrer:
        # seul le nom de ce qui a repondu est connu. Le refus constate sur
        # une reponse HTTP en dit plus, et se traite plus bas.
        if self.error and self.protection:
            return "bloque", (
                f"le site refuse la requete: il repond par {self.protection} "
                "au lieu de sa page"
            )
        if self.error:
            return "echec", self.error
        if not self.robots_allows:
            return "interdit", "le robots.txt du site interdit cette URL"
        # Un refus n'est pas une panne. Une panne, on la relance; un refus,
        # non: c'est une decision du site, et la seule suite correcte est de
        # ne pas insister. Les nommer pareil menerait a insister.
        if self.status in (401, 403, 429):
            named = f" ({self.protection})" if self.protection else ""
            return "bloque", (
                f"le site refuse la requete: HTTP {self.status}{named}, "
                f"{self.page_bytes} octets au lieu d'une page de resultats"
            )
        if self.status >= 400:
            return "echec", f"le site repond HTTP {self.status}"
        # La page de resultats se suffit parfois a elle-meme: elle publie ses
        # annonces en JSON-LD. Dans ce cas l'absence de liens ne prouve rien.
        if self.results_complete:
            via = f" via {self.extraction_tier}" if self.extraction_tier else ""
            rendu = ", apres rendu navigateur" if self.rendered else ""
            return "liste", (
                f"{self.results_listings} annonces lues directement sur la page de "
                f"resultats{via}, dont {self.results_complete} completes{rendu}"
            )
        if self.links_found == 0 and self.js_suspected:
            return "js", "la page de recherche est rendue par JavaScript, rien a extraire en HTTP simple"
        if self.links_found == 0:
            return "motif", "aucun lien d'annonce reconnu: le motif de lien ne correspond pas"
        if not self.samples:
            return "partiel", "des liens trouves, mais aucune annonce n'a pu etre ouverte"
        if self.usable_samples == 0:
            return "extraction", "annonces ouvertes, mais les donnees essentielles manquent"
        if self.usable_samples < len(self.samples):
            return "partiel", (
                f"{self.usable_samples}/{len(self.samples)} annonces exploitables"
            )
        return "ok", f"{self.links_found} annonces detectees, extraction complete sur l'echantillon"

    def actions(self) -> list[str]:
        """What to do next, concretely."""
        level, _ = self.verdict()
        if level in ("echec", "bloque"):
            return self._failure_actions()
        if level == "interdit":
            return [
                "Ne pas collecter cette URL.",
                "Chercher un flux officiel ou une offre professionnelle aupres du site.",
            ]
        if level == "liste":
            source_of_truth = (
                "publie leurs donnees en JSON-LD."
                if self.extraction_tier == "schema.org"
                else "embarque leurs donnees dans le JSON qu'elle hydrate "
                     "(voir sources/embedded.py)."
            )
            lines = [
                "Source exploitable sans ouvrir les annonces: la page de resultats "
                + source_of_truth,
                f"Une requete par page de resultats au lieu de {self.results_listings}: "
                "moins de charge pour le site, plus de couverture pour vous.",
            ]
            if self.results_missing:
                lines.append(
                    "Manque sur la liste: " + ", ".join(self.results_missing)
                    + ". Ces champs viendront de la page d'annonce pour la selection finale."
                )
            if self.links_found == 0:
                lines.append(
                    "Les liens d'annonce ne sont pas dans le HTML (ajoutes en JavaScript): "
                    "c'est normal ici, la voie liste ne s'en sert pas."
                )
            lines.append(f"Passer `verified: true` dans sites/{self.source}.yaml.")
            return lines
        if level == "js":
            if self.note:
                return [
                    "La page ne porte pas ses annonces et aucun navigateur n'est "
                    "installe pour aller voir plus loin.",
                    'Installer le rendu: pip install -e ".[browser]" puis '
                    "python -m playwright install chromium",
                    f"Relancer ensuite: carexpert diagnose -s {self.source} "
                    f'--url "{self.url}" --browser',
                ]
            return [
                "Relancer avec `--browser`: la page est peut-etre rendue cote client sans "
                "embarquer son etat, et un rendu reel tranchera en une commande.",
                "Si le rendu revient vide lui aussi, passer par les alertes natives du site, "
                "puis `carexpert analyse-url` annonce par annonce.",
                "Ou negocier un acces API/partenaire: c'est la seule voie propre a l'echelle.",
            ]
        if level == "motif" and self.suggested_pattern:
            return [
                f"Remplacer `listing_link_pattern` par: {self.suggested_pattern}",
                f"Fichier: src/carexpert/sources/sites/{self.source}.yaml",
                "Puis relancer ce diagnostic.",
            ]
        if level == "motif":
            return [
                "Ouvrir la page dans un navigateur et relever la forme des URL d'annonce.",
                f"Renseigner `listing_link_pattern` dans sites/{self.source}.yaml.",
            ]
        if level in ("extraction", "partiel"):
            missing = Counter(f for s in self.samples for f in s.missing_critical)
            hints = ", ".join(f"{name} ({count})" for name, count in missing.most_common(4))
            return [
                f"Champs manquants sur l'echantillon: {hints or 'aucun'}.",
                f"Ajouter des selecteurs CSS de secours dans sites/{self.source}.yaml, section `selectors`.",
                "Les champs restent extraits du schema.org quand il est present: ne completer que les trous.",
            ]
        return [
            f"Source exploitable. Passer `verified: true` dans sites/{self.source}.yaml.",
            f'Lancer un vrai scan: carexpert scan --source {self.source} --url "{self.url}" --deep 5',
        ]

    def _failure_actions(self) -> list[str]:
        """Quoi faire quand rien n'est sorti, selon ce qui a repondu.

        Ces trois cas tombaient dans le fourre-tout de fin, qui conseille
        `verified: true`: un diagnostic qui declare exploitable une source
        injoignable est pire que pas de diagnostic du tout.
        """
        refused = (
            bool(self.protection)
            or "anti-bot" in self.error
            or self.status in (401, 403, 429)
        )
        if refused:
            named = f" ({self.protection})" if self.protection else ""
            return [
                f"Le site a refuse la requete{named}. Ne pas insister: contourner une "
                "protection changerait la nature juridique de l'acte.",
                "Verifier d'abord que ce refus vient bien du site: depuis un reseau "
                "d'entreprise ou un conteneur, un proxy sortant repond 403 a sa place.",
                "Un rendu navigateur (--browser) lit la page comme un visiteur le "
                "ferait; si le site repond encore par un defi, la collecte s'arrete.",
                "Sinon: alertes natives du site puis `carexpert analyse-url`, ou "
                "l'agregateur `leparking` qui republie une partie de ces annonces.",
                "A l'usage serieux: demander un acces professionnel au site.",
            ]
        return [
            "Le site n'a pas repondu. Verifier l'URL dans un navigateur, et que la "
            "machine a bien un acces sortant vers ce domaine.",
            f"Relancer ensuite: carexpert diagnose -s {self.source} --url \"{self.url}\"",
            "Ne pas passer `verified: true`: rien n'a ete verifie.",
        ]


def suggest_link_pattern(html: str, base_url: str) -> tuple[str | None, int]:
    """Infer the shape of advert URLs from the links a page actually contains.

    Groups every internal link by its path shape (digits and slugs masked),
    then returns a regex for the most repeated shape that carries an
    identifier. Advert links are, by construction, the ones repeated dozens
    of times on a results page.
    """
    from .structured import _soup

    host = urlparse(base_url).netloc
    shapes: Counter[str] = Counter()
    examples: dict[str, str] = {}

    for anchor in _soup(html).find_all("a", href=True):
        href = urljoin(base_url, anchor["href"].split("#")[0].split("?")[0])
        parsed = urlparse(href)
        if parsed.netloc and parsed.netloc != host:
            continue
        path = parsed.path.rstrip("/")
        if not path or path.count("/") < 1:
            continue
        shape = _shape(path)
        if not re.search(r"\\d\{|\\d\+", shape):
            continue                      # no identifier: not an advert link
        shapes[shape] += 1
        examples.setdefault(shape, path)

    if not shapes:
        return None, 0
    shape, count = shapes.most_common(1)[0]
    if count < 3:
        return None, count
    return shape, count


def _shape(path: str) -> str:
    """`/annonce/peugeot-308-123456` -> `/annonce/[^/]+-\\d{4,}`."""
    parts = []
    for segment in path.split("/"):
        if not segment:
            continue
        if re.fullmatch(r"\d+", segment):
            parts.append(r"\d+")
        elif re.fullmatch(r"[0-9a-f]{8}-[0-9a-f-]{20,}", segment):
            parts.append(r"[0-9a-f-]{30,}")
        elif re.search(r"\d{4,}", segment):
            parts.append(re.sub(r"[\w-]*\d{4,}[\w-]*", r"[^/]+-?\\d{4,}", segment, count=1))
        else:
            parts.append(re.escape(segment))
    return "/" + "/".join(parts)


def diagnose_search(
    url: str,
    *,
    source: str = "inconnue",
    pattern: str = r"/\d{5,}",
    selectors: dict | None = None,
    samples: int = 3,
    fetcher: PoliteFetcher | None = None,
) -> DiagnosticReport:
    """Check one search URL end to end and say what to fix."""
    from .structured import extract_jsonld, extract_listing_links, find_item_list

    report = DiagnosticReport(url=url, source=source, pattern_used=pattern)
    owned = fetcher is None
    fetcher = fetcher or PoliteFetcher()

    try:
        report.robots_present = fetcher._robots_for(url) is not None
        report.robots_allows = fetcher.allowed(url)
        report.crawl_delay = fetcher.crawl_delay(url)
        if not report.robots_allows:
            return report

        started = time.monotonic()
        try:
            page = fetcher.get(url, use_cache=False)
        except RobotsDisallowed:
            report.robots_allows = False
            return report
        except BotProtection as exc:
            # Le site a repondu, et il a refuse. Ce n'est pas une panne.
            report.error = str(exc)
            report.protection = exc.protection or "protection anti-bot"
            return report
        except (BrowserUnavailable, FetchError) as exc:
            report.error = str(exc)
            return report
        report.elapsed_s = time.monotonic() - started
        report.status = page.status
        report.page_bytes = len(page.text)
        report.note = getattr(fetcher, "escalation_blocked", "")
        if not page.ok:
            report.protection = named_protection(page.text) or ""
            return report

        links = extract_listing_links(page.text, url, pattern)
        report.links_found = len(links)
        report.js_suspected = any(marker in page.text for marker in JS_MARKERS)
        report.rendered = page.rendered
        report.note = getattr(fetcher, "escalation_blocked", "")

        # Voie liste: ce que la page de resultats donne sans rien ouvrir.
        rows = extract_listings_from_search(page.text, base_url=url, source=source)
        report.results_listings = len(rows)
        if rows:
            report.extraction_tier = (
                "schema.org" if find_item_list(extract_jsonld(page.text))
                else "etat embarque"
            )
        missing: Counter[str] = Counter()
        for row in rows:
            enrich(row)
            absent = [name for name in CRITICAL_FIELDS if not _is_filled(row, name)]
            missing.update(absent)
            if not absent:
                report.results_complete += 1
        report.results_missing = [name for name, _ in missing.most_common(4)]
        if report.results_complete:
            return report

        if not links:
            suggestion, count = suggest_link_pattern(page.text, url)
            if suggestion:
                report.suggested_pattern = suggestion
                links = extract_listing_links(page.text, url, suggestion)
                report.links_found = len(links)

        for link in links[:samples]:
            report.samples.append(_diagnose_listing(fetcher, link, source, selectors))
    finally:
        if owned:
            fetcher.close()
    return report


def _diagnose_listing(
    fetcher: PoliteFetcher, url: str, source: str, selectors: dict | None
) -> SampleReport:
    sample = SampleReport(url=url)
    try:
        page = fetcher.get(url, use_cache=False)
    except (BotProtection, BrowserUnavailable, FetchError, RobotsDisallowed) as exc:
        sample.error = str(exc)
        return sample
    sample.status = page.status
    if not page.ok:
        sample.error = f"HTTP {page.status}"
        return sample

    nodes = extract_jsonld(page.text)
    vehicle = find_vehicle_node(nodes)
    sample.has_jsonld = vehicle is not None
    if vehicle is not None:
        raw_type = vehicle.get("@type") or ""
        sample.jsonld_types = raw_type if isinstance(raw_type, list) else [str(raw_type)]

    listing = extract_from_page(page.text, url=url, source=source, selectors=selectors)
    if listing is None:
        sample.error = "aucune donnee exploitable sur la page"
        sample.missing_critical = list(CRITICAL_FIELDS)
        return sample

    enrich(listing)
    sample.photo_count = len(listing.photos)
    for name in CRITICAL_FIELDS + USEFUL_FIELDS:
        if _is_filled(listing, name):
            sample.filled.append(name)
    sample.missing_critical = [f for f in CRITICAL_FIELDS if f not in sample.filled]
    return sample


def _is_filled(listing: ListingData, name: str) -> bool:
    value = getattr(listing, name, None)
    if value in (None, "", []):
        return False
    return getattr(value, "value", value) != "unknown"
