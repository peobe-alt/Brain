"""Is the estimate calibrated, or does it just like every car?

The median car **is** the market. Take a base of several hundred adverts of
one model and value each one against the others: half should come out above
their estimate and half below, and the median gap should sit near zero. That
is not an opinion, it is what "market price" means.

So a base where every advert reads "below market" is not a base full of
bargains. It is an estimate that inflates, and the ranking it produces is
sorted by noise. This module measures that gap and says so, because nothing
else in the tool can: a scan prints its twenty best, and its twenty best look
convincing whatever the bias.

Read it as a check on the estimate, not on the market: it says whether the
cote can be trusted, never whether a given car is a good buy.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import Listing
from ..db import Valuation as StoredValuation

#: Beyond this median gap the estimate leans hard enough that the ranking is
#: telling you more about the estimator than about the cars.
LEANING = 0.05
BROKEN = 0.12


@dataclass
class Calibration:
    """What a whole base says about its own estimate."""

    total: int = 0
    valued: int = 0
    with_year: int = 0
    with_km: int = 0
    with_description: int = 0
    deltas: list[float] = field(default_factory=list)
    confidences: list[float] = field(default_factory=list)
    comps: list[int] = field(default_factory=list)
    verdicts: dict[str, int] = field(default_factory=dict)
    #: Median gap per selection tier: a tier that drifts on its own is worth
    #: seeing, since the widest ones carry the least information.
    by_tier: dict[str, list[float]] = field(default_factory=dict)

    def percentile(self, share: float) -> float:
        if not self.deltas:
            return 0.0
        ordered = sorted(self.deltas)
        index = min(len(ordered) - 1, max(0, round(share * (len(ordered) - 1))))
        return ordered[index]

    @property
    def median_delta(self) -> float:
        return statistics.median(self.deltas) if self.deltas else 0.0

    @property
    def below_market_share(self) -> float:
        """Share of adverts the estimate calls cheaper than the market.

        A sound estimate puts this near one half. At 0,9 the tool is not
        finding bargains, it is announcing them.
        """
        if not self.deltas:
            return 0.0
        return sum(1 for d in self.deltas if d > 0) / len(self.deltas)

    @property
    def state(self) -> str:
        gap = abs(self.median_delta)
        if not self.deltas:
            return "sans_mesure"
        if gap >= BROKEN:
            return "faussee"
        if gap >= LEANING:
            return "penchee"
        return "juste"

    @property
    def headline(self) -> str:
        if self.state == "sans_mesure":
            return "Aucune annonce estimee: rien a mesurer."
        sense = "sous" if self.median_delta > 0 else "au-dessus de"
        gap = abs(self.median_delta) * 100
        part = self.below_market_share * 100
        if self.state == "juste":
            return (f"Estimation centree: l'annonce mediane est a {gap:.1f} % "
                    f"{sense} sa cote, et {part:.0f} % de la base est donnee "
                    "sous le marche.")
        verdict = "penche" if self.state == "penchee" else "est faussee"
        return (f"L'estimation {verdict}: l'annonce mediane est a {gap:.1f} % "
                f"{sense} sa cote, et {part:.0f} % de la base est donnee sous "
                "le marche. La moitie devrait l'etre.")


def calibration(
    session: Session,
    *,
    make: str | None = None,
    model: str | None = None,
    source: str | None = None,
) -> Calibration:
    """Measure the estimate against the base it was computed on."""
    conditions = [Listing.active.is_(True)]
    if make:
        conditions.append(Listing.make == make)
    if model:
        conditions.append(Listing.model == model)
    if source:
        conditions.append(Listing.source == source)

    report = Calibration()
    rows = session.execute(
        select(Listing).where(*conditions).order_by(Listing.id)
    ).scalars().all()

    stored = {
        row.listing_id: row
        for row in session.execute(select(StoredValuation)).scalars().all()
    }

    for row in rows:
        report.total += 1
        if row.year:
            report.with_year += 1
        if row.km:
            report.with_km += 1
        if row.description:
            report.with_description += 1
        if row.verdict:
            report.verdicts[row.verdict] = report.verdicts.get(row.verdict, 0) + 1

        valuation = stored.get(row.id)
        # Une cote sans comparable n'est pas une cote: la retenir ferait
        # compter un zero parfait a chaque annonce non jugeable, et la base
        # paraitrait d'autant mieux calibree qu'elle est vide (invariant 7).
        if valuation is None or not valuation.comps_count:
            continue
        report.valued += 1
        report.deltas.append(valuation.delta_pct)
        report.confidences.append(valuation.confidence)
        report.comps.append(valuation.comps_count)
        tier = (valuation.details or {}).get("tier") or valuation.method
        report.by_tier.setdefault(tier, []).append(valuation.delta_pct)

    return report
