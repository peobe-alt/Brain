"""Command line interface.

    carexpert demo                 # tout voir fonctionner, sans reseau ni cle API
    carexpert scan --make Peugeot --model 308 --price-max 15000 --deep 5
    carexpert deals --min-score 70
    carexpert show 42
    carexpert analyse-url "https://..."
    carexpert serve
"""

from __future__ import annotations

import json
import logging
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .config import get_settings
from .db import Analysis, Listing, Watchlist, init_db, select, session_scope
from .schemas import Fuel, SearchQuery

app = typer.Typer(
    add_completion=False,
    help="Un expert automobile qui scanne les annonces europeennes a votre place.",
)
watch_app = typer.Typer(help="Gerer les recherches permanentes et leurs alertes.")
app.add_typer(watch_app, name="watch")

console = Console()

VERDICT_STYLE = {"grab": "bold green", "check": "yellow", "avoid": "red", "unknown": "dim"}
VERDICT_LABEL = {
    "grab": "A SAISIR", "check": "A VOIR", "avoid": "A FUIR", "unknown": "A ESTIMER",
}


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )


def _eur(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:,.0f}".replace(",", " ")


def _build_query(
    make: str | None, model: str | None, keywords: str | None, price_min: int | None,
    price_max: int | None, year_min: int | None, km_max: int | None, fuel: str | None,
    countries: str, limit: int,
) -> SearchQuery:
    return SearchQuery(
        make=make, model=model, keywords=keywords, price_min=price_min, price_max=price_max,
        year_min=year_min, km_max=km_max,
        fuel=Fuel(fuel) if fuel else None,
        countries=[c.strip().upper() for c in countries.split(",") if c.strip()],
        limit=limit,
    )


@app.command()
def init() -> None:
    """Creer la base de donnees."""
    init_db()
    console.print(f"[green]Base prete[/green] : {get_settings().database_url}")


@app.command("sources")
def list_sources() -> None:
    """Lister les sources d'annonces disponibles."""
    from .sources import available_sources

    table = Table(title="Sources", header_style="bold")
    table.add_column("nom")
    table.add_column("site")
    table.add_column("pays")
    table.add_column("JS requis")
    for name, info in available_sources().items():
        table.add_row(
            name, info.label, ", ".join(info.countries) or "-",
            "[red]oui[/red]" if info.requires_js else "non",
        )
    console.print(table)
    console.print(
        "[dim]Les sources marquees 'JS requis' rendent leurs resultats cote client : "
        "utiliser une recherche collee avec --url, ou un flux officiel.[/dim]"
    )


