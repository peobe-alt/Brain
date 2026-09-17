"""The shape of an expert report.

Used as the structured-output schema for the model call, so the pipeline
always gets the same fields back and can score them without parsing prose.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["info", "attention", "serieux", "redhibitoire"]


class PhotoFinding(BaseModel):
    photo_index: int = Field(description="Index de la photo analysee, a partir de 0")
    observation: str = Field(description="Ce qui est visible, factuellement")
    severity: Severity = Field(description="Gravite de ce qui est observe")


class RedFlag(BaseModel):
    label: str = Field(description="Le probleme, en une phrase courte")
    severity: Severity
    evidence: str = Field(description="D'ou vient le constat: photo n, description, incoherence")
    estimated_cost_eur: int = Field(default=0, description="Cout de remise en etat estime, 0 si inconnu")


class ExpertReport(BaseModel):
    """What an independent appraiser would write after studying the advert."""

    summary: str = Field(description="Verdict en 2 a 4 phrases, sans langue de bois")
    condition_score: int = Field(ge=0, le=100, description="Etat apparent du vehicule")
    consistency_score: int = Field(
        ge=0, le=100,
        description="Coherence entre kilometrage, annee, usure visible, prix et description",
    )
    photo_findings: list[PhotoFinding] = Field(default_factory=list)
    red_flags: list[RedFlag] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list, description="Points reellement rassurants")
    known_issues_to_check: list[str] = Field(
        default_factory=list, description="Faiblesses connues de cette motorisation a verifier"
    )
    questions_to_seller: list[str] = Field(
        default_factory=list, description="Questions precises a poser avant de se deplacer"
    )
    negotiation_levers: list[str] = Field(
        default_factory=list, description="Arguments chiffres pour negocier"
    )
    estimated_repairs_eur: int = Field(
        default=0, description="Budget de remise en etat a prevoir, en euros"
    )
    verdict: Literal["grab", "check", "avoid"] = Field(
        description="grab: a saisir, check: a voir de pres, avoid: a fuir"
    )
    confidence: int = Field(ge=0, le=100, description="Confiance dans cette analyse")

    def risk_score(self) -> int:
        """0..100 risk level derived from the red flags found."""
        weights = {"info": 3, "attention": 12, "serieux": 30, "redhibitoire": 60}
        raw = sum(weights.get(flag.severity, 10) for flag in self.red_flags)
        raw += max(0, 60 - self.consistency_score) // 2
        return max(0, min(100, raw))
