"""Low-level parsers for the number and date formats used across Europe.

`12.500 EUR`, `12 500 €`, `£12,500`, `125.000 km`, `110 ch`, `81 kW`,
`03/2018` - all of it lands here before anything else looks at it.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date

_SPACES = dict.fromkeys(map(ord, "    "), " ")
_NUMBER_RE = re.compile(r"\d[\d\s.,  ]*\d|\d")


def clean_text(value: str | None) -> str | None:
    """Collapse whitespace and normalise exotic spaces; keep accents."""
    if value is None:
        return None
    value = unicodedata.normalize("NFKC", value).translate(_SPACES)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    value = value.strip()
    return value or None


def strip_accents(value: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", value) if unicodedata.category(c) != "Mn"
    )


def slugify(value: str) -> str:
    value = strip_accents(value or "").lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def _first_number_token(text: str) -> str | None:
    match = _NUMBER_RE.search(unicodedata.normalize("NFKC", text).translate(_SPACES))
    return match.group(0) if match else None


def parse_price(value: str | float | int | None) -> float | None:
    """Parse a money amount, treating `.` `,` and spaces as thousand separators.

    Cars do not cost `12,5`, so an ambiguous `12,500` is read as 12500. Use
    `parse_decimal` when a fractional value is actually plausible.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    token = _first_number_token(str(value))
    if token is None:
        return None
    digits = re.sub(r"[^\d]", "", token)
    if not digits:
        return None
    # A trailing `,xx` / `.xx` is a decimal part, not a thousands group.
    tail = re.search(r"[.,](\d{1,2})$", token)
    if tail and len(digits) > len(tail.group(1)):
        whole = digits[: -len(tail.group(1))]
        return float(f"{whole}.{tail.group(1)}")
    return float(digits)


def parse_decimal(value: str | float | int | None) -> float | None:
    """Parse a value where a decimal part is expected (engine size, rating)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    token = _first_number_token(str(value))
    if token is None:
        return None
    token = token.replace(" ", "")
    if "," in token and "." in token:
        sep = max(token.rfind(","), token.rfind("."))
        token = re.sub(r"[.,]", "", token[:sep]) + "." + token[sep + 1 :]
    elif "," in token:
        token = token.replace(",", ".")
    try:
        return float(token)
    except ValueError:
        return None


def parse_km(value: str | float | int | None) -> int | None:
    """Mileage, with unit awareness (miles are converted to kilometres)."""
    if value is None:
        return None
    raw = str(value).lower()
    amount = parse_price(value)
    if amount is None:
        return None
    if re.search(r"\b(mi|miles|mile)\b", raw) and "km" not in raw:
        amount *= 1.60934
    # "125 tkm" / "125.000" style shorthands used on German sites.
    if re.search(r"\btkm\b", raw) and amount < 1000:
        amount *= 1000
    km = int(round(amount))
    return km if 0 <= km <= 2_000_000 else None


def parse_power_hp(value: str | float | int | None) -> int | None:
    """Engine power in metric horsepower; kW inputs are converted."""
    if value is None:
        return None
    raw = str(value).lower()
    amount = parse_price(value)
    if amount is None:
        return None
    is_kw = bool(re.search(r"\bkw\b", raw)) and not re.search(r"\b(ch|cv|hp|ps|bhp)\b", raw)
    if is_kw:
        amount = amount * 1.35962
    hp = int(round(amount))
    return hp if 15 <= hp <= 1500 else None


def parse_year(value: str | int | None) -> int | None:
    if value is None:
        return None
    match = re.search(r"(19[7-9]\d|20[0-4]\d)", str(value))
    if not match:
        return None
    year = int(match.group(1))
    return year if year <= date.today().year + 1 else None


def parse_registration(value: str | None) -> date | None:
    """Parse a first-registration date: `03/2018`, `2018-03-01`, `mars 2018`."""
    if not value:
        return None
    text = strip_accents(str(value).lower())
    iso = re.search(r"(19[7-9]\d|20[0-4]\d)-(\d{1,2})(?:-(\d{1,2}))?", text)
    if iso:
        year, month = int(iso.group(1)), int(iso.group(2))
        day = int(iso.group(3) or 1)
        return _safe_date(year, month, day)
    slashed = re.search(r"\b(\d{1,2})[/.\-](19[7-9]\d|20[0-4]\d)\b", text)
    if slashed:
        return _safe_date(int(slashed.group(2)), int(slashed.group(1)), 1)
    months = {
        "janv": 1, "jan": 1, "fevr": 2, "feb": 2, "mars": 3, "mar": 3, "avr": 4, "apr": 4,
        "mai": 5, "may": 5, "juin": 6, "jun": 6, "juil": 7, "jul": 7, "aou": 8, "aug": 8,
        "sept": 9, "sep": 9, "oct": 10, "okt": 10, "nov": 11, "dec": 12, "dez": 12,
    }
    named = re.search(r"([a-z]{3,4})[a-z.]*\s+(19[7-9]\d|20[0-4]\d)", text)
    if named and named.group(1) in months:
        return _safe_date(int(named.group(2)), months[named.group(1)], 1)
    year = parse_year(text)
    return _safe_date(year, 6, 1) if year else None


def _safe_date(year: int | None, month: int, day: int) -> date | None:
    if not year or not 1 <= month <= 12:
        return None
    try:
        return date(year, month, max(1, min(day, 28)))
    except ValueError:
        return None
