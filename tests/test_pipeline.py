"""Ingestion, deduplication and the full scan."""

from __future__ import annotations

from datetime import datetime, timedelta

from carexpert.db import Alert, Listing, Pricepoint, select
from carexpert.normalize import enrich
from carexpert.pipeline import fingerprint, ingest, mark_stale, scan
from carexpert.pipeline.dedupe import looks_like_same_car
from carexpert.schemas import ListingData, SearchQuery


def _listing(source="a", source_id="1", price=15000, **kwargs) -> ListingData:
    listing = ListingData(
        source=source, source_id=source_id, url=f"https://{source}/{source_id}",
        title=kwargs.pop("title", "Peugeot 308 SW BlueHDi 130 Allure 2019"),
        price=price, km=kwargs.pop("km", 98_000), year=kwargs.pop("year", 2019), **kwargs,
    )
    return enrich(listing)


def test_same_car_on_two_sites_shares_a_fingerprint():
    a = _listing(source="autoscout24", source_id="111")
    b = _listing(source="lacentrale", source_id="222", price=15400)
    assert fingerprint(a) == fingerprint(b)
    assert looks_like_same_car(a, b)


def test_different_mileage_means_different_car():
    a = _listing(km=98_000)
    b = _listing(km=140_000)
    assert fingerprint(a) != fingerprint(b)


def test_two_similar_but_distinct_cars_keep_distinct_identities():
    """Bucketing mileage would merge these, and shrink every comparable pool."""
    a = _listing(source_id="1", km=88_000)
    b = _listing(source_id="2", km=88_900)
    assert fingerprint(a) != fingerprint(b)


def test_ingest_is_idempotent(session):
    ingest(session, [_listing()])
    ingest(session, [_listing()])
    assert session.execute(select(Listing)).scalars().all().__len__() == 1


def test_price_drop_is_recorded(session):
    ingest(session, [_listing(price=15_000)])
    stats = ingest(session, [_listing(price=13_500)])
    assert stats.price_drops
    prices = session.execute(select(Pricepoint.price_eur)).scalars().all()
    assert sorted(prices) == [13_500, 15_000]


def test_listings_without_price_are_skipped(session):
    stats = ingest(session, [_listing(price=None)])
    assert stats.skipped == 1
    assert session.execute(select(Listing)).scalars().all() == []


def test_stale_listings_are_deactivated(session):
    ingest(session, [_listing()])
    session.flush()
    count = mark_stale(session, "a", datetime.utcnow() + timedelta(days=1))
    assert count == 1
    assert session.execute(select(Listing)).scalar_one().active is False


def test_full_scan_on_the_demo_source(session):
    report = scan(session, sources=["demo"], query=SearchQuery(limit=120, countries=["FR"]))
    assert report.collected["demo"].created > 0
    assert report.valued > 0
    assert report.top
    assert all("score" in item for item in report.top)
    assert report.top[0]["score"] >= report.top[-1]["score"]


def test_scan_survives_a_broken_source(session):
    report = scan(session, sources=["demo", "source-inexistante"],
                  query=SearchQuery(limit=40, countries=["FR"]))
    assert report.errors
    assert report.collected["demo"].seen > 0


def test_watchlist_alerts_fire_once(session):
    from carexpert.alerts import create_watchlist

    create_watchlist(session, "golf", SearchQuery(countries=["FR"]), sources=["demo"],
                     min_score=70, channels=[])
    session.flush()
    first = scan(session, sources=["demo"], query=SearchQuery(limit=150, countries=["FR"]),
                 notify=True)
    alerts_after_first = session.execute(select(Alert)).scalars().all()
    second = scan(session, sources=["demo"], query=SearchQuery(limit=150, countries=["FR"]),
                  notify=True, revalue_all=True)
    alerts_after_second = session.execute(select(Alert)).scalars().all()
    assert first.valued > 0 and second.valued > 0
    assert alerts_after_first, "la premiere passe doit alerter"
    assert len(alerts_after_first) == len(alerts_after_second)


def test_scan_ranks_real_bargains_above_traps(demo_market):
    """The product thesis, as a test."""
    session = demo_market
    report = scan(session, sources=[], query=SearchQuery(limit=260,
                  countries=["FR", "DE", "IT", "BE", "ES", "NL"]))
    top_ids = [item["id"] for item in report.top[:20]]
    rows = {row.id: row for row in session.execute(select(Listing)).scalars().all()}
    kinds = [(rows[i].raw or {}).get("demo_kind") for i in top_ids]
    assert kinds.count("trap") == 0
    assert kinds.count("bargain") >= 8


