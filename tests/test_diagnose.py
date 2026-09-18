"""The diagnostic must name the real problem, not just fail."""

from __future__ import annotations

from carexpert.sources.diagnose import diagnose_search, suggest_link_pattern
from carexpert.sources.fetcher import FetchResult

LISTING_OK = """<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Car","name":"Peugeot 308 SW BlueHDi 130",
 "brand":{"@type":"Brand","name":"Peugeot"},"model":"308 SW","vehicleModelDate":"2019",
 "fuelType":"diesel","vehicleTransmission":"Automatique",
 "mileageFromOdometer":{"@type":"QuantitativeValue","value":"98500","unitCode":"KMT"},
 "vehicleEngine":{"@type":"EngineSpecification","enginePower":{"value":"130","unitCode":"BHP"}},
 "description":"Carnet complet.","image":["/img/1.jpg","/img/2.jpg"],
 "offers":{"@type":"Offer","price":"15990","priceCurrency":"EUR"}}
</script></head><body>annonce</body></html>"""

LISTING_THIN = """<html><head>
<meta property="og:title" content="Peugeot 308 SW">
<meta property="og:description" content="Belle voiture">
</head><body>pas de donnees structurees</body></html>"""

SEARCH_OK = """<html><body>
<a href="/annonce/peugeot-308-100001">a</a><a href="/annonce/peugeot-308-100002">b</a>
<a href="/annonce/renault-clio-100003">c</a><a href="/annonce/golf-100004">d</a>
<a href="/aide">aide</a><a href="/cgu">cgu</a>
</body></html>"""

SEARCH_JS = """<html><body><div id="__next"></div>
<script id="__NEXT_DATA__" type="application/json">{"props":{}}</script>
<a href="/aide">aide</a></body></html>"""


class FakeFetcher:
    """Serves canned pages, so the diagnostic is testable without network."""

    def __init__(self, pages: dict[str, str], *, allowed: bool = True,
                 robots: bool = True, status: int = 200,
                 sitemaps: list[str] | None = None) -> None:
        self.pages = pages
        self._allowed = allowed
        self._robots = robots
        self._sitemaps = sitemaps or []
        self.status = status
        self.calls: list[str] = []

    def _robots_for(self, url):
        return object() if self._robots else None

    def sitemaps(self, url):
        return list(self._sitemaps) if self._robots else []

    def allowed(self, url):
        return self._allowed

    def crawl_delay(self, url):
        return 2.5

    def get(self, url, use_cache=True):
        self.calls.append(url)
        for key, html in self.pages.items():
            if key in url:
                return FetchResult(url=url, status=self.status, text=html)
        return FetchResult(url=url, status=404, text="")

    def close(self):
        return None


def test_a_working_source_is_declared_usable():
    fetcher = FakeFetcher({"/recherche": SEARCH_OK, "/annonce/": LISTING_OK})
    report = diagnose_search("https://site.fr/recherche", source="test",
                             pattern=r"/annonce/[\w-]+-\d+", fetcher=fetcher)
    assert report.verdict()[0] == "ok"
    assert report.links_found == 4
    assert report.usable_samples == 3
    assert all(s.has_jsonld for s in report.samples)
    assert any("verified: true" in action for action in report.actions())


def test_javascript_rendering_is_named_as_such():
    fetcher = FakeFetcher({"/recherche": SEARCH_JS})
    report = diagnose_search("https://site.fr/recherche", source="test",
                             pattern=r"/annonce/\d+", fetcher=fetcher)
    assert report.verdict()[0] == "js"
    assert report.js_suspected
    assert any("analyse-url" in action for action in report.actions())


def test_a_wrong_pattern_is_detected_and_a_fix_proposed():
    fetcher = FakeFetcher({"/recherche": SEARCH_OK, "/annonce/": LISTING_OK})
    report = diagnose_search("https://site.fr/recherche", source="test",
                             pattern=r"/cette-forme-nexiste-pas/\d+", fetcher=fetcher)
    # The suggestion must recover the adverts the wrong pattern missed.
    assert report.suggested_pattern is not None
    assert report.links_found == 4


