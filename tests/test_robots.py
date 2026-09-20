"""Lire un robots.txt comme la norme le dit.

Le respect du robots.txt est la premiere regle de ce projet, et le lecteur
de la bibliotheque standard se trompe dans les deux sens. Mesure sur le
robots.txt reel d'AutoScout24, releve le 2026-09-20.

Il bloque trop sur `?`: le site ecrit `Disallow: /lst?` pour viser la
recherche avec parametres, et `RuleLine.__init__` fait passer le motif par
`urlunparse(urlparse(path))`, ce qui supprime la query vide et laisse
`/lst` - un prefixe qui interdit alors `/lst/volkswagen/golf`, page que le
site autorise. Cette seule ligne a fait declarer la plus grosse source du
projet "INTERDIT PAR LE ROBOTS.TXT".

Il ne bloque pas assez sur les jokers: `Disallow: */util/*` est compare par
`startswith`, or aucune URL ne commence par `*`.
"""

from __future__ import annotations

import pytest

from carexpert.sources.robots import RobotsRules

#: Extrait du robots.txt reel d'AutoScout24, conserve tel quel: c'est lui
#: qui a revele les deux defauts.
AUTOSCOUT = """User-agent: proximic
Disallow:

User-agent: Mediapartners-Google
Allow: /

User-agent: Applebot
Allow: /auto/

User-agent: GPTBot
User-agent: ClaudeBot
User-agent: Google-Extended
User-agent: Applebot-Extended
User-agent: CCBot
Disallow: /

User-agent: *
Disallow: /dealerarea/
Disallow: /entry/
Disallow: */util/*
Disallow: */utils/*
Allow: /garages/*page=1
Disallow: /offres/-
Disallow: /lst?
Disallow: /lst/?
Disallow: /lst-moto?
Disallow: /recherche-avancee
Disallow: /listing-search-api/graphql
# MG, 18.02.2026
"""

UA = "CarExpertBot/0.1 (+contact: quelquun@exemple.fr)"


@pytest.fixture(scope="module")
def autoscout() -> RobotsRules:
    return RobotsRules.parse(AUTOSCOUT)


# --- le defaut qui a coute la plus grosse source --------------------------


def test_a_question_mark_in_a_rule_is_a_question_mark(autoscout):
    """`Disallow: /lst?` vise la recherche a parametres, pas tout `/lst`.

    Lu par la bibliotheque standard, ce motif devient `/lst` et interdit
    `/lst/volkswagen/golf`, que le site autorise.
    """
    assert autoscout.allowed(UA, "https://www.autoscout24.fr/lst/volkswagen/golf?atype=C")
    assert autoscout.allowed(UA, "https://www.autoscout24.fr/lst/renault/twingo?page=2")
    # Et ce que le site vise vraiment reste interdit.
    assert not autoscout.allowed(UA, "https://www.autoscout24.fr/lst?atype=C")
    assert not autoscout.allowed(UA, "https://www.autoscout24.fr/lst/?atype=C")


def test_a_wildcard_matches_something(autoscout):
    """`Disallow: */util/*` ne correspondait a rien: aucune URL ne commence
    par une etoile."""
    assert not autoscout.allowed(UA, "https://www.autoscout24.fr/quelque/util/chose")
    assert not autoscout.allowed(UA, "https://www.autoscout24.fr/a/utils/b")
    assert autoscout.allowed(UA, "https://www.autoscout24.fr/utilitaires/fourgon")


def test_an_allow_carves_an_exception_out_of_a_refusal(autoscout):
    """`Allow: /garages/*page=1` n'a de sens que si le joker fonctionne."""
    assert autoscout.allowed(UA, "https://www.autoscout24.fr/garages/paris?page=1")


def test_the_ordinary_pages_stay_allowed(autoscout):
    assert autoscout.allowed(UA, "https://www.autoscout24.fr/offres/vw-golf-abcd1234")
    assert not autoscout.allowed(UA, "https://www.autoscout24.fr/offres/-quelque-chose")
    assert not autoscout.allowed(UA, "https://www.autoscout24.fr/dealerarea/x")
    assert not autoscout.allowed(UA, "https://www.autoscout24.fr/recherche-avancee")


# --- a qui un groupe s'adresse -------------------------------------------


def test_a_crawler_named_explicitly_reads_its_own_group(autoscout):
    """Le site nomme ClaudeBot et lui interdit tout.

    Un robot nomme lit son groupe et ignore le groupe general, meme quand
    celui-ci est plus permissif.
    """
    for named in ("ClaudeBot/1.0", "GPTBot", "CCBot/2.0"):
        assert not autoscout.allowed(named, "https://www.autoscout24.fr/lst/volkswagen/golf")
    # CarExpert n'est aucun de ceux-la: c'est le groupe general qui vaut.
    assert autoscout.allowed(UA, "https://www.autoscout24.fr/lst/volkswagen/golf")


