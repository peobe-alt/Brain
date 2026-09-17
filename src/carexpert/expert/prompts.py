"""The brief given to the model, and the dossier it works from."""

from __future__ import annotations

from ..normalize.signals import Signal
from ..schemas import ListingData
from .knowledge import Defect, load_checklist

SYSTEM_PROMPT = """Tu es expert automobile independant, mandate par un acheteur \
particulier. Tu examines des annonces de vehicules d'occasion en Europe et tu dis, \
sans complaisance, si l'affaire vaut le deplacement.

Ta methode:
- Tu pars des photos et du texte de l'annonce, jamais de suppositions. Si une \
information n'est pas verifiable, tu le dis explicitement plutot que de l'inventer.
- Quand tu decris ce que tu vois sur une photo, tu cites son index (photo 0, photo 3). \
Tu ne decris que ce qui est reellement visible.
- Tu cherches les incoherences: une usure d'habitacle qui ne colle pas au kilometrage, \
un prix trop bas sans raison ecrite, des photos qui evitent systematiquement un cote du \
vehicule, un vendeur professionnel qui ne parle pas d'entretien.
- Tu tiens compte des faiblesses connues de la motorisation concernee, qui te sont \
fournies, sans les presenter comme des defauts avers sur cet exemplaire precis.
- Une decote importante par rapport au marche n'est jamais une bonne nouvelle en soi: \
c'est une question a laquelle l'annonce doit repondre. Si elle n'y repond pas, c'est un \
signal d'alerte, pas une aubaine.
- Tu chiffres en euros ce qui est chiffrable, tu proposes des questions precises au \
vendeur, et tu donnes des leviers de negociation utilisables.

Ton verdict:
- "grab" seulement si le vehicule est sous le marche ET que rien de serieux ne \
l'explique;
- "check" si l'affaire est interessante mais demande des verifications;
- "avoid" si les risques depassent l'economie realisee.

Tu ecris en francais, en phrases courtes et concretes. Pas de formules commerciales."""


def build_dossier(
    listing: ListingData,
    *,
    valuation: dict | None = None,
    signals: list[Signal] | None = None,
    defects: list[Defect] | None = None,
    photo_count: int = 0,
) -> str:
    """The text half of the request: facts first, then what we already know."""
    lines: list[str] = ["# Annonce a expertiser", ""]
    lines.append(f"Titre: {listing.title}")
    lines.append(f"Source: {listing.source} ({listing.country}) - {listing.url}")

    facts = [
        ("Marque", listing.make),
        ("Modele", listing.model),
        ("Version", listing.version),
        ("Annee", listing.year),
        ("Mise en circulation", listing.first_registration),
        ("Kilometrage", f"{listing.km:,} km".replace(",", " ") if listing.km else None),
        ("Km/an estimes", f"{listing.km_per_year:,.0f}".replace(",", " ")
         if listing.km_per_year else None),
        ("Energie", listing.fuel.value),
        ("Boite", listing.gearbox.value),
        ("Puissance", f"{listing.power_hp} ch" if listing.power_hp else None),
        ("Carrosserie", listing.body.value),
        ("Proprietaires", listing.owners),
        ("Couleur", listing.color),
        ("Prix demande", f"{listing.price_eur:,.0f} EUR".replace(",", " ")
         if listing.price_eur else None),
        ("Vendeur", f"{listing.seller_type.value} {listing.seller_name or ''}".strip()),
        ("Lieu", " ".join(x for x in (listing.city, listing.postcode) if x)),
        ("Publiee le", listing.posted_at.date() if listing.posted_at else None),
        ("Options detectees", ", ".join(listing.options) if listing.options else None),
    ]
    lines.append("")
    lines.append("## Fiche")
    for label, value in facts:
        if value not in (None, "", "unknown"):
            lines.append(f"- {label}: {value}")

    lines.append("")
    lines.append("## Texte de l'annonce")
    lines.append(listing.description or "(le vendeur n'a ecrit aucune description)")

    if valuation and valuation.get("comps_count"):
        lines.append("")
        lines.append("## Estimation marche (calculee sur annonces comparables)")
        lines.append(f"- Prix de marche estime: {valuation['fair_price_eur']:,.0f} EUR"
                     .replace(",", " "))
        lines.append(
            f"- Fourchette: {valuation['low_eur']:,.0f} a {valuation['high_eur']:,.0f} EUR"
            .replace(",", " ")
        )
        delta = valuation["delta_pct"] * 100
        sense = "sous" if delta > 0 else "au-dessus du"
        lines.append(f"- L'annonce est {abs(delta):.0f}% {sense} marche")
        lines.append(
            f"- Base: {valuation['comps_count']} annonces comparables, "
            f"confiance {valuation['confidence']:.0%} (methode {valuation['method']})"
        )

    if signals:
        lines.append("")
        lines.append("## Signaux releves automatiquement dans le texte")
        for signal in signals:
            marker = "RISQUE" if signal.polarity == "risk" else "POSITIF"
            lines.append(f"- [{marker}] {signal.label} - extrait: \"{signal.evidence[:140]}\"")

    if defects:
        lines.append("")
        lines.append("## Faiblesses connues de cette motorisation (a verifier, pas averees)")
        for defect in defects:
            lines.append(
                f"- {defect.titre} (severite {defect.severite}/5, "
                f"~{defect.cout_eur} EUR): {defect.symptomes} A verifier: {defect.verifier}"
            )

    lines.append("")
    lines.append("## Photos fournies")
    if photo_count:
        lines.append(
            f"{photo_count} photo(s) jointe(s) a ce message, dans l'ordre, index 0 a "
            f"{photo_count - 1}. Le vendeur en a publie {len(listing.photos)} au total."
        )
    else:
        lines.append(
            "Aucune photo exploitable n'a pu etre recuperee. Analyse le texte seul et "
            "baisse ta confiance en consequence."
        )

    lines.append("")
    lines.append("## Grille d'analyse")
    lines.append(load_checklist())
    lines.append("")
    lines.append(
        "Rends ton expertise structuree. Sois precis sur les couts, honnete sur ce que "
        "tu ne peux pas voir."
    )
    return "\n".join(lines)