def test_robots_disallow_stops_everything():
    fetcher = FakeFetcher({"/recherche": SEARCH_OK}, allowed=False)
    report = diagnose_search("https://site.fr/recherche", source="test", fetcher=fetcher)
    assert report.verdict()[0] == "interdit"
    assert fetcher.calls == []          # nothing was fetched
    assert any("Ne pas collecter" in action for action in report.actions())


def test_missing_data_points_at_the_selectors():
    fetcher = FakeFetcher({"/recherche": SEARCH_OK, "/annonce/": LISTING_THIN})
    report = diagnose_search("https://site.fr/recherche", source="test",
                             pattern=r"/annonce/[\w-]+-\d+", fetcher=fetcher)
    level, _ = report.verdict()
    assert level in ("extraction", "partiel")
    assert report.samples[0].missing_critical
    assert any("selectors" in action for action in report.actions())


def test_http_error_is_reported_plainly():
    fetcher = FakeFetcher({"/recherche": SEARCH_OK}, status=403)
    report = diagnose_search("https://site.fr/recherche", source="test", fetcher=fetcher)
    assert report.verdict()[0] == "echec"
    assert "403" in report.verdict()[1]


def test_pattern_suggestion_picks_the_repeated_shape():
    pattern, count = suggest_link_pattern(SEARCH_OK, "https://site.fr/recherche")
    assert pattern is not None
    assert count == 4


def test_pattern_suggestion_stays_silent_without_evidence():
    html = '<html><body><a href="/a">x</a><a href="/b">y</a></body></html>'
    pattern, _ = suggest_link_pattern(html, "https://site.fr/")
    assert pattern is None


def test_a_results_page_without_links_but_with_data_is_usable():
    """AutoScout24: pas un seul href, et pourtant toutes les annonces.

    Sans ce cas, le diagnostic conclut "site rendu en JavaScript, rien a
    extraire" et on abandonne une source parfaitement lisible.
    """
    from pathlib import Path

    html = (Path(__file__).parent / "fixtures" / "autoscout24_search.html").read_text(
        encoding="utf-8"
    )
    fetcher = FakeFetcher({"/lst/": html})
    report = diagnose_search(
        "https://www.autoscout24.fr/lst/volvo/v70",
        source="autoscout24",
        pattern=r"/offres/[^/?#]+-[0-9a-f]{8}-[0-9a-f-]{27}",
        fetcher=fetcher,
    )

    assert report.links_found == 0
    assert report.results_listings == 3
    assert report.results_complete == 3
    assert report.verdict()[0] == "liste"
    # Une seule requete: aucune annonce n'a ete ouverte pour en arriver la.
    assert len(fetcher.calls) == 1
    actions = " ".join(report.actions())
    assert "JavaScript" in actions and "verified: true" in actions


# --- Recherches qui ne quittent jamais le navigateur ------------------------
#
# TheParking range toute sa recherche derriere un `#!`. Un fragment n'est pas
# transmis au serveur (RFC 3986, section 3.5): l'URL collee demande la page
# d'accueil, et le diagnostic concluait "site rendu en JavaScript", ce qui
# envoyait chercher un navigateur headless pour un probleme d'URL.

HASHBANG_URL = (
    "https://www.theparking.eu/#!/used-cars/V70.html"
    "%3Fid_energie%3D1%26id_motorisation%3D12"
)

HOME_PAGE = """<html><body><div id="__next"></div>
<script id="__NEXT_DATA__" type="application/json">{"props":{}}</script>
<a href="/aide">aide</a></body></html>"""


