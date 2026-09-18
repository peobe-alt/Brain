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
from datetime import datetime, timedelta
from typing import Iterable

from sqlalchemy import and_, or_, select
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
    #: Adverts left alone because their valuation was still fresh.
    skipped_fresh: int = 0
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
        ]
        if self.skipped_fresh:
            parts.append(f"{self.skipped_fresh} deja a jour")
        parts += [
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
    revalue_all: bool = False,
) -> ScanReport:
    """Run one full pass and return what it found.

    By default only what needs it is re-valued: adverts that are new, whose
    price moved, or whose valuation has gone stale. `revalue_all` forces the
    whole base through, which is what you want after changing the valuation
    curves.
    """
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
    touched = set()
    for stats in report.collected.values():
        touched |= stats.touched_ids
    candidate_ids = _candidate_ids(session, query, touched=touched, revalue_all=revalue_all)
    report.skipped_fresh = _candidate_count(session, query) - len(candidate_ids)

    batch_size = get_settings().valuation_batch_size
    scored: list[tuple[Listing, Valuation, ExpertReport, DealScore]] = []
    for start in range(0, len(candidate_ids), batch_size):
        chunk = candidate_ids[start : start + batch_size]
        rows = session.execute(select(Listing).where(Listing.id.in_(chunk))).scalars().all()
        dropped = _price_drops(session, chunk)
        for row in rows:
            listing = from_row(row)
            valuation = estimate(session, row)
            result = analyze_offline(listing, valuation.as_dict())
            score = score_deal(listing, valuation, result.report,
                               price_dropped=row.id in dropped)
            _persist(session, row, valuation, result.report, score, model=result.model,
                     photos_analyzed=0)
            scored.append((row, valuation, result.report, score))
            report.valued += 1
        session.flush()

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
        report.alerts_sent = dispatch_alerts(session)

    return report


def _query_conditions(query: SearchQuery) -> list:
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
    return conditions


def _candidate_count(session: Session, query: SearchQuery) -> int:
    from sqlalchemy import func

    return session.execute(
        select(func.count(Listing.id)).where(*_query_conditions(query))
    ).scalar_one()


def _candidate_ids(
    session: Session, query: SearchQuery, *, touched: set[int], revalue_all: bool
) -> list[int]:
    """Adverts that need (re)valuing in this pass.

    There is no arbitrary ceiling here: a cap would silently leave part of
    the base unscored, which is invisible in testing and very visible once a
    real market is loaded. Volume is handled by batching instead, and by
    skipping what is still fresh.
    """
    conditions = _query_conditions(query)
    if not revalue_all:
        cutoff = datetime.utcnow() - timedelta(hours=get_settings().valuation_ttl_hours)
        freshness = or_(
            Listing.analyzed_at.is_(None),
            Listing.analyzed_at < cutoff,
            # A price move invalidates the valuation whoever ingested it, and
            # whenever: the comparison is against stored state, not against
            # what this particular run happens to have seen.
            and_(
                Listing.price_changed_at.is_not(None),
                Listing.analyzed_at < Listing.price_changed_at,
            ),
        )
        if touched:
            freshness = or_(freshness, Listing.id.in_(touched))
        conditions.append(freshness)
    statement = select(Listing.id).where(*conditions).order_by(Listing.last_seen.desc())
    return list(session.execute(statement).scalars().all())


def _price_drops(session: Session, listing_ids: list[int]) -> set[int]:
    """Which of these adverts have come down in price, in one query."""
    from ..db import Pricepoint

    rows = session.execute(
        select(Pricepoint.listing_id, Pricepoint.price_eur, Pricepoint.seen_at)
        .where(Pricepoint.listing_id.in_(listing_ids))
        .order_by(Pricepoint.listing_id, Pricepoint.seen_at.asc())
    ).all()
    first: dict[int, float] = {}
    last: dict[int, float] = {}
    for listing_id, price, _ in rows:
        first.setdefault(listing_id, price)
        last[listing_id] = price
    return {lid for lid, price in last.items() if price < first.get(lid, price) - 1}


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


def dispatch_alerts(session: Session, *, max_per_watchlist: int = 20) -> int:
    """Notify every watchlist about the deals it asked for, exactly once.

    Deliberately independent of what this run happened to re-value: a
    watchlist created today must fire on a car scored yesterday, and a scan
    that re-values nothing can still have alerts to send. Everything needed
    is read back from stored state.
    """
    watchlists = list(
        session.execute(select(Watchlist).where(Watchlist.active.is_(True))).scalars().all()
    )
    if not watchlists:
        return 0
    settings = get_settings()
    sent = 0
    for watchlist in watchlists:
        threshold = watchlist.min_score or settings.alert_threshold
        notifiers = get_notifiers(
            list(watchlist.channels) if watchlist.channels is not None else None
        )
        rows = session.execute(
            select(Listing)
            .where(
                Listing.active.is_(True),
                Listing.score.is_not(None),
                Listing.score >= threshold,
            )
            .order_by(Listing.score.desc())
            .limit(max_per_watchlist * 5)
        ).scalars().all()

        delivered_count = 0
        for row in rows:
            if delivered_count >= max_per_watchlist:
                log.info("veille %s: alertes plafonnees a %s pour cette passe",
                         watchlist.name, max_per_watchlist)
                break
            if not matches(watchlist, row) or already_alerted(session, watchlist.id, row.id):
                continue
            loaded = _load_analysis(session, row)
            if loaded is None:
                continue
            valuation, report, score = loaded
            listing = from_row(row)
            subject, body = format_alert(listing, score, valuation, report)
            payload = json_payload(listing, score, valuation, report)
            channels = [n.name for n in notifiers if n.send(subject, body, payload)]
            # With no channel configured the alert is still recorded, so a
            # later run does not re-announce the same car.
            record_alert(session, watchlist, row, score.score, channels, payload)
            delivered_count += 1
            sent += 1
        watchlist.last_run_at = datetime.utcnow()
    return sent


def _load_analysis(
    session: Session, row: Listing
) -> tuple[Valuation, ExpertReport, DealScore] | None:
    """Rebuild the objects an alert needs from what was stored."""
    from ..scoring.deal import ScoreFactor

    valuation_row = session.execute(
        select(ValuationRow).where(ValuationRow.listing_id == row.id)
    ).scalar_one_or_none()
    analysis = session.execute(
        select(Analysis).where(Analysis.listing_id == row.id)
    ).scalar_one_or_none()
    if valuation_row is None or analysis is None or not analysis.report:
        return None

    valuation = Valuation(
        fair_price_eur=valuation_row.fair_price_eur,
        low_eur=valuation_row.low_eur,
        high_eur=valuation_row.high_eur,
        confidence=valuation_row.confidence,
        comps_count=valuation_row.comps_count,
        method=valuation_row.method,
        delta_eur=valuation_row.delta_eur,
        delta_pct=valuation_row.delta_pct,
        details=dict(valuation_row.details or {}),
    )

    payload = dict(analysis.report)
    score_payload = payload.pop("score", {})
    try:
        report = ExpertReport.model_validate(payload)
    except Exception as exc:  # pragma: no cover - stored by an older version
        log.warning("rapport illisible pour %s: %s", row.url, exc)
        return None

    score = DealScore(
        score=score_payload.get("score", row.score or 0),
        headline=score_payload.get("headline", ""),
        factors=[ScoreFactor(**factor) for factor in score_payload.get("factors", [])],
        net_gain_eur=score_payload.get("net_gain_eur", 0.0),
        verdict=score_payload.get("verdict", row.verdict or "check"),
    )
    return valuation, report, score
