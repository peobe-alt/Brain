"""The single number, and the reasons behind it.

The score answers one question: *should I spend an evening driving to see
this car?* It is built additively so every point can be explained in the
interface. A deal that cannot be explained is a deal nobody acts on.

Deliberate choices:

* a discount only counts as much as the valuation's confidence allows;
* repair costs are subtracted in euros, then compared to the discount, so a
  "bargain" that needs a timing belt stops being one;
* risk is capable of cancelling any discount, because the downside of a bad
  used car is far larger than the upside of a good price.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..expert.schema import ExpertReport
from ..schemas import ListingData, SellerType
from ..valuation.estimator import Valuation


@dataclass(slots=True)
class ScoreFactor:
    label: str
    points: float
    detail: str = ""

    def as_dict(self) -> dict:
        return {"label": self.label, "points": round(self.points, 1), "detail": self.detail}


@dataclass(slots=True)
class DealScore:
    score: int
    headline: str
    factors: list[ScoreFactor] = field(default_factory=list)
    net_gain_eur: float = 0.0
    verdict: str = "check"

    def as_dict(self) -> dict:
        return {
            "score": self.score,
            "headline": self.headline,
            "verdict": self.verdict,
            "net_gain_eur": round(self.net_gain_eur, 2),
            "factors": [f.as_dict() for f in self.factors],
        }


BASE_SCORE = 50.0

#: En dessous de ce score, quelque chose a ete retenu contre la voiture.
#: Au-dessus, elle est simplement ordinaire - et "a fuir" ne se dit pas
#: d'une voiture ordinaire.
#:
#: Mesure sur le marche synthetique (260 annonces, verite connue): a 55,
#: 61 des 215 annonces correctes sortaient "A FUIR", dont une Golf de 2024
#: a 4% au-dessus du marche, etat 88/100, vendeur particulier, dont le seul
#: tort etait de ne publier que trois photos. Le score, lui, etait juste:
#: c'est l'etiquette qui affirmait plus que ce que le calcul disait.
#: 45 est le plancher neutre: 50 de base, moins les 5 points retires quand
#: le prix ne peut pas etre situe.
NEUTRAL_FLOOR = 45


def _eur(value: float) -> str:
    """French thousands formatting, without eating the sentence's commas."""
    return f"{value:,.0f}".replace(",", "\u202f")


def score_deal(
    listing: ListingData,
    valuation: Valuation | None,
    report: ExpertReport | None,
    *,
    price_dropped: bool = False,
) -> DealScore:
    """Combine market position, condition and risk into one 0-100 score."""
    factors: list[ScoreFactor] = []
    score = BASE_SCORE
    repairs = float(report.estimated_repairs_eur) if report else 0.0
    delta_eur = valuation.delta_eur if valuation else 0.0

    # --- Market position ---------------------------------------------------
    if valuation and valuation.comps_count:
        delta_pct = valuation.delta_pct * 100
        # Low confidence must not let a shaky estimate drive the score.
        weight = 0.35 + 0.65 * valuation.confidence
        raw = max(-40.0, min(45.0, delta_pct * 1.7))
        points = raw * weight
        score += points
        sense = "sous le marche" if delta_pct >= 0 else "au-dessus du marche"
        factors.append(
            ScoreFactor(
                "Position prix",
                points,
                f"{abs(delta_pct):.0f}% {sense} ({valuation.comps_count} comparables, "
                f"confiance {valuation.confidence:.0%})",
            )
        )
    else:
        factors.append(ScoreFactor("Position prix", -5, _missing_basis(valuation)))
        score -= 5

    if report is not None:
        # --- Risk ----------------------------------------------------------
        risk = report.risk_score()
        risk_points = -risk * 0.45
        score += risk_points
        if risk:
            worst = max(
                report.red_flags,
                key=lambda f: {"info": 0, "attention": 1, "serieux": 2, "redhibitoire": 3}[f.severity],
                default=None,
            )
            factors.append(
                ScoreFactor(
                    "Risques identifies",
                    risk_points,
                    f"{len(report.red_flags)} alerte(s)"
                    + (f", la plus grave: {worst.label}" if worst else ""),
                )
            )

        # --- Condition & coherence ----------------------------------------
        condition_points = (report.condition_score - 60) * 0.25
        score += condition_points
        factors.append(
            ScoreFactor("Etat apparent", condition_points, f"note d'etat {report.condition_score}/100")
        )

        consistency_points = (report.consistency_score - 70) * 0.22
        score += consistency_points
        factors.append(
            ScoreFactor(
                "Coherence de l'annonce",
                consistency_points,
                f"coherence {report.consistency_score}/100 entre km, etat, prix et description",
            )
        )

        # --- Repairs, in euros --------------------------------------------
        if repairs and listing.price_eur:
            ratio = repairs / max(listing.price_eur, 1)
            repair_points = -min(28.0, ratio * 100 * 0.9)
            score += repair_points
            factors.append(
                ScoreFactor(
                    "Remise en etat",
                    repair_points,
                    f"{_eur(repairs)} EUR a prevoir, soit {ratio:.0%} du prix demande",
                )
            )

        verdict_points = {"grab": 8.0, "check": 0.0, "avoid": -20.0}[report.verdict]
        score += verdict_points
        if verdict_points:
            factors.append(ScoreFactor("Avis de l'expert", verdict_points, report.verdict))

    # --- Practical signals -------------------------------------------------
    if price_dropped:
        score += 5
        factors.append(ScoreFactor("Baisse de prix observee", 5, "le vendeur a deja bouge"))

    photo_count = len(listing.photos)
    if photo_count and photo_count < 4:
        score -= 4
        factors.append(
            ScoreFactor("Peu de photos", -4, f"{photo_count} photo(s): annonce peu verifiable")
        )
    elif photo_count >= 10:
        score += 2
        factors.append(ScoreFactor("Annonce bien documentee", 2, f"{photo_count} photos"))

    if listing.seller_type is SellerType.PRIVATE:
        score += 2
        factors.append(ScoreFactor("Vendeur particulier", 2, "marge de negociation, pas de marge pro"))

    km_year = listing.km_per_year
    if km_year and km_year > 30_000:
        score -= 4
        factors.append(
            ScoreFactor("Kilometrage annuel eleve", -4, f"{_eur(km_year)} km/an")
        )
    elif km_year and km_year < 6_000 and (listing.age_years or 0) > 4:
        score -= 3
        factors.append(
            ScoreFactor(
                "Vehicule tres peu roule",
                -3,
                f"{_eur(km_year)} km/an: usure de stationnement, joints et batterie",
            )
        )

    final = int(max(0, min(100, round(score))))
    net_gain = delta_eur - repairs
    return DealScore(
        score=final,
        headline=_headline(final, net_gain, valuation),
        factors=sorted(factors, key=lambda f: -abs(f.points)),
        net_gain_eur=net_gain,
        verdict=_verdict(final, report, valuation),
    )


