"""The expert layer: Claude reads the advert the way an appraiser would.

Two modes, same output shape:

* `analyze()` sends the dossier and the photos to Claude and gets a
  structured `ExpertReport` back;
* `heuristic_report()` builds a report from the rule-based signals alone, so
  the pipeline still runs with no API key, offline, or when you want to keep
  the model calls for shortlisted cars only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..config import get_settings
from ..normalize.signals import Signal, detect_signals, repair_budget
from ..schemas import ListingData
from .cost import Cost, estimate_cost, zero
from .knowledge import Defect, match_defects
from .photos import PreparedPhoto, prepare_photos
from .prompts import SYSTEM_PROMPT, build_dossier
from .schema import ExpertReport, RedFlag

log = logging.getLogger(__name__)

SEVERITY_BY_WEIGHT = [(0.85, "redhibitoire"), (0.6, "serieux"), (0.3, "attention")]


class MissingApiKey(RuntimeError):
    """No Anthropic credentials available for the expert analysis."""


@dataclass(slots=True)
class AnalysisResult:
    report: ExpertReport
    model: str
    photos_analyzed: int
    input_tokens: int = 0
    output_tokens: int = 0
    #: Set when the model could not be used and the rule layer stood in.
    degraded_reason: str = ""

    @property
    def used_model(self) -> bool:
        return self.model != "heuristique"

    @property
    def degraded(self) -> bool:
        return bool(self.degraded_reason)

    def cost(self) -> Cost:
        if not self.used_model:
            return zero()
        return estimate_cost(self.model, self.input_tokens, self.output_tokens)


class ExpertAnalyst:
    """Wraps the Messages API call that produces an expert report."""

    def __init__(self, client=None, model: str | None = None) -> None:
        settings = get_settings()
        self.model = model or settings.model
        self.max_tokens = settings.analysis_max_tokens
        self._client = client

    @property
    def client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - dependency present in prod
                raise MissingApiKey("le paquet `anthropic` n'est pas installe") from exc
            settings = get_settings()
            # The SDK also resolves credentials from the environment or an
            # `ant auth login` profile, so an unset key is not fatal by itself.
            self._client = (
                anthropic.Anthropic(api_key=settings.anthropic_api_key)
                if settings.anthropic_api_key
                else anthropic.Anthropic()
            )
        return self._client

    def analyze(
        self,
        listing: ListingData,
        *,
        valuation: dict | None = None,
        with_photos: bool = True,
        max_photos: int | None = None,
    ) -> AnalysisResult:
        """Analyse one advert. Never raises: a failure degrades to the rules.

        A deep pass runs over a batch of shortlisted cars. One rate limit or
        one malformed page must not cost the whole batch, so every failure
        returns the rule-based report with the reason attached, and the
        caller decides what to say about it.
        """
        signals = detect_signals(listing.title, listing.description)
        defects = match_defects(listing)
        photos: list[PreparedPhoto] = (
            prepare_photos(listing.photos, limit=max_photos) if with_photos else []
        )

        dossier = build_dossier(
            listing,
            valuation=valuation,
            signals=signals,
            defects=defects,
            photo_count=len(photos),
        )
        # Images first, question last: the text refers to photo indices, so
        # the order of the blocks is the order the indices mean.
        content: list[dict] = [photo.as_block() for photo in photos]
        content.append({"type": "text", "text": dossier})

        try:
            response = self.client.messages.parse(
                model=self.model,
                max_tokens=self.max_tokens,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": content}],
                output_format=ExpertReport,
                thinking={"type": "adaptive"},
            )
        except Exception as exc:
            reason = _explain(exc)
            log.info("expertise par regles pour %s: %s", listing.url, reason)
            return self._fallback(listing, signals, defects, valuation, reason)

        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) or "non precisee"
            return self._fallback(listing, signals, defects, valuation,
                                  f"analyse refusee par le modele (categorie {category})")
        if stop_reason == "max_tokens":
            return self._fallback(listing, signals, defects, valuation,
                                  "reponse tronquee: augmenter CAREXPERT_ANALYSIS_MAX_TOKENS")

        report = getattr(response, "parsed_output", None)
        if report is None:
            return self._fallback(listing, signals, defects, valuation,
                                  "le modele n'a pas renvoye de rapport exploitable")

        usage = getattr(response, "usage", None)
        return AnalysisResult(
            report=report,
            model=self.model,
            photos_analyzed=len(photos),
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
        )

    def _fallback(
        self, listing: ListingData, signals: list[Signal], defects: list[Defect],
        valuation: dict | None, reason: str,
    ) -> AnalysisResult:
        return AnalysisResult(
            report=heuristic_report(listing, signals, defects, valuation),
            model="heuristique",
            photos_analyzed=0,
            degraded_reason=reason,
        )


def analyze_offline(listing: ListingData, valuation: dict | None = None) -> AnalysisResult:
    """Rule-based report: no network, no API key, no cost."""
    signals = detect_signals(listing.title, listing.description)
    defects = match_defects(listing)
    return AnalysisResult(
        report=heuristic_report(listing, signals, defects, valuation),
        model="heuristique",
        photos_analyzed=0,
    )


def heuristic_report(
    listing: ListingData,
    signals: list[Signal],
    defects: list[Defect],
    valuation: dict | None = None,
) -> ExpertReport:
    """Build a defensible report from the rule layer alone."""
    risks = [s for s in signals if s.polarity == "risk"]
    positives = [s for s in signals if s.polarity == "positive"]
    delta_pct = (valuation or {}).get("delta_pct", 0.0) * 100

    red_flags = [
        RedFlag(
            label=signal.label,
            severity=_severity(signal.weight),
            evidence=f"texte de l'annonce: \"{signal.evidence[:160]}\"",
            estimated_cost_eur=signal.typical_cost_eur,
        )
        for signal in risks
    ]

    unexplained_discount = delta_pct > 22 and not risks
    if unexplained_discount:
        red_flags.append(
            RedFlag(
                label="Decote importante sans justification ecrite",
                severity="attention",
                evidence=f"prix {delta_pct:.0f}% sous le marche, aucune raison donnee dans l'annonce",
                estimated_cost_eur=0,
            )
        )

    km_per_year = listing.km_per_year
    if km_per_year and km_per_year > 32_000:
        red_flags.append(
            RedFlag(
                label=f"Kilometrage annuel eleve ({km_per_year:,.0f} km/an)".replace(",", " "),
                severity="attention",
                evidence="rapport kilometrage / age du vehicule",
                estimated_cost_eur=0,
            )
        )

    condition = 70
    condition -= sum(12 for s in risks if s.weight >= 0.6)
    condition -= sum(6 for s in risks if s.weight < 0.6)
    condition += min(20, int(6 * len(positives)))
    condition = max(0, min(100, condition))

    consistency = 80 - (20 if unexplained_discount else 0)
    consistency -= 15 if any(s.code == "km_non_garanti" for s in risks) else 0
    consistency = max(0, min(100, consistency))

    repairs = repair_budget(signals) + sum(
        d.cout_eur // 3 for d in defects if d.severite >= 4
    )

    if any(s.weight >= 0.85 for s in risks):
        verdict = "avoid"
    elif delta_pct > 12 and not any(s.weight >= 0.6 for s in risks):
        verdict = "grab"
    else:
        verdict = "check"

    summary_bits = []
    if valuation and valuation.get("comps_count"):
        sense = "sous" if delta_pct > 0 else "au-dessus du"
        summary_bits.append(
            f"Prix {abs(delta_pct):.0f}% {sense} marche sur {valuation['comps_count']} comparables."
        )
    if risks:
        summary_bits.append(
            "Le texte signale: " + ", ".join(s.label.lower() for s in risks[:3]) + "."
        )
    else:
        summary_bits.append("Aucun signal negatif dans le texte de l'annonce.")
    if defects:
        summary_bits.append(f"Point de vigilance moteur: {defects[0].titre}.")
    summary_bits.append("Analyse automatique sans lecture des photos.")

    return ExpertReport(
        summary=" ".join(summary_bits),
        condition_score=condition,
        consistency_score=consistency,
        photo_findings=[],
        red_flags=red_flags,
        strengths=[s.label for s in positives],
        known_issues_to_check=[f"{d.titre}: {d.verifier}" for d in defects],
        questions_to_seller=_questions(listing, defects, risks),
        negotiation_levers=[
            f"{flag.label} (~{flag.estimated_cost_eur} EUR)"
            for flag in red_flags
            if flag.estimated_cost_eur
        ],
        estimated_repairs_eur=repairs,
        verdict=verdict,
        confidence=45 if not listing.description else 60,
    )


def _explain(exc: Exception) -> str:
    """Turn an SDK exception into a sentence that says what to do about it."""
    try:
        import anthropic
    except ImportError:  # pragma: no cover - dependency present in prod
        return str(exc)

    message = str(exc)
    if "Could not resolve authentication" in message or "api_key" in message.lower():
        return "aucune cle API configuree: analyse par regles uniquement"
    if isinstance(exc, anthropic.AuthenticationError):
        return "cle API refusee: verifier ANTHROPIC_API_KEY"
    if isinstance(exc, anthropic.NotFoundError):
        return f"modele introuvable ({exc}): verifier CAREXPERT_MODEL"
    if isinstance(exc, anthropic.RateLimitError):
        return "limite de debit atteinte apres plusieurs tentatives: reduire --deep ou reessayer"
    if isinstance(exc, anthropic.BadRequestError):
        return f"requete refusee: {exc}"
    if isinstance(exc, anthropic.APIStatusError):
        return f"erreur API HTTP {exc.status_code}"
    if isinstance(exc, anthropic.APIConnectionError):
        return "connexion a l'API impossible: verifier le reseau"
    return f"{type(exc).__name__}: {exc}"


def _severity(weight: float) -> str:
    for threshold, label in SEVERITY_BY_WEIGHT:
        if weight >= threshold:
            return label
    return "info"


def _questions(listing: ListingData, defects: list[Defect], risks: list[Signal]) -> list[str]:
    questions = [
        "Le carnet d'entretien et les factures sont-ils disponibles a la consultation ?",
        "Quel est le resultat du dernier controle technique, et de quand date-t-il ?",
    ]
    if listing.km and listing.km > 120_000:
        questions.append("La distribution a-t-elle ete remplacee, et a quel kilometrage ?")
    if listing.seller_type.value == "pro":
        questions.append("Quelle garantie est incluse, et que couvre-t-elle exactement ?")
    else:
        questions.append("Depuis combien de temps possedez-vous le vehicule, et pourquoi le vendre ?")
    for defect in defects[:2]:
        questions.append(f"{defect.titre}: {defect.verifier}")
    for signal in risks[:2]:
        questions.append(f"Pouvez-vous preciser le point suivant: {signal.label.lower()} ?")
    return questions[:8]