def test_the_most_specific_group_wins():
    rules = RobotsRules.parse(
        "User-agent: *\nDisallow: /\n\nUser-agent: monbot\nAllow: /\n"
    )
    assert rules.allowed("MonBot/2.0", "https://site.fr/x")
    assert not rules.allowed("AutreBot/1.0", "https://site.fr/x")


def test_consecutive_agent_lines_share_the_rules_that_follow():
    rules = RobotsRules.parse(
        "User-agent: alpha\nUser-agent: beta\nDisallow: /prive/\n"
    )
    for agent in ("alpha", "beta"):
        assert not rules.allowed(agent, "https://site.fr/prive/x")
        assert rules.allowed(agent, "https://site.fr/public")


# --- les regles de la norme ----------------------------------------------


def test_the_longest_matching_rule_wins():
    rules = RobotsRules.parse(
        "User-agent: *\nDisallow: /dossier/\nAllow: /dossier/public/\n"
    )
    assert not rules.allowed("bot", "https://site.fr/dossier/prive")
    assert rules.allowed("bot", "https://site.fr/dossier/public/page")


def test_an_allow_wins_a_tie():
    rules = RobotsRules.parse("User-agent: *\nDisallow: /page\nAllow: /page\n")
    assert rules.allowed("bot", "https://site.fr/page")


def test_an_empty_disallow_allows_everything():
    """`Disallow:` sans rien apres autorise tout, et n'est pas une regle de
    longueur zero qui battrait toutes les autres."""
    rules = RobotsRules.parse("User-agent: *\nDisallow:\n")
    assert rules.allowed("bot", "https://site.fr/n-importe-quoi")

    mixed = RobotsRules.parse("User-agent: *\nDisallow:\nDisallow: /prive/\n")
    assert mixed.allowed("bot", "https://site.fr/public")
    assert not mixed.allowed("bot", "https://site.fr/prive/x")


def test_a_dollar_anchors_to_the_end():
    rules = RobotsRules.parse("User-agent: *\nDisallow: /*.pdf$\n")
    assert not rules.allowed("bot", "https://site.fr/doc/fiche.pdf")
    assert rules.allowed("bot", "https://site.fr/doc/fiche.pdf.html")


def test_the_query_string_is_part_of_what_a_rule_addresses():
    rules = RobotsRules.parse("User-agent: *\nDisallow: /r?tri=prix\n")
    assert not rules.allowed("bot", "https://site.fr/r?tri=prix&page=2")
    assert rules.allowed("bot", "https://site.fr/r?tri=date")


def test_comments_and_blank_lines_are_ignored():
    rules = RobotsRules.parse(
        "# en-tete\n\nUser-agent: *   # le groupe general\nDisallow: /prive/  # secret\n"
    )
    assert not rules.allowed("bot", "https://site.fr/prive/x")
    assert rules.allowed("bot", "https://site.fr/public")


def test_a_crawl_delay_is_read():
    rules = RobotsRules.parse("User-agent: *\nCrawl-delay: 4.5\nDisallow: /x\n")
    assert rules.crawl_delay("bot") == 4.5
    assert RobotsRules.parse("User-agent: *\nDisallow:\n").crawl_delay("bot") is None


def test_an_empty_or_unreadable_file_forbids_nothing():
    for text in ("", "   \n\n", "n'importe quoi\nsans deux points\n"):
        assert RobotsRules.parse(text).allowed("bot", "https://site.fr/x")


# --- branche dans le collecteur -------------------------------------------


def test_the_fetcher_uses_these_rules(tmp_path, monkeypatch):
    """Le collecteur poli lit ce fichier-ci, pas celui de la bibliotheque."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    served: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            served.append(self.path)
            body = (AUTOSCOUT.encode() if self.path == "/robots.txt"
                    else b"<html><body>page</body></html>")
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # pragma: no cover - silence
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host = f"http://127.0.0.1:{server.server_address[1]}"

    monkeypatch.setenv("CAREXPERT_CACHE_DIR", str(tmp_path / "cache"))
    from carexpert import config
    from carexpert.sources.fetcher import PoliteFetcher

    config.get_settings.cache_clear()
    try:
        fetcher = PoliteFetcher(user_agent=UA, delay=0.01)
        assert fetcher.allowed(f"{host}/lst/volkswagen/golf?atype=C")
        assert not fetcher.allowed(f"{host}/lst?atype=C")
        assert not fetcher.allowed(f"{host}/dealerarea/x")
        # Le fichier n'est lu qu'une fois par hote.
        assert served.count("/robots.txt") == 1
        fetcher.close()
    finally:
        server.shutdown()
        config.get_settings.cache_clear()