def _too_thin(valuation: Valuation | None) -> bool:
    """Is the price estimate too fragile to carry a verdict?

    Two cases, and they are the same mistake at different scales: no
    comparable at all, and a handful of comparables that agree on nothing.
    A niche model reaches the second one routinely - there are fourteen Volvo
    V70 for sale in all of France - and announcing "A FUIR" on two references
    would be a confident answer built on nothing.
    """
    from ..config import get_settings

    if valuation is None or not valuation.comps_count:
        return True
    return valuation.confidence < get_settings().min_confidence_for_verdict


def _verdict(score: int, report: ExpertReport | None, valuation: Valuation | None) -> str:
    """The verdict, including the honest fourth answer.

    Without a solid market position the score sits near its neutral base.
    Reading that as "avoid" would tell someone to walk away from a sound car
    merely because the database is thin, which is exactly what happens on the
    first analyses. `unknown` says what is true: the advert itself looks
    fine, we just cannot price it yet.
    """
    if report is not None and report.verdict == "avoid":
        return "avoid"
    if _too_thin(valuation):
        return "unknown"
    if score >= 75:
        return "grab"
    if score >= NEUTRAL_FLOOR:
        return "check"
    return "avoid"


def _missing_basis(valuation: Valuation | None) -> str:
    """Say what the base lacks, in numbers, rather than that it lacks something.

    "Pas assez de comparables" leaves the reader to guess whether the tool is
    broken, the car is rare, or one more page of results would settle it.
    The two counts answer that in three words.
    """
    details = (valuation.details if valuation else None) or {}
    found = details.get("comparables_trouves")
    needed = details.get("comparables_requis")
    if found is None or not needed:
        return "pas assez de comparables pour situer le prix"
    return (
        f"base insuffisante: {found} annonce{'s' if found > 1 else ''} comparable"
        f"{'s' if found > 1 else ''} en base, il en faut {needed}"
    )


def _headline(score: int, net_gain: float, valuation: Valuation | None) -> str:
    if _too_thin(valuation):
        details = (valuation.details if valuation else None) or {}
        found, needed = details.get("comparables_trouves"), details.get("comparables_requis")
        if found is not None and needed:
            # Dire ce qui manque, en nombres: l'utilisateur saura qu'une page
            # de resultats de plus peut suffire.
            return (
                f"Base insuffisante pour situer ce prix: {found} annonce"
                f"{'s' if found > 1 else ''} comparable{'s' if found > 1 else ''} "
                f"en base, il en faut {needed}"
            )
        if valuation is not None and valuation.comps_count:
            return (
                f"Seulement {valuation.comps_count} reference"
                f"{'s' if valuation.comps_count > 1 else ''} comparable"
                f"{'s' if valuation.comps_count > 1 else ''}: prix non jugeable"
            )
        return "Pas assez de references pour juger le prix"
    if score >= 80:
        return f"Affaire serieuse: environ {_eur(net_gain)} EUR de gain net estime"
    if score >= 65:
        return f"Interessant, sous reserve de verification (gain estime {_eur(net_gain)} EUR)"
    if score >= 45:
        return "Prix coherent avec le marche, rien d'exceptionnel"
    if net_gain > 0:
        return "Bon marche, mais les risques annulent l'economie"
    return "Au-dessus du marche pour ce qu'il propose"
