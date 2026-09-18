"""Canonical vocabulary shared by every layer of the pipeline.

A scraped page, a valuation, an expert report and an alert all speak these
types. Source adapters are responsible for mapping their own site's wording
onto them - nothing downstream should ever see site-specific strings.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class Fuel(str, Enum):
    PETROL = "petrol"
    DIESEL = "diesel"
    HYBRID = "hybrid"           # non rechargeable (HEV / MHEV)
    PHEV = "phev"               # hybride rechargeable
    ELECTRIC = "electric"
    LPG = "lpg"                 # GPL
    CNG = "cng"                 # GNV
    ETHANOL = "ethanol"         # superethanol E85 / flexfuel
    OTHER = "other"
    UNKNOWN = "unknown"


class Gearbox(str, Enum):
    MANUAL = "manual"
    AUTOMATIC = "automatic"
    UNKNOWN = "unknown"


class SellerType(str, Enum):
    PRIVATE = "private"
    PRO = "pro"
    UNKNOWN = "unknown"


class BodyType(str, Enum):
    CITY = "city"
    HATCH = "hatch"
    SEDAN = "sedan"
    ESTATE = "estate"
    SUV = "suv"
    COUPE = "coupe"
    CABRIO = "cabrio"
    VAN = "van"
    PICKUP = "pickup"
    UNKNOWN = "unknown"


class Verdict(str, Enum):
    GRAB = "grab"       # a saisir
    CHECK = "check"     # a voir de pres
    AVOID = "avoid"     # a fuir


#: Static fallback rates used when no FX provider is configured. Europe-wide
#: scanning needs a common currency; plug a real FX feed in production.
FX_TO_EUR: dict[str, float] = {
    "EUR": 1.0,
    "GBP": 1.17,
    "CHF": 1.06,
    "PLN": 0.23,
    "SEK": 0.089,
    "NOK": 0.086,
    "DKK": 0.134,
    "CZK": 0.040,
    "HUF": 0.0025,
    "RON": 0.20,
    "BGN": 0.51,
}


def to_eur(amount: float | None, currency: str | None) -> float | None:
    if amount is None:
        return None
    rate = FX_TO_EUR.get((currency or "EUR").upper())
    if rate is None:
        return None
    return round(amount * rate, 2)


class Photo(BaseModel):
    url: str
    width: int | None = None
    height: int | None = None
    caption: str | None = None


class ListingData(BaseModel):
    """One vehicle advert, normalised.

    Source adapters return this; the pipeline enriches it in place before it
    ever reaches the database.
    """

    # Provenance
    source: str
    source_id: str
    url: str
    country: str = "FR"

    # Commercial
    title: str
    description: str | None = None
    price: float | None = None
    currency: str = "EUR"
    price_eur: float | None = None
    vat_deductible: bool | None = None

    # Vehicle
    make: str | None = None
    model: str | None = None
    version: str | None = None
    year: int | None = None
    first_registration: date | None = None
    km: int | None = None
    fuel: Fuel = Fuel.UNKNOWN
    gearbox: Gearbox = Gearbox.UNKNOWN
    power_hp: int | None = None
    body: BodyType = BodyType.UNKNOWN
    doors: int | None = None
    seats: int | None = None
    color: str | None = None
    owners: int | None = None
    service_history: bool | None = None
    technical_inspection: str | None = None
    options: list[str] = Field(default_factory=list)

    # Seller
    seller_type: SellerType = SellerType.UNKNOWN
    seller_name: str | None = None
    city: str | None = None
    region: str | None = None
    postcode: str | None = None

    # Media & timing
    photos: list[Photo] = Field(default_factory=list)
    posted_at: datetime | None = None
    scraped_at: datetime = Field(default_factory=datetime.utcnow)

    #: Anything the adapter captured but the schema does not model yet.
    extra: dict = Field(default_factory=dict)

    @field_validator("country", mode="before")
    @classmethod
    def _upper_country(cls, v: str | None) -> str:
        return (v or "FR").upper()[:2]

    @property
    def age_years(self) -> float | None:
        """Vehicle age in years, from registration date when available."""
        ref = self.first_registration
        if ref is not None:
            return max(0.0, (date.today() - ref).days / 365.25)
        if self.year:
            return max(0.0, date.today().year - self.year + 0.5)
        return None

    @property
    def km_per_year(self) -> float | None:
        age = self.age_years
        if self.km is None or not age or age < 0.5:
            return None
        return self.km / age

    def fill_price_eur(self) -> None:
        if self.price_eur is None:
            self.price_eur = to_eur(self.price, self.currency)


class SearchQuery(BaseModel):
    """What the user is hunting for, passed down to every source adapter."""

    make: str | None = None
    model: str | None = None
    keywords: str | None = None
    price_min: int | None = None
    price_max: int | None = None
    year_min: int | None = None
    year_max: int | None = None
    km_max: int | None = None
    fuel: Fuel | None = None
    gearbox: Gearbox | None = None
    countries: list[str] = Field(default_factory=lambda: ["FR"])
    radius_km: int | None = None
    postcode: str | None = None
    limit: int = 100

    def label(self) -> str:
        bits = [b for b in (self.make, self.model, self.keywords) if b]
        return " ".join(bits) or "recherche"
