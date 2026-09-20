"""The diagnostic must name the real problem, not just fail."""

from __future__ import annotations

import re

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
                 robots: bool = True, status: int = 200) -> None:
        self.pages = pages
        self._allowed = allowed
        self._robots = robots
        self.status = status
        self.calls: list[str] = []

    def _robots_for(self, url):
        return object() if self._robots else None

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


def test_a_refusal_is_reported_as_a_refusal_not_a_breakdown():
    """Un 403 est une decision du site, pas une panne.

    Les afficher pareil menait a la meme suite - relancer - alors qu'un
    refus ne se relance pas, il se respecte.
    """
    fetcher = FakeFetcher({"/recherche": SEARCH_OK}, status=403)
    report = diagnose_search("https://site.fr/recherche", source="test", fetcher=fetcher)
    assert report.verdict()[0] == "bloque"
    assert "403" in report.verdict()[1]
    assert any("Ne pas insister" in action for action in report.actions())


def test_a_server_error_is_still_a_breakdown():
    fetcher = FakeFetcher({"/recherche": SEARCH_OK}, status=500)
    report = diagnose_search("https://site.fr/recherche", source="test", fetcher=fetcher)
    assert report.verdict()[0] == "echec"
    assert "500" in report.verdict()[1]


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


def test_zero_fields_everywhere_points_at_the_links_not_the_selectors(tmp_path):
    """Mesure sur leparking: page de 174 Ko, 16 liens, 0 champ sur 3 annonces.

    Le diagnostic conseillait d'ajouter des selecteurs CSS. Mauvais conseil:
    les liens suivis etaient des pages de categorie (/voiture-occasion/
    Coupe-...html), et une categorie n'a ni prix ni kilometrage, quels que
    soient les selecteurs qu'on lui applique.
    """
    from carexpert.sources.diagnose import DiagnosticReport, SampleReport

    report = DiagnosticReport(url="https://www.leparking.fr/x", source="leparking")
    report.status = 200
    report.page_bytes = 174_425
    report.links_found = 16
    report.samples = [
        SampleReport(url="https://www.leparking.fr/voiture-occasion/Coupe-occasion.html",
                     status=200, missing_critical=["price_eur", "km", "year", "make"]),
        SampleReport(url="https://www.leparking.fr/voiture-occasion/SUV-occasion.html",
                     status=200, missing_critical=["price_eur", "km", "year", "make"]),
    ]

    actions = report.actions()
    assert report.verdict()[0] == "extraction"
    assert any("pages de categorie" in action for action in actions), actions
    assert any("listing_link_pattern" in action for action in actions), actions
    assert not any("section `selectors`" in action for action in actions), actions


def test_partly_filled_samples_still_point_at_the_selectors():
    """Des champs qui sortent a moitie, eux, sont bien un trou d'extraction."""
    from carexpert.sources.diagnose import DiagnosticReport, SampleReport

    report = DiagnosticReport(url="https://site.fr/x", source="test")
    report.status = 200
    report.links_found = 12
    report.samples = [
        SampleReport(url="https://site.fr/annonce/1", status=200,
                     filled=["title", "price"], missing_critical=["km", "year"]),
    ]
    actions = report.actions()
    assert any("selectors" in action for action in actions), actions


def test_the_page_can_be_written_out_for_inspection(tmp_path):
    """Adapter un lecteur demande la page, pas son resume."""
    target = tmp_path / "page.html"
    fetcher = FakeFetcher({"/recherche": SEARCH_OK})
    report = diagnose_search("https://site.fr/recherche", source="test",
                             fetcher=fetcher, save_to=target)
    assert target.read_text(encoding="utf-8") == SEARCH_OK
    # Le chemin complet: "page.html" seul ne dit pas ou chercher.
    assert report.saved_to == str(target.resolve())
    assert report.saved_to.startswith("/")


