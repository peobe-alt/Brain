"""Turning messy, multilingual advert text into the canonical vocabulary."""

from .text import (
    clean_text,
    parse_decimal,
    parse_km,
    parse_power_hp,
    parse_price,
    parse_registration,
    parse_year,
    slugify,
)
from .vehicle import (
    canonical_make,
    canonical_model,
    detect_body,
    detect_fuel,
    detect_gearbox,
    detect_options,
    enrich,
)
from .signals import Signal, detect_signals

__all__ = [
    "Signal",
    "canonical_make",
    "canonical_model",
    "clean_text",
    "detect_body",
    "detect_fuel",
    "detect_gearbox",
    "detect_options",
    "detect_signals",
    "enrich",
    "parse_decimal",
    "parse_km",
    "parse_power_hp",
    "parse_price",
    "parse_registration",
    "parse_year",
    "slugify",
]