def test_a_search_left_in_the_fragment_is_named_as_such():
    fetcher = FakeFetcher({"theparking.eu": HOME_PAGE})
    report = diagnose_search(HASHBANG_URL, source="theparking", fetcher=fetcher)

    # Le piege: les marqueurs JavaScript sont bien la, et pourtant ce n'est
    # pas le diagnostic. Le serveur n'a jamais vu la recherche.
    assert report.js_suspected
    assert report.verdict()[0] == "fragment"
    assert report.fragment_route == "!/used-cars/V70.html?id_energie=1&id_motorisation=12"
    assert report.fetched_url == "https://www.theparking.eu/"


def test_the_fragment_never_reaches_the_fetcher():
    fetcher = FakeFetcher({"theparking.eu": HOME_PAGE})
    diagnose_search(HASHBANG_URL, source="theparking", fetcher=fetcher)

    assert fetcher.calls == ["https://www.theparking.eu/"]


def test_the_dropped_filters_are_shown_back_decoded():
    """Le navigateur percent-encode le `?` derriere le `#`: illisible brut."""
    fetcher = FakeFetcher({"theparking.eu": HOME_PAGE})
    report = diagnose_search(HASHBANG_URL, source="theparking", fetcher=fetcher)

    actions = " ".join(report.actions())
    assert "id_energie=1" in actions and "id_motorisation=12" in actions
    assert "%3F" not in actions


def test_homepage_adverts_are_never_passed_off_as_the_search():
    """Le pire resultat possible: rendre les annonces d'une autre page.

    La page d'accueil d'un agregateur publie ses vedettes en JSON-LD. Sans
    ce cas, le diagnostic annonce "source exploitable, N annonces completes"
    pour une recherche que le serveur n'a jamais recue.
    """
    from pathlib import Path

    html = (Path(__file__).parent / "fixtures" / "autoscout24_search.html").read_text(
        encoding="utf-8"
    )
    fetcher = FakeFetcher({"theparking.eu": html})
    report = diagnose_search(HASHBANG_URL, source="theparking", fetcher=fetcher)

    assert report.verdict()[0] == "fragment"
    # Une seule requete: aucune annonce hors sujet n'a ete ouverte.
    assert len(fetcher.calls) == 1
    assert report.samples == []


def test_a_plain_anchor_is_not_a_route():
    """`#resultats` ancre une position dans la page: la recherche est dans l'URL."""
    fetcher = FakeFetcher({"/recherche": SEARCH_OK, "/annonce/": LISTING_OK})
    report = diagnose_search("https://site.fr/recherche?make=volvo#resultats",
                             source="test", pattern=r"/annonce/[\w-]+-\d+",
                             fetcher=fetcher)

    assert report.fragment_route == ""
    assert report.verdict()[0] == "ok"


# --- Ne pas laisser l'utilisateur chercher seul ----------------------------
#
# Dire "trouvez une URL servie par le serveur" est un renvoi, pas une action.
# Deux pistes sont deja payees au moment du verdict: le robots.txt (telecharge
# pour la politesse) annonce les sitemaps, et la page se nomme elle-meme.

HOME_WITH_LEADS = """<html><head>
<link rel="canonical" href="/used-cars/volvo-v70.html">
<link rel="alternate" hreflang="de" href="/de/used-cars/volvo-v70.html">
<link rel="stylesheet" href="/style.css">
</head><body><div id="__next"></div>
<script id="__NEXT_DATA__" type="application/json">{"props":{}}</script>
</body></html>"""


def test_a_dead_end_still_hands_back_the_leads_already_paid_for():
    fetcher = FakeFetcher(
        {"theparking.eu": HOME_WITH_LEADS},
        sitemaps=["https://www.theparking.eu/sitemap-index.xml"],
    )
    report = diagnose_search(HASHBANG_URL, source="theparking", fetcher=fetcher)
    actions = " ".join(report.actions())

    assert report.verdict()[0] == "fragment"
    assert "/used-cars/volvo-v70.html" in actions
    assert "sitemap-index.xml" in actions
    # Toujours une seule requete: les deux pistes sortent de ce qu'on avait deja.
    assert len(fetcher.calls) == 1


