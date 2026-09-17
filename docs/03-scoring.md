# Comment une annonce est notee

## En une phrase

Le score repond a : *est-ce que ca vaut le coup de traverser la region pour
aller voir cette voiture ce soir ?*

## Le prix de marche

Pour chaque annonce, on cherche des comparables en base, par paliers de plus
en plus larges :

| Palier | Criteres | Confiance |
|---|---|---|
| `strict` | meme modele, meme energie, meme boite, ±1 an, ±30% km, meme pays | 1.00 |
| `modele_carburant` | meme modele, meme energie, ±2 ans, ±50% km | 0.90 |
| `modele` | meme modele, ±3 ans | 0.75 |
| `modele_europe` | idem, tous pays | 0.60 |
| `modele_large` | ±5 ans, tous pays | 0.40 |

On s'arrete au premier palier qui atteint huit comparables.

Chaque comparable est ensuite **ramene aux conditions du vehicule cible** :
age, kilometrage, boite, options, type de vendeur, pays. Les courbes sont
multiplicatives et explicites dans `valuation/adjust.py` ; elles peuvent etre
reajustees sur vos propres donnees via `fit_depreciation`.

Le prix juste est la mediane des 80% centraux des prix ajustes. Au-dela de
douze comparables, une regression log-lineaire est melangee a parts egales :
elle extrapole mieux aux extremes de l'echantillon.

La **confiance** combine le nombre de comparables, leur dispersion, la
distance d'extrapolation et le palier utilise. Elle pondere ensuite le poids
du prix dans le score : une estimation fragile ne doit pas emporter la
decision.

## Le score

Depart a 50 points, puis :

| Critere | Amplitude | Logique |
|---|---|---|
| Position prix | -40 a +45, pondere par la confiance | l'ecart au marche, mais seulement si l'estimation tient |
| Risques identifies | jusqu'a -45 | un defaut grave peut annuler n'importe quelle remise |
| Etat apparent | -15 a +10 | ce que montrent les photos |
| Coherence | -15 a +7 | km, usure, prix et texte racontent-ils la meme histoire |
| Remise en etat | jusqu'a -28 | le cout des reparations, rapporte au prix demande |
| Avis de l'expert | -20 a +8 | le verdict de synthese |
| Signaux pratiques | -4 a +5 | baisse de prix, nombre de photos, km/an, type de vendeur |

Chaque point est accompagne de sa justification, affichee dans l'interface et
dans `carexpert show`.

## Pourquoi le prix seul ne suffit pas

Mesure sur le marche de demonstration, ou la verite est connue :

| Categorie | Ecart au prix de marche | Score median |
|---|---|---|
| Vraies affaires | -21% | **84** |
| Prix de marche | -2% | 51 |
| Pieges | **-37%** | **30** |

Les pieges sont *les moins chers du marche*. Un tri par prix les placerait en
tete. Apres analyse du texte et de l'etat, ils tombent a 30 et disparaissent
du top 25, qui ne contient plus que des affaires reelles.

C'est toute la raison d'etre de l'outil.

## Limites assumees

- L'estimation vaut ce que vaut la base : peu d'annonces collectees sur un
  modele, peu de fiabilite. Le champ `confiance` le dit, il faut le lire.
- Les courbes de decote sont des moyennes europeennes. Un modele a forte
  cote locale (4x4 en montagne, cabriolet sur la cote) sera mal estime tant
  que la base n'est pas assez fournie pour que les comparables locaux
  prennent le dessus.
- L'expertise se fait sur photos. Elle ne remplace ni l'essai, ni le controle
  technique, ni un passage sur un pont.
