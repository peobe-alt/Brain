# Architecture

## Le probleme, pose correctement

Trouver une bonne affaire dans des milliers d'annonces demande trois choses
que les filtres des sites ne font pas :

1. **Savoir ce que vaut la voiture.** Un filtre "moins de 15 000 EUR" ne dit
   pas si 14 900 EUR est cher ou donne pour ce modele, cette annee, ce
   kilometrage.
2. **Lire l'annonce en entier.** L'information decisive est dans la phrase
   "vendu en l'etat", dans une photo ou l'aile ne reflete pas la lumiere comme
   la portiere, dans un habitacle trop use pour le kilometrage annonce.
3. **Faire les deux sur tout le marche, tous les jours.** Une bonne affaire
   entre particuliers part en quelques heures.

CarExpert repond a ces trois points dans cet ordre.

## Le flux

```
   sources                normalisation           estimation
 ┌────────────┐          ┌──────────────┐       ┌──────────────┐
 │ AutoScout24│          │ marque/modele│       │ comparables  │
 │ La Centrale│  ──────> │ energie/boite│ ────> │ + courbes de │
 │ mobile.de  │  schema. │ options      │       │   decote     │
 │ demo       │  org     │ signaux texte│       │ = prix juste │
 └────────────┘          └──────────────┘       └──────┬───────┘
                                                        │
                        ┌───────────────────────────────┘
                        v
                ┌──────────────┐        ┌──────────────┐      ┌─────────┐
                │ passe large  │  top N │ expertise    │      │ score   │
                │ regles seules│ ─────> │ Claude       │ ───> │ + alerte│
                │ (gratuite)   │        │ photos+texte │      │         │
                └──────────────┘        └──────────────┘      └─────────┘
```

## Les couches

| Module | Role | Pourquoi c'est la |
|---|---|---|
| `sources/` | collecte | Un adaptateur par site, decrit en YAML. L'extraction lit le `schema.org` publie pour le referencement : bien plus stable que des selecteurs CSS. |
| `normalize/` | vocabulaire commun | `Serie 3`, `3er`, `3 Series` doivent devenir la meme chose, sinon les comparables sont faux. |
| `normalize/signals.py` | lecture du texte | Transforme la prose en signaux typés, avec gestion des negations (`jamais accidente`). |
| `valuation/` | prix de marche | Comparables choisis par paliers, ramenes aux conditions du vehicule cible, mediane robuste. |
| `expert/` | expertise | Claude lit photos et texte avec une sortie structuree. Une base de faiblesses connues par motorisation oriente son regard. |
| `scoring/` | decision | Un score 0-100 additif, ou chaque point est justifie. |
| `alerts/` | notification | Veilles permanentes, une alerte par annonce et par veille. |
| `api/` + `web/` | interface | Rendu serveur, sans etape de build. Les endpoints JSON permettent de brancher un vrai front plus tard. |

## Deux passes, pour une raison de cout

Analyser mille annonces avec un modele multimodal coute cher. La chaine est
donc separee en deux :

- **passe large** : tout est collecte, normalise, estime et note avec les
  regles internes. Local, instantane, gratuit.
- **passe profonde** : seules les meilleures candidates partent chez Claude
  avec leurs photos, puis sont renotees.

`--deep 10` sur un scan de 800 annonces, c'est dix appels modele, pas huit
cents. Et une analyse profonde n'est jamais ecrasee par une passe large
ulterieure.

## Choix techniques

- **SQLite par defaut**, Postgres en changeant une variable d'environnement.
  Rien a installer pour commencer.
- **Pas de numpy** : la regression de decote est resolue par elimination de
  Gauss sur un systeme 3x3, une trentaine de lignes.
- **Pillow optionnel** : sans lui, les photos partent non redimensionnees.
- **Sortie structuree** cote modele : le rapport d'expertise est un objet
  valide, jamais du texte a parser.
