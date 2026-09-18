"""Value curves used to bring a comparable onto the target car's terms.

A 2019 Golf with 60 000 km and a 2019 Golf with 140 000 km are not the same
advert. Before comparing prices we restate every comparable as if it had the
target's age, mileage, gearbox and equipment. All curves are multiplicative,
so the adjustment is simply `f(target) / f(comparable)`.

The coefficients below are deliberately simple and explicit: they are meant
to be read, argued with, and refitted on your own collected data (see
`fit_depreciation` for the refit path).
"""

from __future__ import annotations

from ..normalize.vehicle import is_premium
from ..schemas import Fuel, Gearbox, SellerType

#: Yearly value loss, before mileage. Premium loses more, early on.
AGE_RATE_MAINSTREAM = 0.125
AGE_RATE_PREMIUM = 0.155
AGE_RATE_ELECTRIC_BONUS = 0.030

#: Value loss per 150 000 km. Diesels are built for distance and hold better.
KM_RATE_BY_FUEL: dict[Fuel, float] = {
    Fuel.DIESEL: 0.30,
    Fuel.PETROL: 0.38,
    Fuel.HYBRID: 0.34,
    Fuel.PHEV: 0.36,
    Fuel.ELECTRIC: 0.28,     # battery, not engine wear, drives EV value
    Fuel.LPG: 0.38,
    Fuel.CNG: 0.38,
    Fuel.ETHANOL: 0.40,      # marche etroit, revente plus difficile
    Fuel.OTHER: 0.35,
    Fuel.UNKNOWN: 0.35,
}

#: Rough market premium a professional listing carries over a private one
#: (warranty, paperwork, margin). Used to compare like with like.
SELLER_FACTOR: dict[SellerType, float] = {
    SellerType.PRO: 1.07,
    SellerType.PRIVATE: 1.0,
    SellerType.UNKNOWN: 1.03,
}

#: Relative price level of a used car by country, EUR-denominated, France = 1.
#: Rough but enough to flag cross-border arbitrage.
COUNTRY_FACTOR: dict[str, float] = {
    "FR": 1.00, "DE": 0.93, "BE": 0.97, "NL": 1.02, "IT": 0.95, "ES": 0.94,
    "PT": 0.98, "AT": 0.99, "LU": 0.96, "PL": 0.88, "CH": 1.08, "GB": 0.90,
    "SE": 0.97, "DK": 1.05, "CZ": 0.90,
}

OPTION_VALUE = 0.010          # per detected option
OPTION_CAP = 0.09             # never more than +9% for equipment
AUTOMATIC_PREMIUM = 1.05


def age_rate(make: str | None, fuel: Fuel) -> float:
    rate = AGE_RATE_PREMIUM if is_premium(make) else AGE_RATE_MAINSTREAM
    if fuel is Fuel.ELECTRIC:
        rate += AGE_RATE_ELECTRIC_BONUS
    return rate


def age_factor(age_years: float | None, make: str | None, fuel: Fuel) -> float:
    if age_years is None:
        return 1.0
    return max(0.05, (1 - age_rate(make, fuel)) ** max(age_years, 0.0))


def km_factor(km: int | None, fuel: Fuel) -> float:
    if km is None:
        return 1.0
    rate = KM_RATE_BY_FUEL.get(fuel, 0.35)
    return max(0.05, (1 - rate) ** (max(km, 0) / 150_000))


def options_factor(options: list[str] | None) -> float:
    count = len(options or [])
    return 1 + min(count * OPTION_VALUE, OPTION_CAP)


def gearbox_factor(gearbox: Gearbox) -> float:
    return AUTOMATIC_PREMIUM if gearbox is Gearbox.AUTOMATIC else 1.0


def seller_factor(seller_type: SellerType) -> float:
    return SELLER_FACTOR.get(seller_type, 1.0)


def country_factor(country: str | None) -> float:
    return COUNTRY_FACTOR.get((country or "FR").upper(), 1.0)


def vehicle_factor(
    *,
    age_years: float | None,
    km: int | None,
    make: str | None,
    fuel: Fuel,
    gearbox: Gearbox,
    options: list[str] | None,
    seller_type: SellerType = SellerType.UNKNOWN,
    country: str | None = None,
) -> float:
    """Everything that makes this particular car worth more or less."""
    return (
        age_factor(age_years, make, fuel)
        * km_factor(km, fuel)
        * options_factor(options)
        * gearbox_factor(gearbox)
        * seller_factor(seller_type)
        * country_factor(country)
    )


def fit_depreciation(samples: list[tuple[float, int, float]]) -> tuple[float, float] | None:
    """Refit age and mileage rates on real data.

    `samples` is `(age_years, km, price_eur)`. Returns `(age_rate, km_rate)`
    fitted by least squares on `log(price) = a + b*age + c*km/150000`, or
    `None` when there is not enough signal. Wire this into a periodic job
    once you have a few thousand listings of your own.
    """
    points = [(a, k / 150_000, p) for a, k, p in samples if p and p > 500 and a is not None]
    if len(points) < 12:
        return None
    from math import exp, log

    n = float(len(points))
    sx1 = sum(p[0] for p in points)
    sx2 = sum(p[1] for p in points)
    sy = sum(log(p[2]) for p in points)
    sx1x1 = sum(p[0] * p[0] for p in points)
    sx2x2 = sum(p[1] * p[1] for p in points)
    sx1x2 = sum(p[0] * p[1] for p in points)
    sx1y = sum(p[0] * log(p[2]) for p in points)
    sx2y = sum(p[1] * log(p[2]) for p in points)

    matrix = [[n, sx1, sx2, sy], [sx1, sx1x1, sx1x2, sx1y], [sx2, sx1x2, sx2x2, sx2y]]
    solution = _solve(matrix)
    if solution is None:
        return None
    _, b, c = solution
    if b >= 0 or c >= 0:       # prices rising with age/km: refuse the fit
        return None
    return (1 - exp(b), 1 - exp(c))


def _solve(matrix: list[list[float]]) -> list[float] | None:
    """Gaussian elimination on an augmented 3x4 matrix (no numpy needed)."""
    size = len(matrix)
    for col in range(size):
        pivot_row = max(range(col, size), key=lambda r: abs(matrix[r][col]))
        if abs(matrix[pivot_row][col]) < 1e-12:
            return None
        matrix[col], matrix[pivot_row] = matrix[pivot_row], matrix[col]
        pivot = matrix[col][col]
        matrix[col] = [value / pivot for value in matrix[col]]
        for row in range(size):
            if row == col:
                continue
            factor = matrix[row][col]
            matrix[row] = [v - factor * pv for v, pv in zip(matrix[row], matrix[col])]
    return [matrix[i][size] for i in range(size)]
