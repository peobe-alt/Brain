"""Value the adverts of a page captured in the browser.

Same treatment as a scan, minus the collection: the page is already read, so
the work starts at the database and ends at a score. What comes out is meant
to be drawn over the site's own page, so each entry carries its advert's
address - that is what the extension matches its cards on.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import Analysis, Listing, Valuation
from ..sources.capture import CapturedPage
from .ingest import ingest
from .run import value_and_score

log = logging.getLogger(__name__)

VERDICT_LABEL = {
    "grab": "A saisir", "check": "A verifier", "avoid": "A fuir", "unknown": "A estimer",
}


@dataclass
class CaptureOutcome:
    """What the browser gets back: one entry per advert it can annotate."""

    kind: str
    source: str | None
    message: str
    results: list[dict[str, Any]] = field(default_factory=list)
    skipped: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "source": self.source,
            "message": self.message,
            "count": len(self.results),
            "skipped": self.skipped,
            "results": self.results,
        }


def absorb(session: Session, page: CapturedPage) -> CaptureOutcome:
    """Store the page's adverts, value them, and score them."""
    if not page.listings:
        return CaptureOutcome(kind=page.kind, source=page.source, message=page.reason)

    stats = ingest(session, page.listings)
    session.flush()

    rows = _rows_for(session, page)
    results = []
    for listing in page.listings:
        row = rows.get(listing.source_id)
        if row is None:  # prix ou titre manquant: ingest l'a refusee
            continue
        value_and_score(session, row)
        results.append(payload(session, row))
    session.flush()

    return CaptureOutcome(
        kind=page.kind,
        source=page.source,
        message=_message(page, results, stats.created),
        results=results,
        skipped=max(0, len(page.listings) - len(results)),
    )


def _rows_for(session: Session, page: CapturedPage) -> dict[str, Listing]:
    ids = [listing.source_id for listing in page.listings]
    rows = session.execute(
        select(Listing).where(Listing.source == page.source, Listing.source_id.in_(ids))
    ).scalars().all()
    return {row.source_id: row for row in rows}


def payload(session: Session, row: Listing) -> dict[str, Any]:
    """One advert, as the page overlay needs it.

    Read back from the row rather than from the score just computed: an
    advert already expertised by Claude keeps its deep reading, and the badge
    must show the same verdict as the dashboard, not a fresher rule-based one.

    `fair_price_eur` is None whenever the comparable base was too thin to
    price the car, and the overlay keys on that: no price, no percentage, no
    gain - only what is missing to get one.
    """
    analysis = session.execute(
        select(Analysis).where(Analysis.listing_id == row.id)
    ).scalar_one_or_none()
    report = dict(analysis.report) if analysis else {}
    score = report.get("score") or {}

    return {
        "id": row.id,
        "url": row.url,
        "source": row.source,
        "source_id": row.source_id,
        "title": row.title,
        "price_eur": row.price_eur,
        "fair_price_eur": row.fair_price_eur,
        "delta_pct": row.delta_pct,
        "net_gain_eur": round(score.get("net_gain_eur") or 0),
        "score": row.score,
        "verdict": row.verdict,
        "verdict_label": VERDICT_LABEL.get(row.verdict or "", "Non analyse"),
        "headline": score.get("headline") or "",
        "factors": [
            {"label": factor.get("label", ""), "points": factor.get("points", 0)}
            for factor in (score.get("factors") or [])
        ],
        "summary": report.get("summary") or "",
        "red_flags": [
            {"label": flag.get("label", ""), "severity": flag.get("severity", "info")}
            for flag in (report.get("red_flags") or [])
        ],
        "questions": list(report.get("questions_to_seller") or []),
        "levers": list(report.get("negotiation_levers") or []),
        "checks": list(report.get("known_issues_to_check") or []),
        "repairs_eur": report.get("estimated_repairs_eur") or 0,
        "photos_analyzed": analysis.photos_analyzed if analysis else 0,
        "model": analysis.model if analysis else "",
        "basis": _basis(session, row),
        # Une page de resultats ne donne jamais le descriptif, et c'est la que
        # sont les pieges. Savoir lesquelles ont deja ete ouvertes evite de
        # proposer de rouvrir ce qui est deja lu.
        "has_detail": bool(row.description),
    }


def _basis(session: Session, row: Listing) -> dict[str, Any]:
    """What the estimate rests on, or what it would take to have one.

    A badge that says "a estimer" and prints "45% sous le marche" in the same
    breath is read as a market price. So the overlay gets the evidence, in
    numbers: how many comparable adverts are in the base, how many this level
    of similarity needs, and how many of that model the base holds at all.
    """
    valuation = session.execute(
        select(Valuation).where(Valuation.listing_id == row.id)
    ).scalar_one_or_none()
    details = (valuation.details if valuation else None) or {}
    known = session.execute(
        select(func.count(Listing.id)).where(
            Listing.active.is_(True), Listing.make == row.make, Listing.model == row.model
        )
    ).scalar_one() if row.make and row.model else 0

    return {
        "enough": bool(valuation and valuation.comps_count),
        "comps": details.get("comparables_trouves", valuation.comps_count if valuation else 0),
        "needed": details.get("comparables_requis") or 0,
        "tier": details.get("tier") or "",
        "confidence": round(valuation.confidence, 2) if valuation else 0.0,
        # Combien d'annonces de ce modele la base connait, tous criteres
        # confondus: c'est ce nombre qui monte quand on navigue.
        "known": known,
        "model": " ".join(part for part in (row.make, row.model) if part),
    }


def _message(page: CapturedPage, results: list[dict[str, Any]], created: int) -> str:
    """Say what was read and, separately, what could be priced.

    Those two numbers are not the same one, and conflating them is how a
    tool ends up announcing "6 annonces analysees" over six adverts it was
    unable to situate on any market.
    """
    read = len(results)
    priced = sum(1 for result in results if result.get("fair_price_eur"))
    if page.kind == "listing":
        if not read:
            return "Annonce illisible sur cette page."
        return "Annonce analysee." if priced else "Annonce lue, prix non situe: base insuffisante."
    if not read:
        return "Aucune annonce lisible sur cette page."

    plural = "s" if read > 1 else ""
    fresh = f", dont {created} nouvelle{'s' if created > 1 else ''}" if created else ""
    if not priced:
        return f"{read} annonce{plural} lue{plural}{fresh}, aucune situee: base insuffisante."
    return f"{read} annonce{plural} lue{plural}{fresh}, {priced} situee{'s' if priced > 1 else ''} sur le marche."
