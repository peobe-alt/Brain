"""Turning comparables into a price, an interval, and an honest confidence."""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ..config import get_settings
from ..schemas import ListingData
from ..db import Listing
from .adjust import fit_depreciation, vehicle_factor
from .comps import Facts, find_comparables, to_facts


@dataclass(slots=True)
class Valuation:
    """What the market says this car is worth, and how sure we are."""

    fair_price_eur: float
    low_eur: float
    high_eur: float
    confidence: float
    comps_count: int
    method: str
    delta_eur: float
    delta_pct: float
    details: dict = field(default_factory=dict)

    @property
    def is_underpriced(self) -> bool:
        return self.delta_pct > 0

    def as_dict(self) -> dict:
        return {
            "fair_price_eur": round(self.fair_price_eur, 2),
            "low_eur": round(self.low_eur, 2),
            "high_eur": round(self.high_eur, 2),
            "confidence": round(self.confidence, 3),
            "comps_count": self.comps_count,
            "method": self.method,
            "delta_eur": round(self.delta_eur, 2),
            "delta_pct": round(self.delta_pct, 4),
            "details": self.details,
        }


def _factor(facts: Facts) -> float:
    return vehicle_factor(
        age_years=facts.age_years,
        km=facts.km,
        make=facts.make,
        fuel=facts.fuel,
        gearbox=facts.gearbox,
        options=facts.options,
        seller_type=facts.seller_type,
        country=facts.country,
    )


def robust_center(values: list[float]) -> float:
    """Median of the middle 80%: immune to a single absurd advert."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) >= 10:
        cut = max(1, int(len(ordered) * 0.1))
        ordered = ordered[cut:-cut]
    return statistics.median(ordered)


def estimate(
    session: Session,
    target: ListingData | Listing | Facts,
    *,
    min_comps: int | None = None,
) -> Valuation:
    """Estimate a fair market price for one advert.

    Every comparable is restated on the target's terms (age, mileage,
    gearbox, equipment, seller type, country) before the median is taken.
    """
    settings = get_settings()
    min_comps = min_comps or settings.min_comps_for_confidence
    facts = target if isinstance(target, Facts) else to_facts(target)
    asking = facts.price_eur or 0.0

    comps, tier = find_comparables(session, facts, min_count=min_comps)
    unique_vehicles = len({c.fingerprint for c in comps if c.fingerprint})
    if not comps:
        return Valuation(
            fair_price_eur=asking, low_eur=asking, high_eur=asking, confidence=0.0,
            comps_count=0, method="aucune_reference", delta_eur=0.0, delta_pct=0.0,
            details={"raison": "aucun vehicule comparable en base pour ce modele"},
        )

    target_factor = _factor(facts)
    adjusted: list[float] = []
    for comp in comps:
        comp_factor = _factor(comp)
        if comp_factor <= 0 or not comp.price_eur:
            continue
        adjusted.append(comp.price_eur * (target_factor / comp_factor))
    if not adjusted:
        return Valuation(
            fair_price_eur=asking, low_eur=asking, high_eur=asking, confidence=0.0,
            comps_count=0, method="aucune_reference", delta_eur=0.0, delta_pct=0.0,
            details={"raison": "comparables sans prix exploitable"},
        )

    center = robust_center(adjusted)
    dispersion = _mad(adjusted, center)
    method = f"comparables:{tier}"

    # With enough spread in age/mileage, blend in a fitted curve: it
    # extrapolates better than adjusted medians at the edges of the sample.
    fitted = None
    samples = [
        (c.age_years, c.km, c.price_eur)
        for c in comps
        if c.age_years is not None and c.km is not None and c.price_eur
    ]
    if len(samples) >= 12 and facts.age_years is not None and facts.km is not None:
        rates = fit_depreciation(samples)
        if rates:
            fitted = _predict_from_rates(samples, rates, facts)
            if fitted and 0.5 * center <= fitted <= 1.8 * center:
                center = 0.5 * center + 0.5 * fitted
                method = f"mixte:{tier}"

    confidence = _confidence(len(comps), dispersion, center, tier, facts, comps)
    spread = max(dispersion, center * 0.04)
    delta = center - asking if asking else 0.0

    return Valuation(
        fair_price_eur=center,
        low_eur=max(0.0, center - spread),
        high_eur=center + spread,
        confidence=confidence,
        comps_count=len(comps),
        method=method,
        delta_eur=delta,
        delta_pct=(delta / center) if center else 0.0,
        details={
            "tier": tier,
            "dispersion_eur": round(dispersion, 2),
            "ajustement_cible": round(target_factor, 4),
            "prix_ajustes_min": round(min(adjusted), 2),
            "prix_ajustes_max": round(max(adjusted), 2),
            "modele_ajuste": round(fitted, 2) if fitted else None,
            "pays_compares": sorted({c.country for c in comps}),
            "vehicules_distincts": unique_vehicles or len(comps),
            "exemples": [
                {"titre": c.title[:80], "prix": c.price_eur, "km": c.km,
                 "annee": c.year, "url": c.url}
                for c in comps[:5]
            ],
        },
    )


def _predict_from_rates(
    samples: list[tuple[float, int, float]], rates: tuple[float, float], facts: Facts
) -> float | None:
    """Price the target with freshly fitted age/mileage rates."""
    from math import log

    age_rate, km_rate = rates
    if not (0 < age_rate < 0.5 and 0 < km_rate < 0.8):
        return None

    def curve(age: float, km: float) -> float:
        return ((1 - age_rate) ** age) * ((1 - km_rate) ** (km / 150_000))

    # Recover the implied "as new" price, robustly, then apply it to the target.
    implied = [price / curve(age, km) for age, km, price in samples if curve(age, km) > 0.01]
    if not implied:
        return None
    base = robust_center(implied)
    value = base * curve(facts.age_years or 0.0, float(facts.km or 0))
    return value if value > 0 and log(value) else None


def _mad(values: list[float], center: float) -> float:
    """Median absolute deviation, scaled to be comparable to a std-dev."""
    if len(values) < 3:
        return max(values) - min(values) if values else 0.0
    return 1.4826 * statistics.median([abs(v - center) for v in values])


def _confidence(
    count: int, dispersion: float, center: float, tier: str, target: Facts, comps: list[Facts]
) -> float:
    """0..1: sample size, agreement between comparables, and extrapolation."""
    from .comps import tier_confidence

    c_count = min(1.0, count / 12)
    relative_spread = (dispersion / center) if center else 1.0
    c_spread = max(0.0, min(1.0, 1 - relative_spread / 0.28))

    c_extrap = 1.0
    kms = [c.km for c in comps if c.km is not None]
    if target.km is not None and kms:
        lo, hi = min(kms), max(kms)
        if target.km < lo or target.km > hi:
            overshoot = (lo - target.km) if target.km < lo else (target.km - hi)
            c_extrap -= min(0.5, overshoot / max(hi, 1))
    years = [c.year for c in comps if c.year]
    if target.year and years and not (min(years) <= target.year <= max(years)):
        c_extrap -= 0.2

    raw = 0.45 * c_count + 0.30 * c_spread + 0.25 * max(0.0, c_extrap)
    return round(max(0.0, min(1.0, raw * tier_confidence(tier))), 3)
