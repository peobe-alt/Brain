"""Red flags and reassurance signals hidden in advert text.

A huge share of the real information in a used-car advert is in the wording,
not in the structured fields: `vendu en l'etat`, `distribution faite`,
`compteur non garanti`. This module turns that prose into typed signals with
a severity weight and, where it makes sense, a typical repair cost used later
as a negotiation lever.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from .text import strip_accents

Polarity = Literal["risk", "positive"]


@dataclass(slots=True)
class Signal:
    code: str
    label: str
    polarity: Polarity
    weight: float               # 0..1, how strongly it moves the risk score
    category: str
    evidence: str = ""
    typical_cost_eur: int = 0
    keywords: tuple[str, ...] = field(default_factory=tuple, repr=False)

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "label": self.label,
            "polarity": self.polarity,
            "weight": self.weight,
            "category": self.category,
            "evidence": self.evidence,
            "typical_cost_eur": self.typical_cost_eur,
        }


def _s(code, label, polarity, weight, category, keywords, cost=0) -> Signal:
    return Signal(code, label, polarity, weight, category, keywords=keywords, typical_cost_eur=cost)


RISK_SIGNALS: list[Signal] = [
    _s("vendu_en_letat", "Vendu en l'etat, sans garantie", "risk", 0.9, "commercial",
       ("vendu en letat", "en letat", "vendu dans letat", "verkauft wie gesehen",
        "sold as seen", "as-is", " as is ", "senza garanzia")),
    _s("pour_pieces", "Annonce pour pieces ou export", "risk", 1.0, "mecanique",
       ("pour pieces", "pieces detachees", "ersatzteilspender", "bastlerfahrzeug",
        "for parts", "export uniquement", "nur fur export")),
    _s("moteur_hs", "Moteur annonce en panne", "risk", 1.0, "mecanique",
       ("moteur hs", "moteur a refaire", "moteur casse", "motorschaden", "engine failure",
        "ne demarre pas", "ne roule pas", "moteur bloque"), 4000),
    _s("boite_hs", "Boite de vitesses en panne", "risk", 0.95, "mecanique",
       ("boite hs", "boite a refaire", "getriebeschaden", "gearbox failure",
        "boite qui craque"), 2500),
    _s("accidente", "Vehicule accidente ou sinistre", "risk", 0.85, "historique",
       ("accidente", "vehicule accident", "unfallwagen", "unfallschaden", "sinistre",
        "vei grave", "vge", "vei", "damaged", "grele", "carrosserie a refaire"), 2000),
    _s("km_non_garanti", "Kilometrage non garanti", "risk", 0.8, "historique",
       ("km non garanti", "kilometrage non garanti", "compteur non garanti",
        "kilometerstand nicht garantiert", "mileage not guaranteed", "compteur bloque",
        "compteur hs")),
    _s("ct_ko", "Controle technique absent ou defavorable", "risk", 0.7, "administratif",
       ("sans controle technique", "ct non", "ct a faire", "ct ko", "contre visite",
        "controle technique a faire", "tuv fallig", "tuv abgelaufen", "mot failed",
        "revisione scaduta"), 400),
    _s("carte_grise_probleme", "Probleme de carte grise / gage", "risk", 0.95, "administratif",
       ("carte grise barree", "sans carte grise", "vehicule gage", "opposition",
        "kein fahrzeugbrief", "no logbook", "no v5")),
    _s("distribution_a_faire", "Distribution a prevoir", "risk", 0.5, "entretien",
       ("distribution a faire", "distribution a prevoir", "courroie a changer",
        "zahnriemen fallig", "timing belt due", "distribution non faite"), 900),
    _s("embrayage_a_faire", "Embrayage fatigue", "risk", 0.5, "mecanique",
       ("embrayage a faire", "embrayage patine", "embrayage a changer", "kupplung defekt",
        "clutch slipping"), 1200),
    _s("turbo", "Turbo defaillant", "risk", 0.7, "mecanique",
       ("turbo hs", "turbo a changer", "turbolader defekt", "turbo failure"), 1500),
    _s("injecteurs", "Injecteurs a remplacer", "risk", 0.6, "mecanique",
       ("injecteurs hs", "injecteur hs", "injecteurs a changer", "injektoren defekt"), 1200),
    _s("fap_egr", "FAP / EGR en defaut", "risk", 0.55, "mecanique",
       ("fap hs", "fap a changer", "vanne egr", "egr hs", "dpf defekt", "filtre a particules"), 1300),
    _s("consomme_huile", "Consommation d'huile anormale", "risk", 0.75, "mecanique",
       ("consomme de lhuile", "consommation dhuile", "olverbrauch", "burns oil"), 3000),
    _s("voyant_moteur", "Voyant moteur allume", "risk", 0.65, "mecanique",
       ("voyant moteur", "voyant allume", "motorkontrollleuchte", "check engine",
        "defaut moteur"), 800),
    _s("fumee", "Fumee a l'echappement", "risk", 0.7, "mecanique",
       ("fumee blanche", "fumee bleue", "fume a lechappement", "qualmt")),
    _s("joint_culasse", "Suspicion de joint de culasse", "risk", 0.95, "mecanique",
       ("joint de culasse", "zylinderkopfdichtung", "head gasket"), 2500),
    _s("rouille", "Corrosion signalee", "risk", 0.6, "carrosserie",
       ("rouille", "corrosion", "rost", "rusty", "point de rouille"), 800),
    _s("clim_hs", "Climatisation hors service", "risk", 0.3, "confort",
       ("clim hs", "clim a recharger", "climatisation ne fonctionne pas", "klima defekt"), 350),
    _s("ancien_taxi", "Usage intensif (taxi, VTC, auto-ecole, flotte)", "risk", 0.6, "historique",
       ("ancien taxi", "ex taxi", "vtc", "auto ecole", "fahrschule", "taxi", "flotte",
        "vehicule de societe intensif")),
    _s("import", "Vehicule importe", "risk", 0.35, "administratif",
       ("importe", "import allemagne", "import espagne", "vehicule import", "importfahrzeug",
        "imported", "eu import"), 500),
    _s("arnaque", "Signaux typiques d'arnaque a distance", "risk", 1.0, "fraude",
       ("western union", "paiement par mandat", "mandat cash", "je suis a letranger",
        "transporteur se charge", "paypal uniquement", "vehicule en angleterre",
        "bon de commande avant visite", "acompte avant visite", "expedition possible")),
    _s("urgence", "Vente presentee comme urgente", "risk", 0.25, "commercial",
       ("vente urgente", "urgent", "cause depart", "besoin dargent", "depart etranger",
        "premier arrive")),
    _s("plusieurs_proprios", "Historique multi-proprietaires", "risk", 0.3, "historique",
       ("4eme main", "5eme main", "plusieurs proprietaires")),
]

POSITIVE_SIGNALS: list[Signal] = [
    _s("carnet_entretien", "Carnet d'entretien / factures", "positive", 0.8, "entretien",
       ("carnet dentretien", "carnet entretien", "suivi concession", "scheckheftgepflegt",
        "scheckheft", "full service history", "factures a lappui", "toutes les factures",
        "entretien a jour", "historique complet")),
    _s("distribution_faite", "Distribution refaite", "positive", 0.7, "entretien",
       ("distribution faite", "distribution refaite", "courroie changee", "zahnriemen neu",
        "timing belt done", "kit distribution neuf")),
    _s("ct_ok", "Controle technique vierge", "positive", 0.6, "administratif",
       ("ct ok", "ct vierge", "controle technique vierge", "ct sans contre visite",
        "tuv neu", "mot until", "revisione ok")),
    _s("premiere_main", "Premiere main", "positive", 0.6, "historique",
       ("premiere main", "1ere main", "1re main", "erstbesitz", "one owner", "unico proprietario")),
    _s("garantie", "Garantie proposee", "positive", 0.5, "commercial",
       ("garantie 12 mois", "garantie 6 mois", "garantie constructeur", "garantie mecanique",
        "mit garantie", "warranty")),
    _s("non_fumeur", "Vehicule non fumeur", "positive", 0.2, "etat",
       ("non fumeur", "nichtraucher", "non smoker")),
    _s("pneus_neufs", "Pneus recents", "positive", 0.3, "entretien",
       ("pneus neufs", "pneus recents", "neue reifen", "new tyres", "4 pneus neufs")),
    _s("freins_neufs", "Freinage refait", "positive", 0.3, "entretien",
       ("freins neufs", "plaquettes neuves", "disques neufs", "bremsen neu")),
    _s("embrayage_neuf", "Embrayage neuf", "positive", 0.4, "entretien",
       ("embrayage neuf", "embrayage change", "kupplung neu")),
    _s("revision_faite", "Revision recente", "positive", 0.4, "entretien",
       ("revision faite", "vidange faite", "revision recente", "grosse revision",
        "inspektion neu")),
]

ALL_SIGNALS = RISK_SIGNALS + POSITIVE_SIGNALS

#: Negations that flip a keyword's meaning. `jamais accidente` is the single
#: most common phrase in French adverts, and reading it as `accidente` would
#: wrongly flag a large share of perfectly clean cars.
NEGATIONS = (
    "pas de", "pas d", "aucun", "aucune", "sans", "jamais", "non", "ni",
    "never", "no", "kein", "keine", "nicht", "nie", "mai", "nunca",
)


def detect_signals(*parts: str | None) -> list[Signal]:
    """Scan advert text and return every signal found, with its evidence."""
    hay = strip_accents(" ".join(p for p in parts if p).lower())
    hay = re.sub(r"['`’]", "", hay)
    hay = re.sub(r"\s+", " ", hay)
    found: list[Signal] = []
    for template in ALL_SIGNALS:
        for keyword in template.keywords:
            # Word boundaries matter: `import` must not fire on `importante`.
            match = re.search(rf"(?<![a-z0-9]){re.escape(keyword.strip())}(?![a-z0-9])", hay)
            if match is None:
                continue
            idx = match.start()
            window = hay[max(0, idx - 90) : idx + len(keyword) + 90].strip()
            if _negated(hay, idx):
                continue
            found.append(
                Signal(
                    code=template.code,
                    label=template.label,
                    polarity=template.polarity,
                    weight=template.weight,
                    category=template.category,
                    evidence=window,
                    typical_cost_eur=template.typical_cost_eur,
                )
            )
            break
    return found


def _negated(hay: str, idx: int) -> bool:
    """Is the keyword preceded by a negation, as in `jamais accidente`?"""
    prefix = hay[max(0, idx - 24) : idx].rstrip(" '-")
    words = prefix.split()
    if not words:
        return False
    # A negation counts if it sits within the two words before the keyword.
    tail = " ".join(words[-2:])
    return any(
        tail == negation or tail.endswith(" " + negation) or words[-1] == negation
        for negation in NEGATIONS
    )


def risk_from_signals(signals: list[Signal]) -> float:
    """Aggregate signals into a 0..1 risk level (positives damp the total)."""
    risk = sum(s.weight for s in signals if s.polarity == "risk")
    comfort = sum(s.weight for s in signals if s.polarity == "positive")
    raw = risk - 0.35 * comfort
    if raw <= 0:
        return 0.0
    return min(1.0, raw / 2.2)


def repair_budget(signals: list[Signal]) -> int:
    """Rough euro cost of everything the advert admits needs doing."""
    return int(sum(s.typical_cost_eur for s in signals if s.polarity == "risk"))
