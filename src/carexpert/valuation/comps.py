"""Finding the right comparables, and knowing when there aren't any.

The estimate is only as good as the sample it rests on, so selection runs in
tiers: start strict (same version, same year, similar mileage, same country)
and widen only as far as needed to reach a usable sample. The tier that was
actually used is reported, because `same model, any country, +/-3 years` is a
much weaker statement than `same trim, same year` and the score must know it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Sequence

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from ..db import Listing
from ..schemas import Fuel, Gearbox, ListingData, SellerType


@dataclass(slots=True)
class Facts:
    """The handful of attributes valuation actually needs."""

    make: str | None
    model: str | None
    year: int | None
    age_years: float | None
    km: int | None
    fuel: Fuel
    gearbox: Gearbox
    body: str
    options: list[str]
    price_eur: float | None
    country: str
    seller_type: SellerType
    listing_id: int | None = None
    #: D'ou vient l'annonce: un marche synthetique ne se compare pas au reel.
    source: str = ""
    url: str = ""
    title: str = ""
    #: Identity of the physical vehicle, shared across sites and reposts.
    fingerprint: str = ""


def to_facts(obj: ListingData | Listing) -> Facts:
    from ..pipeline.dedupe import fingerprint as compute_fingerprint

    if isinstance(obj, ListingData):
        return Facts(
            make=obj.make, model=obj.model, year=obj.year, age_years=obj.age_years,
            km=obj.km, fuel=obj.fuel, gearbox=obj.gearbox, body=obj.body.value,
            options=list(obj.options), price_eur=obj.price_eur, country=obj.country,
            seller_type=obj.seller_type, source=obj.source, url=obj.url,
            title=obj.title, fingerprint=compute_fingerprint(obj),
        )
    age = None
    if obj.year:
        age = max(0.0, date.today().year - obj.year + 0.5)
    return Facts(
        make=obj.make, model=obj.model, year=obj.year, age_years=age, km=obj.km,
        fuel=Fuel(obj.fuel), gearbox=Gearbox(obj.gearbox), body=obj.body,
        options=list(obj.options or []), price_eur=obj.price_eur, country=obj.country,
        seller_type=SellerType(obj.seller_type), listing_id=obj.id,
        source=obj.source, url=obj.url, title=obj.title,
        fingerprint=obj.fingerprint or "",
    )


#: Exactement les colonnes dont `Facts` a besoin. Charger la ligne entiere
#: revient a deserialiser depuis JSON les photos et la charge brute de chaque
#: comparable pour les jeter aussitot: mesure a 1 200 `json.loads` par
#: estimation, dont les deux tiers pour des champs que la valorisation ne lit
#: jamais.
FACTS_COLUMNS = (
    Listing.id, Listing.source, Listing.url, Listing.title, Listing.fingerprint,
    Listing.make, Listing.model, Listing.year, Listing.km, Listing.fuel,
    Listing.gearbox, Listing.body, Listing.options, Listing.price_eur,
    Listing.country, Listing.seller_type,
)


def _facts_from_row(row: Any, this_year: int) -> Facts:
    age = max(0.0, this_year - row.year + 0.5) if row.year else None
    return Facts(
        make=row.make, model=row.model, year=row.year, age_years=age, km=row.km,
        fuel=Fuel(row.fuel), gearbox=Gearbox(row.gearbox), body=row.body,
        options=list(row.options or []), price_eur=row.price_eur,
        country=row.country, seller_type=SellerType(row.seller_type),
        listing_id=row.id, source=row.source, url=row.url, title=row.title,
        fingerprint=row.fingerprint or "",
    )


#: (tier name, year tolerance, mileage tolerance, match fuel, match gearbox, same country)
TIERS: list[tuple[str, int, float, bool, bool, bool]] = [
    ("strict",          1, 0.30, True,  True,  True),
    ("modele_carburant", 2, 0.50, True,  False, True),
    ("modele",          3, 0.70, False, False, True),
    ("modele_europe",   3, 0.70, False, False, False),
    ("modele_large",    5, 1.00, False, False, False),
]

#: Combien d'annonces il faut a chaque palier, en proportion du minimum
#: demande. Plus le palier est large, plus l'echantillon doit etre gros pour
#: dire quelque chose: trois Golf du meme millesime, meme energie et meme
#: boite sont un marche; trois Golf "toutes energies, cinq ans d'ecart,
#: kilometrage du simple au double" sont trois voitures differentes.
#:
#: Mesure qui a impose cette echelle: une Golf 7 1.6 TDI de 255 000 km a
#: 5 900 EUR, valorisee 10 649 EUR - donc "A SAISIR, 45% sous le marche,
#: +4 749 EUR" - sur trois comparables du palier le plus large: une e-Golf
#: electrique, un break TDI et une GTE hybride rechargeable.
TIER_NEED = {
    "strict": 0.4,
    "modele_carburant": 0.5,
    "modele": 0.75,
    "modele_europe": 1.0,
    "modele_large": 1.5,
}

#: En dessous de trois vehicules distincts, aucun palier ne dit rien: une
#: mediane sur deux prix est un des deux prix.
ABSOLUTE_MIN_COMPS = 3


def required_comps(tier: str, min_count: int) -> int:
    """How many comparables this tier needs before it means anything."""
    ratio = TIER_NEED.get(tier)
    if ratio is None:
        return min_count
    return max(ABSOLUTE_MIN_COMPS, round(min_count * ratio))


#: Familles d'energie qui ne se comparent jamais entre elles, a aucun
#: palier. Un palier large accepte de melanger une essence et un diesel:
#: meme carrosserie, meme usage, meme courbe de decote a peu pres. Une
#: electrique n'a rien de tout cela - batterie, autonomie, aides a l'achat,
#: marche de l'occasion different - et une hybride rechargeable non plus.
#:
#: Mesure: une e-Golf de 163 000 km affichee 8 490 EUR valorisee 7 827 EUR,
#: donc "8% au-dessus du marche, A FUIR", sur un echantillon de Golf 1.6 TDI.
#: L'hybride non rechargeable reste dans la famille thermique: c'est une
#: essence avec une petite batterie, son prix vit dans la meme fourchette.
#: La prise, elle, change tout.
THERMAL = ("petrol", "diesel", "hybrid", "lpg", "cng", "ethanol", "other")
FUEL_FAMILIES = {
    Fuel.ELECTRIC: ("electric",),
    Fuel.PHEV: ("phev",),
    Fuel.HYBRID: THERMAL,
    Fuel.PETROL: THERMAL,
    Fuel.DIESEL: THERMAL,
    Fuel.LPG: THERMAL,
    Fuel.CNG: THERMAL,
    Fuel.ETHANOL: THERMAL,
}


#: Sources qui ne decrivent aucun marche reel. Le marche synthetique sert a
#: mesurer la qualite du classement, jamais a fixer un prix: une Golf reelle
#: valorisee sur des Golf inventees sort systematiquement "au-dessus du
#: marche", avec une confiance elevee puisque les fausses annonces sont, elles,
#: parfaitement coherentes entre elles.
SYNTHETIC_SOURCES = ("demo",)


def find_comparables(
    session: Session,
    target: Facts,
    *,
    min_count: int = 8,
    max_age_days: int = 120,
    exclude_ids: Sequence[int] = (),
) -> tuple[list[Facts], str]:
    """Return the tightest comparable set that carries its own weight.

    `min_count` scales the whole ladder rather than fixing one threshold:
    each tier needs a share of it (`TIER_NEED`), so widening the selection
    also raises the bar it has to clear.
    """
    if not target.make or not target.model:
        return [], "aucun"

    best: list[Facts] = []
    best_tier = "aucun"
    seen_since = datetime.utcnow() - timedelta(days=max_age_days)
    this_year = date.today().year

    for tier, year_tol, km_tol, match_fuel, match_gearbox, same_country in TIERS:
        conditions: list[Any] = [
            Listing.make == target.make,
            Listing.model == target.model,
            Listing.price_eur.is_not(None),
            Listing.price_eur > 500,
            Listing.last_seen >= seen_since,
        ]
        # Une annonce reelle ne se compare qu'a des annonces reelles, et
        # reciproquement.
        if target.source in SYNTHETIC_SOURCES:
            conditions.append(Listing.source.in_(SYNTHETIC_SOURCES))
        else:
            conditions.append(Listing.source.not_in(SYNTHETIC_SOURCES))
        if target.listing_id:
            conditions.append(Listing.id != target.listing_id)
        if exclude_ids:
            conditions.append(Listing.id.not_in(list(exclude_ids)))
        if target.year:
            conditions.append(Listing.year.between(target.year - year_tol, target.year + year_tol))
        if target.km:
            low = int(target.km * (1 - km_tol))
            high = int(target.km * (1 + km_tol)) + 15_000
            conditions.append(Listing.km.between(low, high))
        if match_fuel and target.fuel is not Fuel.UNKNOWN:
            conditions.append(Listing.fuel == target.fuel.value)
        elif target.fuel in FUEL_FAMILIES:
            # Meme quand le palier renonce a l'energie exacte, il ne renonce
            # pas a la famille: une thermique reste comparee a des thermiques.
            conditions.append(Listing.fuel.in_(FUEL_FAMILIES[target.fuel]))
        if match_gearbox and target.gearbox is not Gearbox.UNKNOWN:
            conditions.append(Listing.gearbox == target.gearbox.value)
        if same_country:
            conditions.append(Listing.country == target.country)

        rows = session.execute(
            select(*FACTS_COLUMNS).where(and_(*conditions)).limit(400)
        ).all()
        candidates = deduplicate(
            [_facts_from_row(row, this_year) for row in rows],
            exclude_fingerprint=target.fingerprint,
        )
        # S'arreter au premier palier qui se suffit a lui-meme, et non au
        # premier qui atteint un seuil unique: quatre annonces strictement
        # comparables valent mieux que douze "meme modele, toutes energies",
        # et l'ancienne regle jetait les quatre pour garder les douze.
        if len(candidates) >= required_comps(tier, min_count):
            return candidates, tier
        if len(candidates) > len(best):
            best, best_tier = candidates, tier

    return best, best_tier if best else "aucun"


def deduplicate(candidates: list[Facts], *, exclude_fingerprint: str = "") -> list[Facts]:
    """One physical vehicle, one vote.

    The same car is routinely posted on three sites at three prices, and
    dealers cross-post far more than private sellers. Counting each copy
    would let a handful of vehicles dominate the sample, and since dealer
    prices are the higher ones the bias is systematically upward: every
    advert would look like a better deal than it is. Where a vehicle appears
    several times, the cheapest listing wins, because that is the price at
    which it can actually be bought.
    """
    best: dict[str, Facts] = {}
    unidentified: list[Facts] = []
    for candidate in candidates:
        key = candidate.fingerprint
        if not key:
            unidentified.append(candidate)
            continue
        if exclude_fingerprint and key == exclude_fingerprint:
            continue            # the target advertised elsewhere is not a comparable
        current = best.get(key)
        if current is None or (candidate.price_eur or 0) < (current.price_eur or 0):
            best[key] = candidate
    return list(best.values()) + unidentified


def tier_confidence(tier: str) -> float:
    """How much the selection tier itself deserves to be trusted."""
    return {
        "strict": 1.0,
        "modele_carburant": 0.9,
        "modele": 0.75,
        "modele_europe": 0.6,
        "modele_large": 0.4,
        "aucun": 0.0,
    }.get(tier, 0.5)
