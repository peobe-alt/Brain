"""Un marche qui n'obeit pas a nos courbes, pour mesurer sans se mentir.

`sources/demo.py` genere ses prix avec **exactement** les constantes de
`valuation/adjust.py`: 0,125 et 0,155 de decote annuelle, 0,30 et 0,38 au
kilometrage, +5 % pour une boite automatique, et la meme forme
`(1-r)**age * (1-k)**(km/150000)`. Mesurer la precision de l'estimateur sur
ce marche-la, c'est le regarder inverser son propre generateur. Le chiffre
qui en sort ne peut pas etre mauvais, donc il ne veut rien dire.

Ce module fournit l'autre metre: une loi de prix que l'estimateur ne connait
pas, et qui ressemble davantage a un marche reel.

* **Perte concave en kilometrage.** Les premiers 50 000 km coutent bien plus
  cher que les derniers, la ou notre exponentielle perd le meme pourcentage
  a chaque tranche.
* **Decote annuelle en racine.** Forte la premiere annee, molle la dixieme.
* **Un plancher.** Aucune voiture ne se vend sous sa valeur de reprise, et
  notre modele multiplicatif, lui, descend indefiniment.
* **Une rupture de generation.** Un restylage fait decrocher d'un coup les
  millesimes d'avant. Notre modele n'a rien pour dire ca: il est continu, par
  construction.

Les annonces produites portent une source qui n'existe pas, mais elles
passent par le chemin des annonces **reelles**: c'est bien ce qu'on veut
mesurer. Elles ne doivent donc jamais rejoindre une vraie base, et seule la
base jetable d'un test les recoit.
"""

from __future__ import annotations

import math
import random
from datetime import date

from carexpert.normalize import enrich
from carexpert.schemas import Fuel, Gearbox, ListingData, SellerType

#: Une source qui n'est pas un site: rien ne doit pouvoir la collecter.
SOURCE = "marche-temoin"

#: Prix neuf par modele, pour que les gammes ne se ressemblent pas toutes.
CATALOGUE = [
    ("Renault", "Clio", 21_000),
    ("Peugeot", "208", 22_000),
    ("Volkswagen", "Golf", 30_000),
    ("Toyota", "Yaris", 23_000),
    ("Dacia", "Sandero", 15_000),
    ("Citroen", "C3", 20_000),
]

#: En dessous, une voiture ne se vend plus, elle se reprend.
FLOOR_EUR = 1_200.0

#: Age du restylage, et ce que perdent d'un coup les millesimes d'avant.
GENERATION_AGE = 6.0
GENERATION_DROP = 0.86


def foreign_price(age_years: float, km: int, new_price: float) -> float:
    """Ce que vaut la voiture dans un marche qui ignore nos courbes."""
    age = max(age_years, 0.0)
    by_age = math.exp(-0.30 * age ** 0.75)
    by_km = 1 - 0.55 * (max(km, 0) / 250_000) ** 0.6
    generation = GENERATION_DROP if age >= GENERATION_AGE else 1.0
    return max(FLOOR_EUR, new_price * by_age * max(0.15, by_km) * generation)


def foreign_market(
    size: int = 320,
    *,
    seed: int = 7,
    make: str | None = None,
    model: str | None = None,
    years: tuple[int, int] | None = None,
    km_per_year: int = 14_000,
    noise: float = 0.06,
) -> list[ListingData]:
    """A market priced by `foreign_price`, each advert carrying its truth.

    `extra["juste"]` holds the price the market really asks, which is what
    the estimate is measured against.
    """
    rng = random.Random(seed)
    this_year = date.today().year
    low, high = years or (this_year - 14, this_year - 1)
    catalogue = [
        row for row in CATALOGUE
        if (make is None or row[0] == make) and (model is None or row[1] == model)
    ] or CATALOGUE

    adverts: list[ListingData] = []
    for index in range(size):
        brand, name, new_price = rng.choice(catalogue)
        year = rng.randint(low, high)
        age = this_year - year + rng.random()
        km = max(5_000, int(rng.gauss(km_per_year, km_per_year * 0.32) * age))
        truth = foreign_price(age, km, new_price)
        adverts.append(advert(
            f"{seed}-{index}", brand, name, year, km,
            truth * rng.uniform(1 - noise, 1 + noise), truth,
        ))
    return adverts


def advert(
    source_id: str,
    make: str,
    model: str,
    year: int,
    km: int,
    price: float,
    truth: float,
    *,
    source: str = SOURCE,
    seller_type: SellerType = SellerType.PRIVATE,
) -> ListingData:
    return enrich(ListingData(
        source=source, source_id=str(source_id),
        url=f"https://{source}.invalid/annonce/{source_id}",
        title=f"{make} {model} {year}", price=round(price, -1),
        make=make, model=model, year=year, km=km,
        fuel=Fuel.PETROL, gearbox=Gearbox.MANUAL,
        seller_type=seller_type, country="FR",
        extra={"juste": round(truth, 2)},
    ))
