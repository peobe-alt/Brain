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
from pathlib import Path
from collections import Counter, defaultdict
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
    #: Ou la page lue a ete ecrite, quand `--save` le demande.
    saved_to: str = ""
    #: Les formes d'URL internes que la page contient, la plus repetee en
    #: tete, avec un exemple. Quand aucun lien n'est reconnu, c'est la seule
    #: chose qui dise quoi ecrire a la place: le motif se lit sur la page, il
    #: ne se devine pas de loin.
    link_shapes: list[tuple[str, int, str]] = field(default_factory=list)
    #: La forme d'URL de recherche que le site declare pour lui-meme. Ne sert
    #: que quand la notre n'a rien donne: c'est alors la reponse, ecrite par
    #: le site.
    declared_search: str = ""
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
        if level == "motif":
            lines: list[str] = []
            if self.suggested_pattern:
                lines.append(
                    f"Remplacer `listing_link_pattern` par: {self.suggested_pattern}"
                )
            lines += self._shape_lines()
            if self.declared_search:
                lines.append(
                    f"Le site declare sa forme de recherche: {self.declared_search}"
                )
            lines.append(f"Fichier: src/carexpert/sources/sites/{self.source}.yaml")
            lines.append("Puis relancer ce diagnostic.")
            return lines
        if level == "extraction" and self.samples and all(
            not sample.filled for sample in self.samples
        ):
            # Pas un champ sur aucune annonce, alors que la page de resultats
            # fait 170 Ko: ce ne sont pas les selecteurs qui manquent, ce sont
            # les liens qui ne pointent pas sur des annonces. Une page de
            # categorie n'a ni prix ni kilometrage, et n'en aura jamais.
            lines = [
                "Aucun champ sur aucune annonce: les liens suivis ne sont "
                "probablement pas des annonces mais des pages de categorie.",
                f"Verifier en ouvrant: {self.samples[0].url}",
            ]
            lines += self._shape_lines()
            if self.declared_search:
                lines += [
                    f"Le site declare lui-meme sa forme de recherche: "
                    f"{self.declared_search}",
                    "La comparer a l'URL testee ci-dessus: une URL de recherche "
                    "fausse redirige sur l'accueil, en HTTP 200 et page pleine.",
                ]
            if self.suggested_pattern:
                lines += [
                    f"Motif deduit de la page elle-meme: {self.suggested_pattern}",
                    f"Le mettre dans `listing_link_pattern`, sites/{self.source}.yaml, "
                    "puis relancer ce diagnostic.",
                ]
            else:
                lines.append(
                    f"Resserrer `listing_link_pattern` dans sites/{self.source}.yaml."
                )
            lines.append(
                f"Pour examiner la page: carexpert diagnose -s {self.source} "
                f'--url "{self.url}" --save page.html'
            )
            return lines
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

    def _shape_lines(self) -> list[str]:
        """Les familles d'URL de la page, avec un exemple chacune.

        "Ouvrir la page dans un navigateur et relever la forme des URL" est
        vrai mais inutilisable sur 476 Ko. La page connait ses familles.
        """
        if not self.link_shapes:
            return []
        lines = ["Formes d'URL internes presentes sur la page, la plus frequente en tete:"]
        lines += [
            f"    {count:>4} x  {example}" for _, count, example in self.link_shapes
        ]
        return lines

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

    Advert links are, by construction, the ones repeated dozens of times on a
    results page. The trick is grouping them: two adverts of different trims
    live at `/detail/renault-twingo/twingo-2-rip-curl/...` and
    `/detail/renault-twingo/twingo-3-zen/...`, so masking digits alone puts
    them in separate groups and splits the count between them.

    So links are grouped by their route skeleton - first segment and depth -
    and only then are the segments that differ *within* a group widened.
    Measured on leparking: grouping on the full slug proposed a pattern that
    matched Twingo Rip Curl adverts and nothing else.
    """
    from .structured import _soup

    host = urlparse(base_url).netloc
    groups: dict[tuple[str, int], list[list[str]]] = defaultdict(list)

    for anchor in _soup(html).find_all("a", href=True):
        href = urljoin(base_url, anchor["href"].split("#")[0].split("?")[0])
        parsed = urlparse(href)
        if parsed.netloc and parsed.netloc != host:
            continue
        segments = [part for part in parsed.path.split("/") if part]
        if not segments:
            continue
        skeleton = (segments[0], len(segments))
        if segments not in groups[skeleton]:
            groups[skeleton].append(segments)

    # Ce que l'URL de recherche contient deja ne fait pas partie de la route:
    # sur une page Twingo, toutes les annonces sont des Twingo, et un motif
    # qui fige `renault-twingo` ne servira qu'a cette recherche-la.
    searched = {part for part in urlparse(base_url).path.split("/") if part}
    searched |= {part.removesuffix(".html") for part in searched}

    for segments_list in sorted(groups.values(), key=len, reverse=True):
        if len(segments_list) < 3:
            continue
        pattern = _pattern_from_group(segments_list, searched)
        if pattern:
            return pattern, len(segments_list)
    return None, 0


def internal_link_shapes(
    html: str, base_url: str, limit: int = 6
) -> list[tuple[str, int, str]]:
    """The site's own internal URL families: (shape, count, one example).

    When nothing matches, "open the page in a browser and work out the shape
    of advert URLs" is true but useless: the page is 476 KB. The page already
    knows its families, and printing them turns three rounds of guessing into
    one look.
    """
    from .structured import _soup

    host = urlparse(base_url).netloc
    counts: Counter[str] = Counter()
    examples: dict[str, str] = {}

    for anchor in _soup(html).find_all("a", href=True):
        href = urljoin(base_url, anchor["href"].split("#")[0].split("?")[0])
        parsed = urlparse(href)
        if parsed.netloc and parsed.netloc != host:
            continue
        segments = [part for part in parsed.path.split("/") if part]
        if not segments:
            continue
        shape = "/" + "/".join(
            [segments[0]] + ["*"] * (len(segments) - 1)
        )
        counts[shape] += 1
        examples.setdefault(shape, parsed.path)

    return [(shape, count, examples[shape]) for shape, count in counts.most_common(limit)]


def _mostly(values: set[str], pattern: str, share: float = 0.7) -> bool:
    matching = sum(1 for value in values if re.search(pattern, value))
    return matching >= max(2, int(len(values) * share))


def _pattern_from_group(group: list[list[str]], searched: set[str]) -> str | None:
    """One regex covering a group of same-shaped paths.

    A position every path agrees on stays literal - that is the site's own
    route, and keeping it is what stops the pattern matching category pages.
    A position they disagree on is the variable part: the make, the trim, the
    advert's own slug.
    """
    parts: list[str] = []
    has_identifier = False
    for index in range(len(group[0])):
        values = {segments[index] for segments in group}
        if len(values) == 1 and not (values & searched):
            parts.append(re.escape(next(iter(values))))
            continue
        if len(values) == 1:
            parts.append(r"[^/]+")
            continue
        pattern, is_identifier = _position_pattern(values)
        parts.append(pattern)
        has_identifier = has_identifier or is_identifier
    # Sans identifiant, le motif attrape aussi bien les pages de categorie:
    # c'est exactement le defaut qu'on vient de corriger.
    return "/" + "/".join(parts) if has_identifier else None


def _position_pattern(values: set[str]) -> tuple[str, bool]:
    """A regex for one varying path segment, and whether it identifies.

    An identifier is not necessarily a number. leparking names its adverts
    `K5L7PC4Q`: eight characters, letters and digits, no separator. Three
    link patterns in a row demanded digits, and the inference stayed silent
    for the same reason.
    """
    suffix = _common_extension(values)
    stems = {value[: -len(suffix)] if suffix else value for value in values}
    tail = re.escape(suffix)

    if all(re.fullmatch(r"\d+", stem) for stem in stems):
        return r"\d+" + tail, True
    if _mostly(stems, r"\d{4,}"):
        # La majorite, pas la totalite: une annonce a l'identifiant plus court
        # que les autres annulait la deduction entiere.
        return r"[^/]*\d{4,}[^/]*" + tail, True
    # Un code compact ou lettres et chiffres se melent: un identifiant, pas
    # un slug, qui lui porte des tirets et des mots.
    if all(
        re.fullmatch(r"[A-Za-z0-9]{5,16}", stem) and re.search(r"\d", stem)
        and re.search(r"[A-Za-z]", stem)
        for stem in stems
    ):
        return r"[A-Za-z0-9]{5,16}" + tail, True
    return r"[^/]+", False


def _common_extension(values: set[str]) -> str:
    """The file extension every value shares, if any."""
    extensions = {
        match.group(0) if (match := re.search(r"\.[a-z]{2,5}$", value)) else ""
        for value in values
    }
    return extensions.pop() if len(extensions) == 1 else ""


def diagnose_search(
    url: str,
    *,
    source: str = "inconnue",
    pattern: str = r"/\d{5,}",
    selectors: dict | None = None,
    samples: int = 3,
    fetcher: PoliteFetcher | None = None,
    save_to: Path | None = None,
) -> DiagnosticReport:
    """Check one search URL end to end and say what to fix."""
    from .structured import (
        declared_search_url,
        extract_jsonld,
        extract_listing_links,
        find_item_list,
    )

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
        if save_to is not None:
            # Adapter un lecteur demande la page, pas son resume. Sans ca, la
            # seule facon de la transmettre est de la recopier a la main.
            save_to.write_text(page.text, encoding="utf-8")
            # Le chemin complet, pas celui qu'on a tape: "leparking.html" ne
            # dit pas dans quel dossier chercher, et c'est la seule question
            # qu'on se pose ensuite.
            report.saved_to = str(save_to.resolve())
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
            report.link_shapes = internal_link_shapes(page.text, url)
            suggestion, count = suggest_link_pattern(page.text, url)
            if suggestion:
                report.suggested_pattern = suggestion
                links = extract_listing_links(page.text, url, suggestion)
                report.links_found = len(links)

        for link in links[:samples]:
            report.samples.append(_diagnose_listing(fetcher, link, source, selectors))

        # Des liens reconnus, et pas un champ derriere: le motif attrape autre
        # chose que des annonces. La page sait laquelle de ses formes d'URL est
        # la bonne, elle la repete vingt fois; on la lui demande.
        if report.samples and not any(sample.filled for sample in report.samples):
            suggestion, _ = suggest_link_pattern(page.text, url)
            if suggestion and suggestion != pattern:
                report.suggested_pattern = suggestion
            report.declared_search = declared_search_url(page.text) or ""
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
