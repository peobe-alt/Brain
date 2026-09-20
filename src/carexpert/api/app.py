"""Dashboard and JSON API.

Server-rendered on purpose: no build step, no bundler, one command to run.
The JSON endpoints are there so a real front end (or a mobile app) can be
plugged on top later without touching the pipeline.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from ..config import get_settings
from ..db import Analysis, Listing, Valuation, Watchlist, init_db, session_scope
from ..schemas import SearchQuery
from ..sources import available_sources, source_for_url
from . import extension as extension_api
from .extension import EXTENSION_DIR, guard_origin
from .extension import router as extension_router
from .jobs import runner

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="CarExpert",
    description="Scanner d'annonces auto avec expertise assistee",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
app.middleware("http")(guard_origin)
app.include_router(extension_router)
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))

VERDICT_LABEL = {
    "grab": "A saisir", "check": "A verifier", "avoid": "A fuir", "unknown": "A estimer",
}


def _listing_payload(row: Listing, duplicates: list[Listing] | None = None) -> dict[str, Any]:
    gain = (row.fair_price_eur or 0) - (row.price_eur or 0)
    duplicates = duplicates or []
    spread = 0.0
    if duplicates and row.price_eur:
        highest = max((d.price_eur or 0) for d in duplicates)
        spread = max(0.0, highest - row.price_eur)
    return {
        "other_sites": [
            {"source": d.source, "url": d.url, "price_eur": d.price_eur} for d in duplicates
        ],
        "price_spread_eur": round(spread),
        "id": row.id,
        "title": row.title,
        "url": row.url,
        "source": row.source,
        "country": row.country,
        "price_eur": row.price_eur,
        "fair_price_eur": row.fair_price_eur,
        "delta_pct": row.delta_pct,
        "net_gain_eur": gain,
        "score": row.score,
        "verdict": row.verdict,
        "verdict_label": VERDICT_LABEL.get(row.verdict or "", "Non analyse"),
        "year": row.year,
        "km": row.km,
        "fuel": row.fuel,
        "gearbox": row.gearbox,
        "make": row.make,
        "model": row.model,
        "city": row.city,
        "seller_type": row.seller_type,
        "photo": (row.photos or [{}])[0].get("url") if row.photos else None,
        "photo_count": len(row.photos or []),
    }


def _query_deals(session, *, min_score: int, make: str | None, verdict: str | None, limit: int):
    """Best deals, one entry per physical vehicle."""
    from ..pipeline.dedupe import group_by_vehicle

    conditions = [Listing.active.is_(True), Listing.score.is_not(None), Listing.score >= min_score]
    if make:
        conditions.append(Listing.make == make)
    if verdict:
        conditions.append(Listing.verdict == verdict)
    # Over-fetch, because cross-posted copies collapse into one entry.
    statement = select(Listing).where(*conditions).order_by(Listing.score.desc()).limit(limit * 3)
    rows = list(session.execute(statement).scalars().all())
    return group_by_vehicle(rows)[:limit]


@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    min_score: int = Query(0, ge=0, le=100),
    make: str | None = None,
    verdict: str | None = None,
    limit: int = Query(60, ge=1, le=300),
):
    with session_scope() as session:
        grouped = _query_deals(session, min_score=min_score, make=make, verdict=verdict,
                               limit=limit)
        deals = [_listing_payload(row, others) for row, others in grouped]
        makes = [
            m for m in session.execute(
                select(Listing.make).where(Listing.make.is_not(None)).distinct().order_by(Listing.make)
            ).scalars().all()
        ]
        total = session.execute(select(func.count(Listing.id))).scalar_one()
        analyzed = session.execute(
            select(func.count(Listing.id)).where(Listing.score.is_not(None))
        ).scalar_one()
        great = session.execute(
            select(func.count(Listing.id)).where(Listing.score >= 75)
        ).scalar_one()
        watchlists = session.execute(select(func.count(Watchlist.id))).scalar_one()

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "deals": deals,
            "makes": makes,
            "filters": {"min_score": min_score, "make": make or "", "verdict": verdict or ""},
            "stats": {
                "total": total,
                "analyzed": analyzed,
                "great": great,
                "watchlists": watchlists,
            },
        },
    )


@app.get("/listing/{listing_id}", response_class=HTMLResponse)
def listing_detail(request: Request, listing_id: int):
    with session_scope() as session:
        row = session.get(Listing, listing_id)
        if row is None:
            raise HTTPException(status_code=404, detail="annonce introuvable")
        analysis = session.execute(
            select(Analysis).where(Analysis.listing_id == listing_id)
        ).scalar_one_or_none()
        valuation = session.execute(
            select(Valuation).where(Valuation.listing_id == listing_id)
        ).scalar_one_or_none()
        payload = _listing_payload(row)
        payload["description"] = row.description
        payload["options"] = row.options or []
        payload["photos"] = [p.get("url") for p in (row.photos or []) if p.get("url")]
        report = dict(analysis.report) if analysis else None
        valuation_payload = (
            {
                "fair_price_eur": valuation.fair_price_eur,
                "low_eur": valuation.low_eur,
                "high_eur": valuation.high_eur,
                "confidence": valuation.confidence,
                "comps_count": valuation.comps_count,
                "method": valuation.method,
                "details": valuation.details,
            }
            if valuation
            else None
        )
        model_used = analysis.model if analysis else None
        photos_analyzed = analysis.photos_analyzed if analysis else 0

    return templates.TemplateResponse(
        request,
        "listing.html",
        {
            "listing": payload,
            "report": report,
            "score": (report or {}).get("score"),
            "valuation": valuation_payload,
            "model_used": model_used,
            "photos_analyzed": photos_analyzed,
        },
    )


@app.get("/api/deals")
def api_deals(
    min_score: int = Query(0, ge=0, le=100),
    make: str | None = None,
    verdict: str | None = None,
    limit: int = Query(50, ge=1, le=300),
):
    with session_scope() as session:
        grouped = _query_deals(session, min_score=min_score, make=make, verdict=verdict,
                               limit=limit)
        return {
            "count": len(grouped),
            "deals": [_listing_payload(row, others) for row, others in grouped],
        }


@app.get("/api/listing/{listing_id}")
def api_listing(listing_id: int):
    with session_scope() as session:
        row = session.get(Listing, listing_id)
        if row is None:
            raise HTTPException(status_code=404, detail="annonce introuvable")
        analysis = session.execute(
            select(Analysis).where(Analysis.listing_id == listing_id)
        ).scalar_one_or_none()
        payload = _listing_payload(row)
        payload["description"] = row.description
        payload["report"] = analysis.report if analysis else None
        return payload


@app.get("/api/health")
def health():
    with session_scope() as session:
        total = session.execute(select(func.count(Listing.id))).scalar_one()
    return {"status": "ok", "listings": total}


# --- Lancer une recherche -------------------------------------------------


def _query_from_form(
    make: str | None, model: str | None, price_max: int | None,
    year_min: int | None, km_max: int | None, country: str, limit: int,
) -> SearchQuery:
    return SearchQuery(
        make=(make or "").strip() or None,
        model=(model or "").strip() or None,
        price_max=price_max or None,
        year_min=year_min or None,
        km_max=km_max or None,
        countries=[country] if country else ["FR"],
        limit=limit,
    )


@app.get("/recherche", response_class=HTMLResponse)
def new_search(request: Request, erreur: str = ""):
    sources = {name: info for name, info in available_sources().items() if name != "demo"}
    return templates.TemplateResponse(
        request,
        "search.html",
        {
            "sources": sources,
            "erreur": erreur,
            "running": runner.running(),
            "recent": runner.recent(6),
        },
    )


@app.post("/recherche")
def start_search(
    url: str = Form(""),
    make: str = Form(""),
    model: str = Form(""),
    source: str = Form(""),
    country: str = Form("FR"),
    price_max: int | None = Form(None),
    year_min: int | None = Form(None),
    km_max: int | None = Form(None),
    limit: int = Form(500),
    deep: int = Form(0),
    save_as: str = Form(""),
):
    """Start a scan from the browser, either from a pasted URL or criteria."""
    url = (url or "").strip()
    query = _query_from_form(make, model, price_max, year_min, km_max, country, limit)

    if url:
        if not url.startswith(("http://", "https://")):
            return _search_error("Cette adresse ne ressemble pas a un lien. "
                                 "Collez l'adresse complete, celle qui commence par https://")
        guessed = source_for_url(url)
        if guessed is None:
            known = ", ".join(sorted(n for n in available_sources() if n != "demo"))
            return _search_error(f"Ce site n'est pas encore connu. Sites disponibles : {known}.")
        sources = [guessed]
        label = f"{guessed} : {url}"
    else:
        if not (query.make or query.model):
            return _search_error("Indiquez au moins une marque, ou collez l'adresse "
                                 "d'une recherche faite sur le site.")
        sources = [source] if source else [
            name for name in available_sources() if name != "demo"
        ]
        label = " ".join(x for x in (query.make, query.model) if x) or "Recherche"

    job, problem = runner.start(
        label=label, sources=sources, query=query,
        url=url or None, deep=max(0, deep),
    )
    if job is None:
        return _search_error(problem)

    if save_as.strip():
        _save_watchlist(save_as.strip(), query, sources, url or None)

    return RedirectResponse(f"/scan/{job.id}", status_code=303)


def _search_error(message: str) -> RedirectResponse:
    from urllib.parse import quote

    return RedirectResponse(f"/recherche?erreur={quote(message)}", status_code=303)


def _save_watchlist(name: str, query: SearchQuery, sources: list[str], url: str | None) -> None:
    with session_scope() as session:
        existing = session.execute(
            select(Watchlist).where(Watchlist.name == name)
        ).scalar_one_or_none()
        payload = query.model_dump(mode="json")
        if existing:
            existing.query = payload
            existing.sources = sources
            existing.search_url = url
        else:
            session.add(Watchlist(name=name, query=payload, sources=sources,
                                  search_url=url, channels=["console"]))


@app.get("/scan/{job_id}", response_class=HTMLResponse)
def scan_progress(request: Request, job_id: str):
    job = runner.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="scan introuvable")
    return templates.TemplateResponse(request, "scan.html", {"job": job.as_dict()})


@app.get("/api/scan/{job_id}")
def api_scan(job_id: str):
    job = runner.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="scan introuvable")
    return job.as_dict()


# --- Veilles --------------------------------------------------------------


@app.get("/veilles", response_class=HTMLResponse)
def watchlists(request: Request, erreur: str = ""):
    with session_scope() as session:
        rows = list(session.execute(
            select(Watchlist).order_by(Watchlist.created_at.desc())
        ).scalars().all())
        items = [
            {
                "id": w.id,
                "name": w.name,
                "query": w.query,
                "search_url": w.search_url,
                "sources": w.sources,
                "min_score": w.min_score,
                "active": w.active,
                "last_run_at": w.last_run_at,
                "criteria": _criteria_text(w),
            }
            for w in rows
        ]
    return templates.TemplateResponse(
        request,
        "watchlists.html",
        {"watchlists": items, "erreur": erreur, "running": runner.running()},
    )


def _criteria_text(w: Watchlist) -> str:
    if w.search_url:
        return w.search_url
    query = w.query or {}
    bits = [str(query.get(key)) for key in ("make", "model") if query.get(key)]
    if query.get("price_max"):
        bits.append(f"jusqu'a {query['price_max']} EUR")
    if query.get("year_min"):
        bits.append(f"a partir de {query['year_min']}")
    if query.get("km_max"):
        bits.append(f"moins de {query['km_max']} km")
    return " ".join(bits) or "tous criteres"


@app.post("/veilles/{watchlist_id}/lancer")
def run_watchlist(watchlist_id: int):
    from urllib.parse import quote

    with session_scope() as session:
        w = session.get(Watchlist, watchlist_id)
        if w is None:
            raise HTTPException(status_code=404, detail="veille introuvable")
        name, url, sources = w.name, w.search_url, list(w.sources or [])
        query = SearchQuery(**(w.query or {}))
        w.last_run_at = datetime.utcnow()

    if not sources:
        sources = [n for n in available_sources() if n != "demo"]
    job, problem = runner.start(label=name, sources=sources, query=query, url=url)
    if job is None:
        return RedirectResponse(f"/veilles?erreur={quote(problem)}", status_code=303)
    return RedirectResponse(f"/scan/{job.id}", status_code=303)


@app.post("/veilles/{watchlist_id}/supprimer")
def delete_watchlist(watchlist_id: int):
    with session_scope() as session:
        w = session.get(Watchlist, watchlist_id)
        if w is not None:
            session.delete(w)
    return RedirectResponse("/veilles", status_code=303)


# --- Extension navigateur -------------------------------------------------


@app.get("/extension", response_class=HTMLResponse)
def extension_page(request: Request):
    """How to install the extension, and whether it is talking to us."""
    sources = [name for name in available_sources() if name != "demo"]
    return templates.TemplateResponse(
        request,
        "extension.html",
        {
            "folder": str(EXTENSION_DIR),
            "sites": sorted(sources),
            "activity": extension_api.activity,
            "deep_ready": bool(get_settings().anthropic_api_key),
        },
    )