def test_the_whole_base_is_scored_not_a_capped_slice(session):
    """A cap would leave most of a real market silently unscored."""
    from carexpert.sources.demo import DemoSource

    listings = list(
        DemoSource(seed=5, size=1400).search(SearchQuery(limit=1400, countries=["FR"]))
    )
    ingest(session, listings)
    session.flush()
    total = len(session.execute(select(Listing)).scalars().all())
    assert total > 1000, "il faut depasser l'ancien plafond pour que le test ait un sens"

    report = scan(session, sources=[], query=SearchQuery(limit=5000, countries=["FR"]))
    assert report.valued == total
    unscored = session.execute(select(Listing).where(Listing.score.is_(None))).scalars().all()
    assert unscored == []


def test_a_second_scan_skips_what_is_still_fresh(session):
    query = SearchQuery(limit=200, countries=["FR"])
    first = scan(session, sources=["demo"], query=query)
    assert first.valued > 0
    assert first.skipped_fresh == 0

    second = scan(session, sources=[], query=query)
    assert second.valued == 0
    assert second.skipped_fresh == first.valued


def test_a_price_change_forces_a_new_valuation(session):
    ingest(session, [_listing(price=15_000)])
    session.flush()
    scan(session, sources=[], query=SearchQuery(limit=10, countries=["FR"]))

    # Same advert, lower price: it must be re-valued despite being fresh.
    stats = ingest(session, [_listing(price=12_000)])
    session.flush()
    assert stats.touched_ids
    report = scan(session, sources=[], query=SearchQuery(limit=10, countries=["FR"]))
    assert report.valued == 1


def test_price_drops_are_detected_in_one_query(session):
    from carexpert.pipeline.run import _price_drops

    ingest(session, [_listing(source_id="a", price=15_000),
                     _listing(source_id="b", price=9_000)])
    session.flush()
    ingest(session, [_listing(source_id="a", price=13_000)])
    session.flush()
    ids = [row.id for row in session.execute(select(Listing)).scalars().all()]
    dropped = _price_drops(session, ids)
    assert len(dropped) == 1


def test_a_watchlist_created_later_still_fires(session):
    """The bug this guards against: alerts tied to what a run re-valued."""
    from carexpert.alerts import create_watchlist

    query = SearchQuery(limit=200, countries=["FR"])
    first = scan(session, sources=["demo"], query=query, notify=True)
    assert first.valued > 0
    assert first.alerts_sent == 0          # aucune veille n'existait encore

    create_watchlist(session, "tardive", SearchQuery(countries=["FR"]), sources=["demo"],
                     min_score=70, channels=[])
    session.flush()

    # Rien n'a bouge depuis: aucune annonce n'est reevaluee, et pourtant les
    # affaires deja en base doivent remonter.
    second = scan(session, sources=[], query=query, notify=True)
    assert second.valued == 0
    assert second.alerts_sent > 0
    assert session.execute(select(Alert)).scalars().all()


def test_alerts_are_capped_per_run(session):
    from carexpert.alerts import create_watchlist
    from carexpert.pipeline.run import dispatch_alerts

    scan(session, sources=["demo"], query=SearchQuery(limit=200, countries=["FR"]))
    create_watchlist(session, "large", SearchQuery(countries=["FR"]), sources=["demo"],
                     min_score=0, channels=[])
    session.flush()
    sent = dispatch_alerts(session, max_per_watchlist=5)
    assert sent == 5, "une nouvelle veille ne doit pas deverser toute la base d'un coup"


def test_an_alerted_car_is_never_announced_twice(session):
    from carexpert.alerts import create_watchlist
    from carexpert.pipeline.run import dispatch_alerts

    scan(session, sources=["demo"], query=SearchQuery(limit=200, countries=["FR"]))
    create_watchlist(session, "unique", SearchQuery(countries=["FR"]), sources=["demo"],
                     min_score=70, channels=[])
    session.flush()
    first = dispatch_alerts(session)
    second = dispatch_alerts(session)
    assert first > 0
    assert second == 0


def test_an_empty_channel_list_notifies_nobody(capsys):
    from carexpert.alerts import get_notifiers

    assert get_notifiers([]) == []
    assert [n.name for n in get_notifiers(None)] == ["console"]
    assert capsys.readouterr().out == ""


# --- Passe de detail -------------------------------------------------------