def test_a_javascript_site_gets_the_same_leads():
    """Meme impasse, meme sortie: le sitemap ne depend pas du rendu."""
    fetcher = FakeFetcher(
        {"/recherche": HOME_WITH_LEADS},
        sitemaps=["https://site.fr/sitemap.xml"],
    )
    report = diagnose_search("https://site.fr/recherche", source="test",
                             pattern=r"/annonce/\d+", fetcher=fetcher)

    assert report.verdict()[0] == "js"
    assert "sitemap.xml" in " ".join(report.actions())


def test_a_stylesheet_is_not_a_server_side_lead():
    """Sans filtre sur `rel`, chaque feuille de style passerait pour une piste."""
    fetcher = FakeFetcher({"theparking.eu": HOME_WITH_LEADS})
    report = diagnose_search(HASHBANG_URL, source="theparking", fetcher=fetcher)

    assert all("style.css" not in link for link in report.server_side_links)
    assert len(report.server_side_links) == 2


def test_no_leads_means_no_empty_promises():
    """Une page muette ne doit pas produire de ligne d'action vide."""
    fetcher = FakeFetcher({"theparking.eu": HOME_PAGE})
    report = diagnose_search(HASHBANG_URL, source="theparking", fetcher=fetcher)

    assert report.sitemaps == [] and report.server_side_links == []
    assert all(action.strip() for action in report.actions())
    assert not any("sitemap" in action.lower() for action in report.actions())


def test_a_refusal_gets_no_workaround():
    """Un robots.txt qui interdit est un refus, pas un probleme a resoudre.

    Les pistes serveur sont utiles face a une impasse technique. Face a un
    refus explicite, les afficher reviendrait a proposer un contournement.
    """
    fetcher = FakeFetcher(
        {"theparking.eu": HOME_WITH_LEADS},
        allowed=False,
        sitemaps=["https://www.theparking.eu/sitemap-index.xml"],
    )
    report = diagnose_search(HASHBANG_URL, source="theparking", fetcher=fetcher)
    actions = " ".join(report.actions())

    assert report.verdict()[0] == "interdit"
    assert "sitemap" not in actions.lower()
    assert "Ne pas collecter" in actions
    assert fetcher.calls == []


# --- Notre reseau, ou le site? ---------------------------------------------
#
# Cas rencontre pour de vrai sur theparking.eu: le proxy de sortie repond 403
# au CONNECT et le site n'est jamais joint. Le rapport disait "SITE
# INJOIGNABLE", ce qui invite a desactiver une source qui n'a rien fait, et
# la boucle de reprise repassait quatre fois sur un refus de politique.


class BlockedFetcher(FakeFetcher):
    """Sortie reseau refusee: rien ne sort de la machine."""

    def get(self, url, use_cache=True):
        from carexpert.sources.fetcher import NetworkBlocked

        self.calls.append(url)
        raise NetworkBlocked(
            "sortie reseau refusee pour www.theparking.eu (403 Forbidden); "
            "le site n'a pas ete joint"
        )


def test_a_blocked_egress_does_not_accuse_the_site():
    fetcher = BlockedFetcher({})
    report = diagnose_search(HASHBANG_URL, source="theparking", fetcher=fetcher)
    level, phrase = report.verdict()
    actions = " ".join(report.actions())

    assert level == "reseau"
    assert report.network_blocked
    assert "n'a pas ete joint" in phrase
    assert "Ce n'est pas le site qui refuse" in actions
    # Le piege: ne pas faire retirer une source qui n'a jamais ete testee.
    assert "ne pas la desactiver" in actions


def test_a_real_site_refusal_stays_a_site_refusal():
    """403 servi par le site: la distinction n'a de valeur que si elle tient."""
    fetcher = FakeFetcher({"/recherche": SEARCH_OK}, status=403)
    report = diagnose_search("https://site.fr/recherche", source="test", fetcher=fetcher)

    assert report.verdict()[0] == "echec"
    assert not report.network_blocked
