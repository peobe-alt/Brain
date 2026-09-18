"""An offline, synthetic used-car market.

It exists for three reasons: the whole pipeline can be demonstrated without
touching a single real website, the test suite gets a deterministic market
with known good deals and known traps, and valuation logic can be checked
against ground truth we control.

The generator plants three populations:

* `fair`    - priced where the market sits;
* `bargain` - genuinely underpriced, clean history (what we want to surface);
* `trap`    - cheaper still, but the description admits why (what we want
              to push down even though the price looks irresistible).
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterator

from ..normalize import enrich
from ..schemas import Fuel, Gearbox, ListingData, Photo, SearchQuery, SellerType
from .base import SourceAdapter, SourceInfo


@dataclass(frozen=True, slots=True)
class Trim:
    """One engine-and-finish combination: the level at which a price is set.

    A model name alone does not price a car. A 308 with a 100 hp diesel and a
    308 SW with a 130 hp petrol are two different products sharing a badge,
    and a market that ignores that is a market where the hard part of
    valuation has been quietly removed.
    """

    version: str
    fuel: Fuel
    power_hp: int
    new_price: int


# make, model, premium?, trims
CATALOG: list[tuple[str, str, bool, tuple[Trim, ...]]] = [
    ("Peugeot", "308", False, (
        Trim("Active BlueHDi 100", Fuel.DIESEL, 100, 27_500),
        Trim("Allure PureTech 130", Fuel.PETROL, 130, 30_500),
        Trim("SW GT BlueHDi 130", Fuel.DIESEL, 130, 35_500),
    )),
    ("Peugeot", "3008", False, (
        Trim("Active PureTech 130", Fuel.PETROL, 130, 33_500),
        Trim("GT BlueHDi 130", Fuel.DIESEL, 130, 40_000),
        Trim("GT Hybrid 225", Fuel.PHEV, 225, 51_000),
    )),
    ("Renault", "Clio", False, (
        Trim("Life SCe 65", Fuel.PETROL, 65, 17_500),
        Trim("Intens TCe 100", Fuel.PETROL, 100, 21_500),
        Trim("Intens Blue dCi 100", Fuel.DIESEL, 100, 23_500),
    )),
    ("Renault", "Captur", False, (
        Trim("Zen TCe 90", Fuel.PETROL, 90, 23_000),
        Trim("Intens TCe 140", Fuel.PETROL, 140, 28_500),
        Trim("Initiale E-Tech 160", Fuel.PHEV, 160, 36_500),
    )),
    ("Volkswagen", "Golf", False, (
        Trim("Life 1.0 TSI 110", Fuel.PETROL, 110, 29_500),
        Trim("Confortline 1.6 TDI 115", Fuel.DIESEL, 115, 32_500),
        Trim("GTD 2.0 TDI 200", Fuel.DIESEL, 200, 45_000),
    )),
    ("Volkswagen", "Tiguan", False, (
        Trim("Life 1.5 TSI 130", Fuel.PETROL, 130, 36_500),
        Trim("Carat 2.0 TDI 150", Fuel.DIESEL, 150, 44_000),
        Trim("R-Line 2.0 TDI 200", Fuel.DIESEL, 200, 52_000),
    )),
    ("Toyota", "Yaris", False, (
        Trim("Dynamic 1.0 VVT-i 72", Fuel.PETROL, 72, 18_500),
        Trim("Design Hybride 116", Fuel.HYBRID, 116, 24_500),
    )),
    ("Dacia", "Sandero", False, (
        Trim("Essential SCe 65", Fuel.PETROL, 65, 12_500),
        Trim("Stepway TCe 90", Fuel.PETROL, 90, 16_500),
        Trim("Stepway ECO-G 100", Fuel.LPG, 100, 17_500),
    )),
    ("BMW", "Serie 3", True, (
        Trim("318d Business", Fuel.DIESEL, 150, 45_000),
        Trim("320d xDrive Touring", Fuel.DIESEL, 190, 56_000),
        Trim("330e M Sport", Fuel.PHEV, 292, 62_000),
    )),
    ("Audi", "A3", True, (
        Trim("30 TDI Business", Fuel.DIESEL, 116, 35_500),
        Trim("Sportback 35 TFSI S line", Fuel.PETROL, 150, 42_000),
        Trim("Sportback 40 TFSI e", Fuel.PHEV, 204, 48_000),
    )),
    ("Mercedes-Benz", "Classe C", True, (
        Trim("C 200 d Avantgarde", Fuel.DIESEL, 160, 48_000),
        Trim("C 220 d Break AMG Line", Fuel.DIESEL, 200, 58_000),
        Trim("C 300 e Break", Fuel.PHEV, 320, 64_000),
    )),
    ("Tesla", "Model 3", True, (
        Trim("Standard Plus", Fuel.ELECTRIC, 306, 48_000),
        Trim("Long Range AWD", Fuel.ELECTRIC, 498, 58_000),
    )),
]

FUEL_LABEL = {
    Fuel.PETROL: "essence", Fuel.DIESEL: "diesel", Fuel.HYBRID: "hybride",
    Fuel.PHEV: "hybride rechargeable", Fuel.ELECTRIC: "electrique",
    Fuel.LPG: "GPL", Fuel.CNG: "GNV", Fuel.OTHER: "", Fuel.UNKNOWN: "",
}

CITIES = [
    ("Lyon", "69003", "FR"), ("Paris", "75015", "FR"), ("Bordeaux", "33000", "FR"),
    ("Lille", "59000", "FR"), ("Nantes", "44000", "FR"), ("Marseille", "13008", "FR"),
    ("Munich", "80331", "DE"), ("Berlin", "10115", "DE"), ("Milan", "20121", "IT"),
    ("Bruxelles", "1000", "BE"), ("Madrid", "28001", "ES"), ("Amsterdam", "1011", "NL"),
]

OPTION_POOL = [
    "GPS", "camera de recul", "sieges chauffants", "toit panoramique", "attelage",
    "regulateur adaptatif", "Apple CarPlay", "jantes alu 18", "clim automatique bi-zone",
    "phares full LED", "hayon electrique", "radar de recul",
]

BARGAIN_REASONS = [
    "Vente rapide cause demenagement a l'etranger, vehicule disponible immediatement.",
    "Deuxieme voiture du foyer, plus utilisee depuis le passage au teletravail.",
    "Reprise du concessionnaire refusee faute de place, prix ajuste pour vente rapide.",
    "Succession, vehicule peu roule, entretien suivi en concession.",
]

TRAP_DEFECTS = [
    ("Vendu en l'etat, moteur fait un bruit anormal a froid.", 0.62),
    ("Compteur non garanti, vehicule importe d'Allemagne sans historique.", 0.66),
    ("Vehicule accidente a l'avant, reparation non effectuee, CT a faire.", 0.55),
    ("Distribution a faire, embrayage patine, voyant moteur allume.", 0.64),
    ("Ancien taxi, 4eme main, consomme de l'huile entre deux vidanges.", 0.60),
]

CLEAN_DESCRIPTIONS = [
    "Carnet d'entretien complet, factures a l'appui, CT vierge, non fumeur.",
    "Premiere main, entretien a jour en concession, pneus neufs, distribution faite.",
    "Vehicule suivi, revision recente, freins neufs, jamais accidente.",
    "Garantie 12 mois, controle technique vierge, historique complet.",
]


def residual_ratio(age_years: float, km: int, premium: bool, fuel: Fuel) -> float:
    """Share of the new price a car still holds, before condition effects."""
    age_rate = 0.155 if premium else 0.125
    if fuel is Fuel.ELECTRIC:
        age_rate += 0.03          # faster early depreciation on EVs
    km_rate = 0.30 if fuel is Fuel.DIESEL else 0.38
    value = (1 - age_rate) ** max(age_years, 0.0)
    value *= (1 - km_rate) ** (max(km, 0) / 150_000)
    return max(0.06, value)


class DemoSource(SourceAdapter):
    """A reproducible market you can run with no network at all."""

    def __init__(self, seed: int = 42, size: int = 120) -> None:
        self.info = SourceInfo(
            name="demo",
            label="Marche de demonstration (hors ligne)",
            countries=["FR", "DE", "IT", "BE", "ES", "NL"],
            notes="Donnees synthetiques: sert aux tests et a la demo sans reseau.",
        )
        self.seed = seed
        self.size = size

    def search(self, query: SearchQuery) -> Iterator[ListingData]:
        rng = random.Random(self.seed)
        produced = 0
        for index in range(self.size * 3):
            listing = self._generate(rng, index)
            if not _matches(listing, query):
                continue
            yield listing
            produced += 1
            if produced >= min(query.limit, self.size):
                return

    def _generate(self, rng: random.Random, index: int) -> ListingData:
        make, model, premium, trims = rng.choice(CATALOG)
        trim = rng.choice(trims)
        fuel = trim.fuel
        year = rng.randint(date.today().year - 10, date.today().year - 1)
        age = date.today().year - year + rng.random()
        km = max(3000, int(rng.gauss(14_500, 4_000) * age))
        gearbox = Gearbox.AUTOMATIC if (premium or rng.random() < 0.4) else Gearbox.MANUAL
        options = rng.sample(OPTION_POOL, rng.randint(0, 6))
        city, postcode, country = rng.choice(CITIES)

        fair = trim.new_price * residual_ratio(age, km, premium, fuel)
        fair *= 1 + 0.012 * len(options)                 # options add a little
        fair *= 1.05 if gearbox is Gearbox.AUTOMATIC else 1.0

        roll = rng.random()
        if roll < 0.08:
            kind = "bargain"
            price = fair * rng.uniform(0.74, 0.84)
            description = f"{rng.choice(BARGAIN_REASONS)} {rng.choice(CLEAN_DESCRIPTIONS)}"
        elif roll < 0.15:
            kind = "trap"
            defect, factor = rng.choice(TRAP_DEFECTS)
            price = fair * factor * rng.uniform(0.95, 1.05)
            description = defect
        else:
            kind = "fair"
            price = fair * rng.uniform(0.94, 1.1)
            description = rng.choice(CLEAN_DESCRIPTIONS)

        if options:
            description += " Options: " + ", ".join(options) + "."
        seller_type = SellerType.PRO if rng.random() < 0.45 else SellerType.PRIVATE
        seller_name = (
            f"Garage {rng.choice(['Martin', 'Dupuis', 'Weber', 'Rossi', 'Lopez'])}"
            if seller_type is SellerType.PRO
            else "Particulier"
        )
        listing_id = hashlib.sha1(f"{self.seed}-{index}".encode()).hexdigest()[:10]
        posted = datetime.utcnow() - timedelta(days=rng.randint(0, 45))

        listing = ListingData(
            source="demo",
            source_id=listing_id,
            url=f"https://demo.carexpert.local/annonce/{listing_id}",
            country=country,
            title=f"{make} {model} {trim.version} {int(round(price / 100) * 100)}",
            description=description,
            price=round(price, -1),
            currency="EUR",
            make=make,
            model=model,
            version=trim.version,
            year=year,
            first_registration=date(year, rng.randint(1, 12), 15),
            km=km,
            fuel=fuel,
            gearbox=gearbox,
            power_hp=trim.power_hp,
            owners=rng.randint(1, 4),
            city=city,
            postcode=postcode,
            seller_type=seller_type,
            seller_name=seller_name,
            photos=[
                Photo(url=f"https://demo.carexpert.local/photo/{listing_id}/{i}.jpg")
                for i in range(rng.randint(3, 10))
            ],
            posted_at=posted,
            extra={"demo_kind": kind, "demo_fair_price": round(fair, 2)},
        )
        listing.title = (
            f"{make} {model} {trim.version} {FUEL_LABEL[fuel]} {year}".replace("  ", " ")
        )
        return enrich(listing)


def _matches(listing: ListingData, query: SearchQuery) -> bool:
    if query.make and (listing.make or "").lower() != query.make.lower():
        return False
    if query.model and (listing.model or "").lower() != query.model.lower():
        return False
    if query.price_max and (listing.price_eur or 0) > query.price_max:
        return False
    if query.price_min and (listing.price_eur or 0) < query.price_min:
        return False
    if query.year_min and (listing.year or 0) < query.year_min:
        return False
    if query.km_max and (listing.km or 0) > query.km_max:
        return False
    if query.fuel and listing.fuel is not query.fuel:
        return False
    if query.countries and listing.country not in query.countries:
        return False
    return True
