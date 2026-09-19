"""The command line, exercised for real.

This suite exists because a syntax error once shipped in `cli.py` while the
whole test suite stayed green: nothing imported it. The main interface of the
product deserves better than that.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture()
def cli(tmp_path, monkeypatch):
    monkeypatch.setenv("CAREXPERT_DATABASE_URL", f"sqlite:///{tmp_path / 'cli.db'}")
    monkeypatch.setenv("CAREXPERT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("COLUMNS", "200")

    from carexpert import config, db

    config.get_settings.cache_clear()
    db.reset_engine()
    from carexpert.cli import app

    yield app
    config.get_settings.cache_clear()
    db.reset_engine()


def _run(app, *args):
    result = runner.invoke(app, list(args))
    if result.exception and not isinstance(result.exception, SystemExit):
        raise result.exception
    return result


def test_help_lists_every_command(cli):
    result = _run(cli, "--help")
    assert result.exit_code == 0
    for command in ("scan", "deals", "show", "demo", "diagnose", "analyse-url", "serve", "watch"):
        assert command in result.output


def test_init_creates_the_database(cli):
    assert _run(cli, "init").exit_code == 0


def test_sources_are_listed_with_their_countries(cli):
    result = _run(cli, "sources")
    assert result.exit_code == 0
    assert "autoscout24" in result.output
    assert "demo" in result.output


def test_demo_runs_end_to_end_and_ranks_deals(cli):
    """200 annonces, pas 80: en dessous, le marche est trop maigre pour juger.

    Mesure: a 80 annonces la mediane est de 4 comparables par vehicule, et
    l'estimateur repond "A ESTIMER" 96 fois sur 100 - ce qui est la bonne
    reponse. Le test passait avant parce que la confiance se laissait porter
    par l'accord entre deux comparables, et l'accord entre deux points ne
    prouve rien.
    """
    result = _run(cli, "demo", "--size", "200")
    assert result.exit_code == 0
    assert "annonces vues" in result.output
    assert "A SAISIR" in result.output or "A VOIR" in result.output


def test_deals_then_show_render_a_full_report(cli):
    _run(cli, "demo", "--size", "80")
    listed = _run(cli, "deals", "--min-score", "0", "--limit", "5")
    assert listed.exit_code == 0

    from carexpert.db import Listing, select, session_scope

    with session_scope() as session:
        best = session.execute(
            select(Listing).where(Listing.score.is_not(None))
            .order_by(Listing.score.desc()).limit(1)
        ).scalar_one()
        listing_id = best.id

    shown = _run(cli, "show", str(listing_id))
    assert shown.exit_code == 0
    assert "Resume" in shown.output
    assert "Decomposition du score" in shown.output


def test_deals_on_an_empty_base_guides_the_user(cli):
    result = _run(cli, "deals")
    assert result.exit_code == 0
    assert "carexpert demo" in result.output


def test_show_on_a_missing_listing_fails_cleanly(cli):
    result = _run(cli, "show", "999999")
    assert result.exit_code == 1
    assert "introuvable" in result.output


def test_diagnose_rejects_an_unknown_source(cli):
    result = _run(cli, "diagnose", "--source", "site-inexistant")
    assert result.exit_code == 1
    assert "inconnue" in result.output


def test_a_watchlist_can_be_created_and_listed(cli):
    created = _run(cli, "watch", "add", "golf", "--make", "Volkswagen",
                   "--model", "Golf", "--price-max", "18000", "--min-score", "78")
    assert created.exit_code == 0
    listed = _run(cli, "watch", "list")
    assert "golf" in listed.output
    assert "78" in listed.output


def test_watch_run_without_watchlist_says_so(cli):
    result = _run(cli, "watch", "run")
    assert result.exit_code == 0
    assert "Aucune veille" in result.output


def test_scan_on_the_demo_source_reports_its_work(cli):
    result = _run(cli, "scan", "--source", "demo", "--limit", "60")
    assert result.exit_code == 0
    assert "estimees" in result.output


def test_a_pasted_placeholder_is_refused_before_it_costs_a_request():
    """"URL leparking" partait en requete et le diagnostic accusait le site.

    Mesure sur une vraie session: trois sources testees, trois "SITE
    INJOIGNABLE - le site n'a pas repondu", pour trois chaines qui n'etaient
    pas des adresses. Le seul moment ou l'on peut le savoir, c'est avant de
    partir.
    """
    from typer.testing import CliRunner

    from carexpert.cli import app

    runner = CliRunner()
    for bad in ("URL leparking", "pas une url", "www.leparking.fr", "ftp://site.fr/x"):
        result = runner.invoke(app, ["diagnose", "-s", "leparking", "--url", bad])
        assert result.exit_code == 2, bad
        assert "Ce n'est pas une URL" in result.output, bad

    # Et une vraie adresse passe le controle: il filtre la faute de frappe,
    # pas la source.
    result = runner.invoke(app, ["scan", "-s", "demo", "--url", "pas une url"])
    assert result.exit_code == 2