class ListOnlySource:
    """Une source qui ne rend, en recherche, que ce qu'une liste contient.

    Pas de descriptif: exactement ce que donne une page de resultats
    AutoScout24. Le descriptif n'arrive qu'en rouvrant l'annonce.
    """

    name = "liste"

    def __init__(self) -> None:
        self.opened: list[str] = []

    def _base(self, source_id: str, price: float) -> "ListingData":
        from carexpert.schemas import Fuel, Gearbox, ListingData

        return ListingData(
            source=self.name,
            source_id=source_id,
            url=f"https://site.fr/offres/{source_id}",
            title="Volvo V70 D5 215ch Summum",
            price=price,
            make="Volvo",
            model="V70",
            year=2011,
            km=150000,
            fuel=Fuel.DIESEL,
            gearbox=Gearbox.AUTOMATIC,
            postcode="69003",
        )

    def search_url(self, url: str, limit: int = 100):
        yield self._base("aaa", 12000)
        yield self._base("bbb", 11500)

    def search(self, query):
        return iter(())

    def fetch_detail(self, listing):
        self.opened.append(listing.source_id)
        full = listing.model_copy(deep=True)
        full.description = (
            "Moteur a revoir, fumee bleue au demarrage, vendu sans controle technique."
            if listing.source_id == "bbb"
            else "Carnet d'entretien complet, distribution faite, controle technique vierge."
        )
        full.postcode = None          # la page d'annonce ne le repete pas
        return full

    def close(self) -> None:
        return None


def test_the_shortlist_is_reopened_to_get_its_description(session, monkeypatch):
    """Sans descriptif, un moteur a revoir passe pour une bonne affaire."""
    from carexpert.db import Listing
    from carexpert.pipeline import run as run_module
    from carexpert.schemas import SearchQuery

    source = ListOnlySource()
    monkeypatch.setattr(run_module, "get_source", lambda name, **kw: source)

    report = run_module.scan(
        session,
        sources=["liste"],
        query=SearchQuery(make="Volvo", model="V70", limit=10),
        search_url="https://site.fr/lst/volvo/v70",
    )

    assert report.detailed == 2
    assert sorted(source.opened) == ["aaa", "bbb"]

    rows = {row.source_id: row for row in session.query(Listing).all()}
    assert "distribution faite" in rows["aaa"].description
    assert "Moteur a revoir" in rows["bbb"].description
    # Ce que la liste seule donnait n'est pas perdu au passage.
    assert rows["aaa"].postcode == "69003"
    assert "detail" in report.summary()


def test_an_advert_already_complete_is_not_reopened(session, monkeypatch):
    """Une source qui ouvre deja chaque annonce ne paie pas deux fois."""
    from carexpert.pipeline import run as run_module
    from carexpert.schemas import SearchQuery

    class CompleteSource(ListOnlySource):
        def search_url(self, url: str, limit: int = 100):
            for row in super().search_url(url, limit):
                row.description = "Deuxieme main, entretien suivi en concession."
                yield row

    source = CompleteSource()
    monkeypatch.setattr(run_module, "get_source", lambda name, **kw: source)

    report = run_module.scan(
        session,
        sources=["liste"],
        query=SearchQuery(make="Volvo", model="V70", limit=10),
        search_url="https://site.fr/lst/volvo/v70",
    )

    assert report.detailed == 0
    assert source.opened == []


def test_the_deep_pass_runs_end_to_end_when_the_model_answers(session, monkeypatch):
    """Sans ce test, la passe profonde ne tournait jamais en entier.

    Tant que l'expertise retombait sur les regles (pas de cle API), le code
    d'apres n'etait jamais atteint. Il contenait un appel a une fonction qui
    n'existait pas: la premiere expertise reelle aurait plante.
    """
    from carexpert.expert.analyst import AnalysisResult
    from carexpert.expert import analyze_offline
    from carexpert.pipeline import run as run_module
    from carexpert.schemas import SearchQuery

    source = ListOnlySource()
    monkeypatch.setattr(run_module, "get_source", lambda name, **kw: source)

    class FakeAnalyst:
        calls = 0

        def __init__(self, *args, **kwargs) -> None:
            pass

        def analyze(self, listing, valuation=None, **kwargs):
            FakeAnalyst.calls += 1
            report = analyze_offline(listing, valuation).report
            return AnalysisResult(
                report=report,
                model="claude-opus-5",
                photos_analyzed=3,
                input_tokens=2500,
                output_tokens=900,
            )

    monkeypatch.setattr(run_module, "ExpertAnalyst", FakeAnalyst)

    report = run_module.scan(
        session,
        sources=["liste"],
        query=SearchQuery(make="Volvo", model="V70", limit=10),
        search_url="https://site.fr/lst/volvo/v70",
        deep=2,
    )

    assert FakeAnalyst.calls == 2
    assert report.deep_analyzed == 2
    assert report.degraded == []
    assert report.cost.eur > 0