def test_the_page_proposes_the_pattern_its_own_links_repeat():
    """Le motif attrape des categories; la page sait lequel est le bon.

    Sur une page de resultats, la forme d'URL d'une annonce est par
    construction la plus repetee. Quand aucun echantillon ne rend un champ,
    on la demande a la page au lieu de la deviner.
    """
    page = "<html><body>" + "".join(
        f'<a href="/voiture-occasion/annonce/renault-twingo-{i}--{9000000 + i}.html">T</a>'
        for i in range(12)
    ) + (
        '<a href="/voiture-occasion/collection.html">Collection</a>'
        '<a href="/voiture-occasion/Coupe-occasion.html">Coupe</a>'
    ) + "</body></html>"

    fetcher = FakeFetcher({"/recherche": page, "/voiture-occasion/": "<html></html>"})
    report = diagnose_search("https://www.leparking.fr/recherche", source="leparking",
                             pattern=r"/voiture-occasion/[^\"'?#]+\.html", fetcher=fetcher)

    assert report.samples and not any(s.filled for s in report.samples)
    assert report.suggested_pattern
    assert "annonce" in report.suggested_pattern
    assert any("Motif deduit de la page" in action for action in report.actions())


HOMEPAGE = """<html><head><title>Le Parking</title>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"WebSite",
"url":"https://www.leparking.fr/","name":"leparking",
"potentialAction":{"@type":"SearchAction",
"target":"https://www.leparking.fr/voiture-occasion/{search_term_string}.html",
"query-input":"required name=search_term_string"}}</script></head>
<body>
<a href="/voiture-occasion/renault.html">Renault</a>
<a href="/voiture-occasion/collection.html">Collection</a>
<a href="/voiture-occasion/Coupe-occasion.html">Coupe</a>
</body></html>"""


def test_a_wrong_search_url_lands_on_the_homepage_in_http_200():
    """Mesure sur leparking: /voitures-occasion/ au pluriel redirige sur
    l'accueil. HTTP 200, 170 Ko, une page pleine: rien ne la distingue d'un
    resultat vide, et le diagnostic accusait le motif de lien.

    Le site publie pourtant la bonne forme dans le `SearchAction` de son
    JSON-LD. C'est la reponse, ecrite par le site lui-meme.
    """
    fetcher = FakeFetcher({"/voitures-occasion/": HOMEPAGE,
                           "/voiture-occasion/": "<html><body>Categorie</body></html>"})
    report = diagnose_search(
        "https://www.leparking.fr/voitures-occasion/renault-twingo.html",
        source="leparking", pattern=r"/voiture-occasion/[^\"'?#]+\.html",
        fetcher=fetcher,
    )

    assert report.declared_search == (
        "https://www.leparking.fr/voiture-occasion/{search_term_string}.html"
    )
    actions = report.actions()
    assert any("declare lui-meme sa forme de recherche" in a for a in actions), actions
    assert any("redirige sur l'accueil" in a for a in actions), actions


def test_the_declared_search_url_is_read_from_the_site_markup():
    from carexpert.sources.structured import declared_search_url

    assert declared_search_url(HOMEPAGE) == (
        "https://www.leparking.fr/voiture-occasion/{search_term_string}.html"
    )
    # Pas de SearchAction, pas d'invention.
    assert declared_search_url("<html><body>rien</body></html>") is None
    assert declared_search_url(SEARCH_OK) is None


LEPARKING_SEARCH = "https://www.leparking.fr/voiture-occasion/renault-twingo.html"


def test_the_inferred_pattern_is_not_tied_to_one_trim():
    """Mesure sur leparking: le motif proposé figeait `twingo-2-rip-curl`.

    Deux annonces de finitions differentes ont des chemins differents, donc
    masquer les chiffres seuls les met dans deux groupes et partage le
    compte entre eux. Le gagnant etait la finition la plus representee, et
    le motif propose ne matchait qu'elle.
    """
    pattern, count = suggest_link_pattern(LEPARKING_REAL, LEPARKING_SEARCH)

    assert pattern and count == 4
    # La finition ne doit pas rester dans le motif: elle change a chaque
    # annonce, c'est le contraire d'une route.
    assert "authentique" not in pattern and "dynamique" not in pattern
    assert re.search(pattern, "/voiture-occasion-detail/renault-twingo/"
                              "renault-twingo-iii-sce-70-zen/M3X9QB2R.html")


