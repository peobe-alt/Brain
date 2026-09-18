"""Dashboard and JSON API.

Server-rendered on purpose: no build step, no bundler, one command to run.
The JSON endpoints are there so a real front end (or a mobile app) can be
plugged on top later without touching the pipeline.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from ..db import Analysis, Listing, Valuation, Watchlist, init_db, session_scope

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
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))

VERDICT_LABEL = {"grab": "A saisir", "check": "A verifier", "avoid": "A fuir"}


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
