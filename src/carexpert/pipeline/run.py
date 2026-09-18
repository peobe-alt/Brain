"""The scan: collect, value, expertise, score, alert.

Deliberately two-pass, because cost is the thing that decides whether a tool
like this is usable daily:

1. **Wide pass** - every advert collected is valued against comparables and
   scored with the rule-based expert. Local, instant, free.
2. **Deep pass** - only the best candidates are sent to Claude with their
   photos for a real expert reading, then rescored.

Scanning a thousand adverts therefore costs a handful of model calls, not a
thousand.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..alerts import (
    already_alerted,
    format_alert,
    get_notifiers,
    json_payload,
    matches,
    record_alert,
)
from ..config import get_settings
from ..db import Analysis, Listing, Valuation as ValuationRow, Watchlist
from ..expert import ExpertAnalyst, analyze_offline
from ..expert.cost import Cost, zero
from ..expert.schema import ExpertReport
from ..schemas import SearchQuery
from ..scoring import DealScore, score_deal
from ..sources import get_source
from ..valuation import estimate
from ..valuation.estimator import Valuation
from .ingest import IngestStats, from_row, ingest

log = logging.getLogger(__name__)


@dataclass
class ScanReport:
    collected: dict[str, IngestStats] = field(default_factory=dict)
    valued: int = 0
    deep_analyzed: int = 0
    #: Deep analyses that fell back to the rule layer, with the reason why.
    degraded: list[str] = field(default_factory=list)
    cost: Cost = field(default_factory=zero)
    alerts_sent: int = 0
    errors: list[str] = field(default_factory=list)
    top: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        collected = sum(s.seen for s in self.collected.values())
        new = sum(s.created for s in self.collected.values())
        parts = [
            f"{collected} annonces collectees ({new} nouvelles)",
            f"{self.valued} estimees",
            f"{self.deep_analyzed} expertisees en profondeur",
        ]
        if self.degraded:
            parts.append(f"{len(self.degraded)} en repli")
        parts.append(f"{self.alerts_sent} alertes")
        if self.cost.eur:
            parts.append(f"cout {self.cost.eur:.2f} EUR")
        return ", ".join(parts)


def scan(
    session: Session,
    *,
    sources: Iterable[str],
    query: SearchQuery,
    deep: int = 0,
    notify: bool = False,
    search_url: str | None = None,
) -> ScanReport:
    """Run one full pass and return what it found."""
    report = ScanReport()

    for name in sources:
        try:
            adapter = get_source(name)
        except KeyError as exc:
            report.errors.append(str(exc))
            continue
        try:
            if search_url is not None and hasattr(adapter, "search_url"):
                listings = list(adapter.search_url(search_url, limit=query.limit))
            else:
                listings = list(adapter.search(query))
            report.collected[name] = ingest(session, listings)
            log.info("%s: %s", name, report.collected[name].summary())
        except Exception as exc:  # one broken source must not kill the scan
            log.exception("source %s en echec", name)
            report.errors.append(f"{name}: {exc}")
        finally:
            adapter.close()

    session.flush()

    # --- Wide pass ---------------------------------------------------------
    candidates = _candidates(session, query)
    scored: list[tuple[Listing, Valuation, ExpertReport, DealScore]] = []
    for row in candidates:
        listing = from_row(row)
        valuation = estimate(session, row)
        result = analyze_offline(listing, valuation.as_dict())
        score = score_deal(listing, valuation, result.report, price_dropped=_dropped(session, row))
        _persist(session, row, valuation, result.report, score, model=result.model,
                 photos_analyzed=0)
        scored.append((row, valuation, result.report, score))
        report.valued += 1

    scored.sort(key=lambda item: -item[3].score)

    # --- Deep pass ---------------------------------------------------------
    if deep > 0:
        analyst = ExpertAnalyst()
        for row, valuation, _, _ in scored[:deep]:
            listing = from_row(row)
            # `analyze` never raises: a failure comes back degraded so one bad
            # call cannot cost the rest of the batch.
            result = analyst.analyze(listing, valuation=valuation.as_dict())
            if result.degraded:
                report.degraded.append(f"{row.title[:40]}: {result.degraded_reason}")
                continue
            report.cost = report.cost + result.cost()
            score = score_deal(listing, valuation, result.report,
                               price_dropped=_dropped(session, row))
            _persist(session, row, valuation, result.report, score, model=result.model,
                     photos_analyzed=result.photos_analyzed)
            report.deep_analyzed += 1
            for index, item in enumerate(scored):
                if item[0].id == row.id:
                    scored[index] = (row, valuation, result.report, score)
                    break
        scored.sort(key=lambda item: -item[3].score)

    session.flush()

    report.top = [
        {
            "id": row.id,
            "url": row.url,
            "title": row.title,
            "price_eur": row.price_eur,
            "fair_price_eur": round(valuation.fair_price_eur),
            "score": score.score,
            "verdict": score.verdict,
            "headline": score.headline,
            "net_gain_eur": round(score.net_gain_eur),
        }
        for row, valuation, _, score in scored[:20]
    ]

    if notify:
        report.alerts_sent = dispatch_alerts(session, scored)

    return report


def _candidates(session: Session, query: SearchQuery) -> list[Listing]:
    """Active adverts worth (re)evaluating in this pass."""
    conditions = [Listing.active.is_(True), Listing.price_eur.is_not(None)]
    if query.make:
        conditions.append(Listing.make == query.make)
    if query.model:
        conditions.append(Listing.model == query.model)
    if query.price_max:
        conditions.append(Listing.price_eur <= query.price_max)
    if query.price_min:
        conditions.append(Listing.price_eur >= query.price_min)
    if query.year_min:
        conditions.append(Listing.year >= query.year_min)
    if query.km_max:
        conditions.append(Listing.km <= query.km_max)
    if query.countries:
        conditions.append(Listing.country.in_(query.countries))
    statement = select(Listing).where(*conditions).order_by(Listing.last_seen.desc()).limit(1000)
    return list(session.execute(statement).scalars().all())


def _dropped(session: Session, row: Listing) -> bool:
    from ..db import Pricepoint

    prices = session.execute(
        select(Pricepoint.price_eur)
        .where(Pricepoint.listing_id == row.id)
        .order_by(Pricepoint.seen_at.asc())
    ).scalars().all()
    return len(prices) >= 2 and prices[-1] < prices[0] - 1


def _persist(
    session: Session,
    row: Listing,
    valuation: Valuation,
    report: ExpertReport,
    score: DealScore,
    *,
    model: str,
    photos_analyzed: int,
) -> None:
    existing = session.execute(
        select(ValuationRow).where(ValuationRow.listing_id == row.id)
    ).scalar_one_or_none()
    payload = valuation.as_dict()
    if existing is None:
        existing = ValuationRow(listing_id=row.id, **_valuation_columns(payload))
        session.add(existing)
    else:
        for key, value in _valuation_columns(payload).items():
            setattr(existing, key, value)
        existing.created_at = datetime.utcnow()

    analysis = session.execute(
        select(Analysis).where(Analysis.listing_id == row.id)
    ).scalar_one_or_none()
    report_json = report.model_dump(mode="json")
    report_json["score"] = score.as_dict()
    if analysis is None:
        analysis = Analysis(listing_id=row.id, model=model, report=report_json,
                            condition_score=report.condition_score,
                            risk_score=report.risk_score(),
                            estimated_repairs_eur=report.estimated_repairs_eur,
                            verdict=report.verdict, photos_analyzed=photos_analyzed)
        session.add(analysis)
    else:
        # Never let a cheap rule-based pass overwrite a real expert reading.
        if analysis.photos_analyzed and not photos_analyzed:
            return
        analysis.model = model
        analysis.report = report_json
        analysis.condition_score = report.condition_score
        analysis.risk_score = report.risk_score()
        analysis.estimated_repairs_eur = report.estimated_repairs_eur
        analysis.verdict = report.verdict
        analysis.photos_analyzed = photos_analyzed
        analysis.created_at = datetime.utcnow()

    row.score = score.score
    row.fair_price_eur = valuation.fair_price_eur
    row.delta_pct = valuation.delta_pct
    row.verdict = score.verdict
    row.analyzed_at = datetime.utcnow()


def _valuation_columns(payload: dict) -> dict:
    return {
        "fair_price_eur": payload["fair_price_eur"],
        "low_eur": payload["low_eur"],
        "high_eur": payload["high_eur"],
        "confidence": payload["confidence"],
        "comps_count": payload["comps_count"],
        "method": payload["method"],
        "delta_eur": payload["delta_eur"],
        "delta_pct": payload["delta_pct"],
        "details": payload["details"],
    }


def dispatch_alerts(
    session: Session, scored: list[tuple[Listing, Valuation, ExpertReport, DealScore]]
) -> int:
    """Notify every watchlist about the deals it asked for, exactly once."""
    watchlists = list(
        session.execute(select(Watchlist).where(Watchlist.active.is_(True))).scalars().all()
    )
    if not watchlists:
        return 0
    settings = get_settings()
    sent = 0
    for watchlist in watchlists:
        threshold = watchlist.min_score or settings.alert_threshold
        notifiers = get_notifiers(list(watchlist.channels or ["console"]))
        for row, valuation, report, score in scored:
            if score.score < threshold or not matches(watchlist, row):
                continue
            if already_alerted(session, watchlist.id, row.id):
                continue
            listing = from_row(row)
            subject, body = format_alert(listing, score, valuation, report)
            delivered = [n.name for n in notifiers if n.send(subject, body,
                          json_payload(listing, score, valuation, report))]
            if delivered:
                record_alert(session, watchlist, row, score.score, delivered,
                             json_payload(listing, score, valuation, report))
                sent += 1
    return sent