def test_the_inferred_pattern_is_not_tied_to_the_model_searched():
    """`renault-twingo` est litteral sur cette page, et pourtant variable.

    Toutes les annonces d'une recherche Twingo sont des Twingo: le segment
    ne varie pas, mais il vient de la recherche, pas de la route du site. Un
    motif qui le fige ne sert qu'a cette recherche-la.
    """
    pattern, _ = suggest_link_pattern(LEPARKING_REAL, LEPARKING_SEARCH)

    assert "twingo" not in pattern
    assert re.search(pattern, "/voiture-occasion-detail/peugeot-208/"
                              "peugeot-208-puretech-130/A7Z1KD8N.html")


def test_the_inferred_pattern_still_refuses_the_category_pages():
    """Ce qui a coute deux tours: `/voiture-occasion/collection.html`."""
    pattern, _ = suggest_link_pattern(LEPARKING_REAL, LEPARKING_SEARCH)

    for category in ("/voiture-occasion/renault-twingo.html",
                     "/tools/A25I41PZ/0/P/PL.html",
                     "/credit-auto.html",
                     "/vendez-votre-voiture.html"):
        assert not re.search(pattern, category), category


def test_a_single_odd_link_does_not_cancel_the_whole_inference():
    """Une annonce a l'identifiant plus court annulait toute la deduction.

    `all(...)` sur les valeurs d'une position veut dire qu'un lien atypique
    sur quarante suffit a ne plus rien proposer. Mesure: le diagnostic
    proposait un motif au tour precedent, et plus rien au suivant.
    """
    links = [
        f'<a href="/detail/renault-twingo/v{i}/twingo-{800000 + i}.html">x</a>'
        for i in range(6)
    ] + ['<a href="/detail/renault-twingo/v9/twingo-42.html">court</a>']
    html = "<html><body>" + "".join(links) + "</body></html>"

    pattern, count = suggest_link_pattern(html, "https://site.fr/voiture-occasion/twingo.html")

    assert pattern, "un lien atypique ne doit pas annuler la deduction"
    assert count == 7
    assert re.search(pattern, "/detail/renault-twingo/v3/twingo-800003.html")


def test_the_page_lists_its_own_url_families_when_nothing_matches():
    """"Ouvrir la page et relever la forme des URL" est inutilisable a 476 Ko."""
    from carexpert.sources.diagnose import internal_link_shapes

    shapes = internal_link_shapes(LEPARKING_REAL, LEPARKING_SEARCH)
    families = {shape for shape, _, _ in shapes}

    assert "/voiture-occasion-detail/*/*/*" in families
    assert "/voiture-occasion/*" in families
    # La plus frequente d'abord: c'est celle des annonces.
    assert shapes[0][0] == "/voiture-occasion-detail/*/*/*"
    assert shapes[0][1] == 4
    assert "voiture-occasion-detail" in shapes[0][2]


def test_a_failed_pattern_prints_the_families_to_choose_from():
    fetcher = FakeFetcher({"/voiture-occasion/": LEPARKING_REAL})
    report = diagnose_search(LEPARKING_SEARCH, source="leparking",
                             pattern=r"/rien-de-tel/\d+", fetcher=fetcher)

    assert report.links_found >= 0
    assert report.link_shapes
    actions = report.actions()
    assert any("Formes d'URL internes" in action for action in actions), actions
    assert any("voiture-occasion-detail" in action for action in actions), actions


#: Forme reelle relevee sur une page de resultats leparking: 75 annonces,
#: identifiant alphanumerique, et 195 liens vers la recherche elle-meme.
LEPARKING_REAL = """<html><body>
<a href="/voiture-occasion-detail/renault-twingo/renault-twingo-ii-1-2-60-authentique/K5L7PC4Q.html">1</a>
<a href="/voiture-occasion-detail/renault-twingo/renault-twingo-iii-sce-70-zen/M3X9QB2R.html">2</a>
<a href="/voiture-occasion-detail/renault-twingo/renault-twingo-ii-1-5-dci-dynamique/P8W2NF6T.html">3</a>
<a href="/voiture-occasion-detail/renault-twingo/renault-twingo-i-1-2-16v/R4J6HV1Y.html">4</a>
<a href="/voiture-occasion/renault-twingo.html">recherche</a>
<a href="/tools/A25I41PZ/0/P/PL.html">outil</a>
<a href="/credit-auto.html">credit</a>
<a href="/vendez-votre-voiture.html">vendre</a>
</body></html>"""


