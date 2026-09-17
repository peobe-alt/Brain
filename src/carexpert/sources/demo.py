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
from datetime import date, datetime, timedelta
from typing import Iterator

from ..normalize import enrich
from ..schemas import Fuel, Gearbox, ListingData, Photo, SearchQuery, SellerType
from .base import SourceAdapter, SourceInfo

# make, model, body words, new price, fuels, premium?
CATALOG: list[tuple[str, str, str, int, tuple[Fuel, ...], bool]] = [
    ("Peugeot", "308", "SW Allure", 31000, (Fuel.DIESEL, Fuel.PETROL), False),
    ("Peugeot", "3008", "GT Line", 39000, (Fuel.DIESEL, Fuel.PHEV), False),
    ("Renault", "Clio", "Intens", 22000, (Fuel.PETROL, Fuel.DIESEL), False),
    ("Renault", "Captur", "Zen", 26000, (Fuel.PETROL, Fuel.PHEV), False),
    ("Volkswagen", "Golf", "Confortline", 33000, (Fuel.DIESEL, Fuel.PETROL), False),
    ("Volkswagen", "Tiguan", "Carat", 42000, (Fuel.DIESEL, Fuel.PETROL), False),
    ("Toyota", "Yaris", "Dynamic", 23000, (Fuel.HYBRID, Fuel.PETROL), False),
    ("Dacia", "Sandero", "Stepway", 16000, (Fuel.PETROL, Fuel.LPG), False),
    ("BMW", "Serie 3", "Touring", 54000, (Fuel.DIESEL, Fuel.PETROL), True),
    ("Audi", "A3", "Sportback S line", 40000, (Fuel.DIESEL, Fuel.PETROL), True),
    ("Mercedes-Benz", "Classe C", "Break", 56000, (Fuel.DIESEL, Fuel.PHEV), True),
    ("Tesla", "Model 3", "Long Range", 52000, (Fuel.ELECTRIC,), True),
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
        make, model, version, new_price, fuels, premium = rng.choice(CATALOG)
        fuel = rng.choice(fuels)
        year = rng.randint(date.today().year - 10, date.today().year - 1)
        age = date.today().year - year + rng.random()
        km = max(3000, int(rng.gauss(14_500, 4_000) * age))
        gearbox = Gearbox.AUTOMATIC if (premium or rng.random() < 0.4) else Gearbox.MANUAL
        options = rng.sample(OPTION_POOL, rng.randint(0, 6))
        city, postcode, country = rng.choice(CITIES)

        fair = new_price * residual_ratio(age, km, premium, fuel)
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
            title=f"{make} {model} {version} {int(round(price / 100) * 100)}",
            description=description,
            price=round(price, -1),
            currency="EUR",
            make=make,
            model=model,
            version=version,
            year=year,
            first_registration=date(year, rng.randint(1, 12), 15),
            km=km,
            fuel=fuel,
            gearbox=gearbox,
            power_hp=rng.choice([90, 100, 110, 120, 130, 150, 190, 245]),
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
        listing.title = f"{make} {model} {version} {FUEL_LABEL[fuel]} {year}".replace("  ", " ")
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
