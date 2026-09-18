"""Matching a listing against documented model-specific weaknesses."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

import re

from ..normalize.text import strip_accents
from ..schemas import ListingData

KNOWLEDGE_DIR = Path(__file__).parent / "knowledge"


@dataclass(slots=True)
class Defect:
    code: str
    titre: str
    symptomes: str
    verifier: str
    cout_eur: int
    severite: int
    makes: list[str]
    keywords: list[str]
    years: tuple[int, int] | None
    fuels: list[str] = field(default_factory=list)
    conditions: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "titre": self.titre,
            "symptomes": self.symptomes,
            "verifier": self.verifier,
            "cout_eur": self.cout_eur,
            "severite": self.severite,
        }


@lru_cache(maxsize=1)
def load_defects(path: Path | None = None) -> list[Defect]:
    source = path or (KNOWLEDGE_DIR / "defects.yaml")
    if not source.exists():
        return []
    raw = yaml.safe_load(source.read_text(encoding="utf-8")) or []
    defects: list[Defect] = []
    for item in raw:
        years = item.get("years")
        defects.append(
            Defect(
                code=item["code"],
                titre=item.get("titre", item["code"]),
                symptomes=item.get("symptomes", ""),
                verifier=item.get("verifier", ""),
                cout_eur=int(item.get("cout_eur", 0)),
                severite=int(item.get("severite", 2)),
                makes=[m for m in item.get("makes", [])],
                keywords=[strip_accents(k.lower()) for k in item.get("keywords", [])],
                years=(int(years[0]), int(years[1])) if years else None,
                fuels=[str(f).lower() for f in item.get("fuels", [])],
                conditions=dict(item.get("conditions") or {}),
            )
        )
    return defects


@lru_cache(maxsize=1)
def load_checklist() -> str:
    path = KNOWLEDGE_DIR / "checklist.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def match_defects(listing: ListingData, defects: list[Defect] | None = None) -> list[Defect]:
    """Known weaknesses that plausibly apply to this exact car."""
    hay = strip_accents(
        " ".join(
            part for part in (listing.title, listing.version, listing.description) if part
        ).lower()
    )
    year = listing.year or (listing.first_registration.year if listing.first_registration else None)
    matched: list[Defect] = []
    for defect in defects or load_defects():
        if defect.makes and (listing.make or "") not in defect.makes:
            continue
        if defect.fuels and listing.fuel.value not in defect.fuels:
            continue
        if defect.years and year and not (defect.years[0] <= year <= defect.years[1]):
            continue
        # Word boundaries matter: `hayon electrique` is an option, not an
        # electric car, and `puretech` must not fire on a longer word.
        if defect.keywords and not any(_kw(hay, k) for k in defect.keywords):
            continue
        if not _conditions_met(defect, listing):
            continue
        matched.append(defect)
    return sorted(matched, key=lambda d: -d.severite)


def _kw(hay: str, keyword: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", hay) is not None


def _conditions_met(defect: Defect, listing: ListingData) -> bool:
    """Numeric guards declared in YAML, e.g. `km_per_year_max: 12000`."""
    checks = defect.conditions
    if not checks:
        return True
    km_year = listing.km_per_year
    if "km_per_year_max" in checks:
        if km_year is None or km_year > float(checks["km_per_year_max"]):
            return False
    if "km_per_year_min" in checks:
        if km_year is None or km_year < float(checks["km_per_year_min"]):
            return False
    if "km_min" in checks and (listing.km or 0) < float(checks["km_min"]):
        return False
    if "km_max" in checks and (listing.km or 0) > float(checks["km_max"]):
        return False
    return True