def test_an_identifier_is_not_always_a_number():
    """leparking nomme ses annonces `K5L7PC4Q`: lettres et chiffres, 8 signes.

    Trois motifs de lien exigeant des chiffres ont echoue d'affilee sur une
    page qui affichait pourtant 75 annonces, et la deduction automatique est
    restee muette pour la meme raison.
    """
    pattern, count = suggest_link_pattern(LEPARKING_REAL, LEPARKING_SEARCH)

    assert pattern, "un identifiant alphanumerique reste un identifiant"
    assert count == 4
    assert re.search(pattern, "/voiture-occasion-detail/renault-twingo/"
                              "renault-twingo-iii-sce-70-zen/M3X9QB2R.html")
    # Et il generalise: ni le modele cherche, ni la finition ne sont figes.
    assert re.search(pattern, "/voiture-occasion-detail/peugeot-208/"
                              "peugeot-208-puretech-130/A7Z1KD8N.html")
    assert not re.search(pattern, "/voiture-occasion/renault-twingo.html")


def test_the_shipped_pattern_reads_the_real_page():
    from carexpert.sources.configured import load_site_configs
    from carexpert.sources.structured import _listing_id, extract_listing_links

    pattern = load_site_configs()["leparking"]["listing_link_pattern"]
    links = extract_listing_links(LEPARKING_REAL, LEPARKING_SEARCH, pattern)

    assert len(links) == 4
    # Invariant 9: un identifiant par vehicule. Ici ce sont les codes du site,
    # pas la finition qu'ils partagent parfois.
    assert {_listing_id(link) for link in links} == {
        "K5L7PC4Q", "M3X9QB2R", "P8W2NF6T", "R4J6HV1Y"
    }
    # Les pieges de la meme page: la recherche elle-meme, citee 195 fois, et
    # un outil dont l'URL porte aussi un code alphanumerique.
    assert not any("/voiture-occasion/renault-twingo" in link for link in links)
    assert not any("/tools/" in link for link in links)


def test_the_file_extension_is_not_part_of_the_identifier():
    from carexpert.sources.structured import _listing_id

    base = "https://www.leparking.fr/voiture-occasion-detail/renault-twingo/x/"
    assert _listing_id(base + "K5L7PC4Q.html") == "K5L7PC4Q"
    # Et un identifiant sans extension n'est pas ampute pour autant.
    assert _listing_id(base + "K5L7PC4Q") == "K5L7PC4Q"


def test_a_protection_answering_in_http_200_is_still_named():
    """Un interstitiel anti-bot repond parfois 200, avec un corps court.

    Ne chercher le nom que sur les reponses en erreur revient a rendre ce
    refus-la comme une panne de configuration, et a conseiller de corriger
    un motif de lien qui n'a rien a se reprocher.
    """
    interstitial = (
        "<html><head><title>leboncoin.fr</title></head><body>"
        "<script src='https://geo.captcha-delivery.com/captcha/?initialCid=A'></script>"
        "</body></html>"
    )
    fetcher = FakeFetcher({"/recherche": interstitial})
    report = diagnose_search("https://www.leboncoin.fr/recherche", source="leboncoin",
                             fetcher=fetcher)

    assert report.status == 200
    assert "captcha-delivery" in report.protection
    assert report.verdict()[0] == "bloque"
    assert any("Ne pas insister" in action for action in report.actions())


def test_the_url_families_are_shown_when_links_match_but_yield_nothing():
    """Le cas pour lequel `internal_link_shapes` a ete ecrit.

    Des liens reconnus, pas un champ derriere: c'est la situation leparking
    exacte (seize liens, zero champ). La fonctionnalite ne se declenchait que
    dans l'autre branche, celle ou aucun lien ne correspondait.
    """
    fetcher = FakeFetcher({"/voiture-occasion/": LEPARKING_REAL,
                           "/tools/": "<html><body>outil</body></html>"})
    report = diagnose_search(LEPARKING_SEARCH, source="leparking",
                             pattern=r"/tools/[^\"'?#]+\.html", fetcher=fetcher)

    assert report.samples and not any(sample.filled for sample in report.samples)
    assert report.link_shapes, "la page connait ses propres familles d'URL"
    assert any("Formes d'URL internes" in action for action in report.actions())