@app.command()
def scan(
    source: list[str] = typer.Option(["demo"], "--source", "-s", help="Sources a interroger."),
    url: Optional[str] = typer.Option(None, "--url", help="URL de recherche collee depuis le site."),
    make: Optional[str] = typer.Option(None, "--make", help="Marque."),
    model: Optional[str] = typer.Option(None, "--model", help="Modele."),
    keywords: Optional[str] = typer.Option(None, "--keywords"),
    price_min: Optional[int] = typer.Option(None, "--price-min"),
    price_max: Optional[int] = typer.Option(None, "--price-max"),
    year_min: Optional[int] = typer.Option(None, "--year-min"),
    km_max: Optional[int] = typer.Option(None, "--km-max"),
    fuel: Optional[str] = typer.Option(None, "--fuel", help="petrol, diesel, hybrid, phev, electric"),
    countries: str = typer.Option("FR", "--countries", help="Codes pays separes par des virgules."),
    limit: int = typer.Option(100, "--limit"),
    deep: int = typer.Option(0, "--deep", help="Nombre d'annonces expertisees par Claude (photos)."),
    notify: bool = typer.Option(False, "--notify", help="Declencher les alertes des watchlists."),
    revalue_all: bool = typer.Option(
        False, "--revalue-all",
        help="Reestimer toute la base, meme ce qui est deja a jour (apres modification des courbes).",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Collecter, estimer, expertiser et classer des annonces."""
    _setup_logging(verbose)
    init_db()
    from .pipeline import scan as run_scan

    query = _build_query(make, model, keywords, price_min, price_max, year_min, km_max,
                         fuel, countries, limit)
    with console.status("Collecte et analyse en cours..."):
        with session_scope() as session:
            report = run_scan(session, sources=source, query=query, deep=deep,
                              notify=notify, search_url=url, revalue_all=revalue_all)

    console.print(f"\n[bold]{report.summary()}[/bold]")
    for error in report.errors:
        console.print(f"[red]erreur[/red] {error}")
    for reason in report.degraded:
        console.print(f"[yellow]repli sur les regles[/yellow] {reason}")
    if report.cost.eur:
        console.print(f"[dim]Expertise approfondie : {report.cost.label()}[/dim]")
    if report.top:
        _print_deals_table(report.top)
    console.print("\n[dim]Detail d'une annonce : carexpert show <id>[/dim]")


@app.command()
def deals(
    min_score: int = typer.Option(60, "--min-score"),
    limit: int = typer.Option(20, "--limit"),
    make: Optional[str] = typer.Option(None, "--make"),
) -> None:
    """Afficher les meilleures affaires deja analysees."""
    init_db()
    with session_scope() as session:
        conditions = [Listing.score.is_not(None), Listing.score >= min_score,
                      Listing.active.is_(True)]
        if make:
            conditions.append(Listing.make == make)
        from .pipeline.dedupe import group_by_vehicle

        rows = session.execute(
            select(Listing).where(*conditions).order_by(Listing.score.desc()).limit(limit * 3)
        ).scalars().all()
        payload = [
            {
                "id": row.id, "title": row.title, "price_eur": row.price_eur,
                "fair_price_eur": row.fair_price_eur, "score": row.score,
                "verdict": row.verdict, "url": row.url,
                "net_gain_eur": (row.fair_price_eur or 0) - (row.price_eur or 0),
                "km": row.km, "year": row.year, "copies": len(others),
            }
            for row, others in group_by_vehicle(rows)[:limit]
        ]
    if not payload:
        console.print("[yellow]Aucune affaire au-dessus de ce score.[/yellow] "
                      "Lancer d'abord : carexpert demo")
        return
    _print_deals_table(payload)


def _print_deals_table(rows: list[dict]) -> None:
    table = Table(header_style="bold", show_lines=False)
    table.add_column("id", justify="right", style="dim")
    table.add_column("score", justify="right")
    table.add_column("avis")
    table.add_column("vehicule", max_width=42)
    table.add_column("prix", justify="right")
    table.add_column("marche", justify="right")
    table.add_column("ecart", justify="right")
    for row in rows:
        verdict = row.get("verdict", "check")
        gain = row.get("net_gain_eur") or 0
        table.add_row(
            str(row.get("id", "")),
            f"[{VERDICT_STYLE.get(verdict, 'white')}]{row['score']}[/]",
            f"[{VERDICT_STYLE.get(verdict, 'white')}]{VERDICT_LABEL.get(verdict, verdict)}[/]",
            row["title"][:42],
            _eur(row.get("price_eur")) + (f" [dim]x{row['copies'] + 1}[/dim]"
                                          if row.get("copies") else ""),
            _eur(row.get("fair_price_eur")),
            f"[green]+{_eur(gain)}[/green]" if gain > 0 else f"[red]{_eur(gain)}[/red]",
        )
    console.print(table)


@app.command()
def show(listing_id: int = typer.Argument(..., help="Identifiant affiche par `deals`.")) -> None:
    """Afficher l'expertise complete d'une annonce."""
    init_db()
    with session_scope() as session:
        row = session.get(Listing, listing_id)
        if row is None:
            console.print(f"[red]Annonce {listing_id} introuvable[/red]")
            raise typer.Exit(1)
        analysis = session.execute(
            select(Analysis).where(Analysis.listing_id == row.id)
        ).scalar_one_or_none()
        data = {
            "title": row.title, "url": row.url, "price": row.price_eur,
            "fair": row.fair_price_eur, "score": row.score, "verdict": row.verdict,
            "year": row.year, "km": row.km, "fuel": row.fuel, "gearbox": row.gearbox,
            "city": row.city, "country": row.country, "seller": row.seller_type,
            "report": analysis.report if analysis else None,
            "model_used": analysis.model if analysis else None,
            "photos_analyzed": analysis.photos_analyzed if analysis else 0,
        }

    verdict = data["verdict"] or "unknown"
    market = (
        f"Estimation marche : {_eur(data['fair'])} EUR"
        if data["fair"]
        else "Estimation marche : [dim]pas encore de comparables en base[/dim]"
    )
    body = "\n".join([
        f"[bold]{data['title']}[/bold]",
        data["url"],
        "",
        f"Prix demande : [bold]{_eur(data['price'])} EUR[/bold]    {market}",
        f"{data['year']} - {_eur(data['km'])} km - {data['fuel']} - {data['gearbox']} - "
        f"{data['city'] or '?'} ({data['country']})",
    ])
    console.print(Panel(
        body,
        title=f"[{VERDICT_STYLE.get(verdict, 'white')}]{data['score']}/100 - "
              f"{VERDICT_LABEL.get(verdict, verdict)}[/]",
    ))

    report = data["report"]
    if not report:
        console.print("[yellow]Pas encore d'expertise pour cette annonce.[/yellow]")
        return

    console.print(f"\n[bold]Resume[/bold]\n{report['summary']}\n")
    score = report.get("score", {})
    if score.get("factors"):
        table = Table(title="Decomposition du score", header_style="bold")
        table.add_column("points", justify="right")
        table.add_column("critere")
        table.add_column("detail")
        for factor in score["factors"]:
            color = "green" if factor["points"] > 0 else "red"
            table.add_row(f"[{color}]{factor['points']:+.1f}[/]", factor["label"], factor["detail"])
        console.print(table)

    _print_list("Alertes", [f"[{f['severity']}] {f['label']} - {f['evidence']}"
                            for f in report.get("red_flags", [])], "red")
    _print_list("Observations sur les photos",
                [f"photo {f['photo_index']} : {f['observation']}"
                 for f in report.get("photo_findings", [])], "cyan")
    _print_list("Points rassurants", report.get("strengths", []), "green")
    _print_list("A verifier sur cette motorisation", report.get("known_issues_to_check", []), "yellow")
    _print_list("Questions au vendeur", report.get("questions_to_seller", []), "white")
    _print_list("Leviers de negociation", report.get("negotiation_levers", []), "magenta")
    console.print(
        f"\n[dim]Budget remise en etat estime : {report.get('estimated_repairs_eur', 0)} EUR - "
        f"analyse par {data['model_used']} sur {data['photos_analyzed']} photo(s)[/dim]"
    )


def _print_list(title: str, items: list[str], color: str) -> None:
    if not items:
        return
    console.print(f"[bold]{title}[/bold]")
    for item in items:
        console.print(f"  [{color}]-[/] {item}")
    console.print()


@app.command("analyse-url")
def analyse_url(
    url: str = typer.Argument(..., help="URL d'une annonce."),
    source: str = typer.Option("autoscout24", "--source", help="Source correspondant au site."),
    no_photos: bool = typer.Option(False, "--no-photos", help="Ne pas envoyer les photos."),
) -> None:
    """Expertiser une annonce precise, en direct."""
    init_db()
    from .expert import ExpertAnalyst, analyze_offline
    from .pipeline.ingest import ingest
    from .sources import get_source
    from .valuation import estimate

    adapter = get_source(source)
    if not hasattr(adapter, "fetch_listing"):
        console.print(f"[red]La source {source} ne sait pas ouvrir une annonce isolee[/red]")
        raise typer.Exit(1)
    listing = adapter.fetch_listing(url)  # type: ignore[attr-defined]
    adapter.close()
    if listing is None:
        console.print("[red]Annonce illisible.[/red] Le site rend peut-etre sa page en JavaScript.")
        raise typer.Exit(1)

    with session_scope() as session:
        ingest(session, [listing])
        session.flush()
        row = session.execute(
            select(Listing).where(Listing.source == listing.source,
                                  Listing.source_id == listing.source_id)
        ).scalar_one()
        valuation = estimate(session, row)
        listing_id = row.id

    try:
        result = ExpertAnalyst().analyze(listing, valuation=valuation.as_dict(),
                                         with_photos=not no_photos)
    except Exception as exc:
        console.print(f"[yellow]Expertise Claude indisponible ({exc}), repli sur les regles.[/yellow]")
        result = analyze_offline(listing, valuation.as_dict())

    from .pipeline.run import _persist
    from .scoring import score_deal

    score = score_deal(listing, valuation, result.report)
    with session_scope() as session:
        row = session.get(Listing, listing_id)
        _persist(session, row, valuation, result.report, score, model=result.model,
                 photos_analyzed=result.photos_analyzed)
    show(listing_id)


VERDICT_DIAG = {
    "ok": ("green", "SOURCE EXPLOITABLE"),
    "liste": ("green", "SOURCE EXPLOITABLE (PAGE DE RESULTATS)"),
    "partiel": ("yellow", "PARTIELLEMENT EXPLOITABLE"),
    "extraction": ("yellow", "EXTRACTION INCOMPLETE"),
    "motif": ("yellow", "MOTIF DE LIEN A CORRIGER"),
    "js": ("red", "SITE RENDU EN JAVASCRIPT"),
    "fragment": ("red", "RECHERCHE RESTEE DANS LE NAVIGATEUR"),
    "interdit": ("red", "INTERDIT PAR LE ROBOTS.TXT"),
    "echec": ("red", "SITE INJOIGNABLE"),
}


@app.command()
def diagnose(
    source: str = typer.Option(..., "--source", "-s", help="Source a tester."),
    url: Optional[str] = typer.Option(None, "--url", help="URL de recherche collee depuis le site."),
    make: Optional[str] = typer.Option(None, "--make", help="Sinon, criteres pour construire l'URL."),
    model: Optional[str] = typer.Option(None, "--model"),
    samples: int = typer.Option(3, "--samples", help="Nombre d'annonces ouvertes pour verification."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Verifier qu'une source fonctionne vraiment, et dire quoi corriger sinon."""
    _setup_logging(verbose)
    from .sources import get_source
    from .sources.configured import ConfiguredSource, link_pattern, load_site_configs
    from .sources.diagnose import diagnose_search

    configs = load_site_configs()
    config = configs.get(source, {})
    if not config:
        console.print(
            f"[red]Source '{source}' inconnue.[/red] Disponibles : "
            + ", ".join(sorted(configs))
        )
        raise typer.Exit(1)

    target = url
    if target is None:
        adapter = get_source(source)
        if isinstance(adapter, ConfiguredSource):
            target = adapter.build_search_url(_build_query(make, model, None, None, None,
                                                           None, None, None, "FR", 50), 1)
        adapter.close()
    if not target:
        console.print("[red]Aucune URL a tester.[/red] Passer --url ou --make/--model.")
        raise typer.Exit(1)

    console.print(f"[dim]Test de {target}[/dim]\n")
    with console.status("Verification en cours (rythme poli, comptez quelques secondes)..."):
        report = diagnose_search(
            target,
            source=source,
            pattern=link_pattern(config),
            selectors=config.get("selectors"),
            samples=samples,
        )

    level, phrase = report.verdict()
    color, label = VERDICT_DIAG.get(level, ("white", level.upper()))
    console.print(Panel(phrase, title=f"[{color}]{label}[/]", border_style=color))

    table = Table(header_style="bold", show_header=False, box=None)
    table.add_column("critere", style="dim")
    table.add_column("valeur")
    table.add_row("robots.txt", "present" if report.robots_present else "absent")
    table.add_row("autorise", "[green]oui[/green]" if report.robots_allows else "[red]non[/red]")
    if report.crawl_delay:
        table.add_row("delai impose", f"{report.crawl_delay:.1f} s")
    if report.fragment_route:
        table.add_row("URL demandee", report.fetched_url)
        table.add_row("reste dans le navigateur", f"[red]#{report.fragment_route}[/red]")
    table.add_row("reponse", f"HTTP {report.status} - {report.page_bytes} octets "
                             f"en {report.elapsed_s:.1f} s")
    table.add_row("motif de lien", report.pattern_used)
    table.add_row("annonces detectees", str(report.links_found))
    if report.results_listings:
        table.add_row(
            "annonces sur la liste",
            f"[green]{report.results_listings}[/green] lues sans ouvrir d'annonce "
            f"({report.results_complete} completes)",
        )
    if report.suggested_pattern:
        table.add_row("motif suggere", f"[yellow]{report.suggested_pattern}[/yellow]")
    if report.js_suspected:
        table.add_row("rendu", "[red]JavaScript detecte[/red]")
    if report.sitemaps:
        table.add_row("sitemaps annonces", "\n".join(report.sitemaps[:3]))
    console.print(table)

    if report.samples:
        console.print()
        sample_table = Table(title="Echantillon d'annonces", header_style="bold")
        sample_table.add_column("annonce", max_width=44)
        sample_table.add_column("schema.org")
        sample_table.add_column("photos", justify="right")
        sample_table.add_column("champs extraits")
        sample_table.add_column("manquants")
        for sample in report.samples:
            sample_table.add_row(
                sample.url[-44:],
                "[green]oui[/green]" if sample.has_jsonld else "[yellow]non[/yellow]",
                str(sample.photo_count),
                f"{len(sample.filled)}/10",
                "[red]" + ", ".join(sample.missing_critical) + "[/red]"
                if sample.missing_critical else "[green]aucun[/green]",
            )
        console.print(sample_table)

    console.print("\n[bold]A faire maintenant[/bold]")
    for action in report.actions():
        console.print(f"  [cyan]>[/cyan] {action}")


@app.command()
def demo(
    size: int = typer.Option(300, "--size", help="Taille du marche synthetique."),
    deep: int = typer.Option(0, "--deep", help="Annonces expertisees par Claude (necessite une cle API)."),
) -> None:
    """Demonstration complete, hors ligne : marche synthetique, estimation, scoring."""
    init_db()
    from .pipeline import ingest, scan as run_scan
    from .sources.demo import DemoSource

    query = SearchQuery(limit=size, countries=["FR", "DE", "IT", "BE", "ES", "NL"])
    with console.status("Generation d'un marche de demonstration..."):
        listings = list(DemoSource(size=size).search(query))
        with session_scope() as session:
            stats = ingest(session, listings)
    console.print(f"[green]{stats.summary()}[/green]")

    with console.status("Estimation et expertise..."):
        with session_scope() as session:
            report = run_scan(session, sources=[], query=query, deep=deep)
    console.print(f"[bold]{report.summary()}[/bold]\n")
    _print_deals_table(report.top[:12])

    with session_scope() as session:
        rows = session.execute(
            select(Listing).where(Listing.score.is_not(None)).order_by(Listing.score.desc()).limit(1)
        ).scalars().all()
        best = rows[0].id if rows else None
    if best:
        console.print(f"\n[dim]Detail de la meilleure affaire : carexpert show {best}[/dim]")
        console.print("[dim]Tableau de bord : carexpert serve[/dim]")


@watch_app.command("add")
def watch_add(
    name: str = typer.Argument(...),
    make: Optional[str] = typer.Option(None, "--make"),
    model: Optional[str] = typer.Option(None, "--model"),
    price_max: Optional[int] = typer.Option(None, "--price-max"),
    km_max: Optional[int] = typer.Option(None, "--km-max"),
    year_min: Optional[int] = typer.Option(None, "--year-min"),
    fuel: Optional[str] = typer.Option(None, "--fuel"),
    countries: str = typer.Option("FR", "--countries"),
    sources: list[str] = typer.Option(["demo"], "--source", "-s"),
    min_score: int = typer.Option(75, "--min-score"),
    channel: list[str] = typer.Option(["console"], "--channel",
                                      help="console, webhook, telegram, email"),
) -> None:
    """Creer ou mettre a jour une recherche permanente."""
    init_db()
    from .alerts import create_watchlist

    query = _build_query(make, model, None, None, price_max, year_min, km_max, fuel,
                         countries, 200)
    with session_scope() as session:
        watchlist = create_watchlist(session, name, query, sources=list(sources),
                                     min_score=min_score, channels=list(channel))
        console.print(f"[green]Veille '{watchlist.name}' enregistree[/green] "
                      f"(alerte a partir de {min_score}/100, canaux : {', '.join(channel)})")


@watch_app.command("list")
def watch_list() -> None:
    """Lister les recherches permanentes."""
    init_db()
    with session_scope() as session:
        rows = session.execute(select(Watchlist)).scalars().all()
        data = [
            {"name": w.name, "query": w.query, "sources": w.sources, "min_score": w.min_score,
             "channels": w.channels, "active": w.active, "last": w.last_run_at}
            for w in rows
        ]
    if not data:
        console.print("[yellow]Aucune veille. Exemple :[/yellow] "
                      "carexpert watch add golf --make Volkswagen --model Golf --price-max 15000")
        return
    table = Table(header_style="bold")
    table.add_column("nom")
    table.add_column("criteres")
    table.add_column("sources")
    table.add_column("seuil", justify="right")
    table.add_column("canaux")
    table.add_column("derniere alerte")
    for item in data:
        criteria = {k: v for k, v in item["query"].items() if v not in (None, [], "")}
        criteria.pop("limit", None)
        table.add_row(
            item["name"], json.dumps(criteria, ensure_ascii=False)[:60],
            ", ".join(item["sources"] or []), str(item["min_score"]),
            ", ".join(item["channels"] or []),
            item["last"].strftime("%d/%m %H:%M") if item["last"] else "-",
        )
    console.print(table)


@watch_app.command("run")
def watch_run(
    deep: int = typer.Option(3, "--deep", help="Annonces expertisees par Claude par veille."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Executer toutes les veilles actives et envoyer les alertes."""
    _setup_logging(verbose)
    init_db()
    from .pipeline import scan as run_scan

    with session_scope() as session:
        watchlists = session.execute(
            select(Watchlist).where(Watchlist.active.is_(True))
        ).scalars().all()
        payload = [(w.name, SearchQuery(**w.query), list(w.sources or ["demo"])) for w in watchlists]

    if not payload:
        console.print("[yellow]Aucune veille active.[/yellow]")
        return

    for name, query, sources in payload:
        console.print(f"[bold]Veille {name}[/bold] sur {', '.join(sources)}")
        with session_scope() as session:
            report = run_scan(session, sources=sources, query=query, deep=deep, notify=True)
        console.print(f"  {report.summary()}")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
) -> None:
    """Lancer le tableau de bord web."""
    import uvicorn

    init_db()
    console.print(f"[green]Tableau de bord[/green] : http://{host}:{port}")
    uvicorn.run("carexpert.api.app:app", host=host, port=port, log_level="warning")


if __name__ == "__main__":  # pragma: no cover
    app()
