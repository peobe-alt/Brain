# Comment une annonce est notee

## En une phrase

Le score repond a : *est-ce que ca vaut le coup de traverser la region pour
aller voir cette voiture ce soir ?*

## Le prix de marche

Pour chaque annonce, on cherche des comparables en base, par paliers de plus
en plus larges :

| Palier | Criteres | Il en faut | Confiance |
|---|---|---|---|
| `strict` | meme modele, meme energie, meme boite, ±1 an, ±30% km, meme pays | 3 | 1.00 |
| `modele_carburant` | meme modele, meme energie, ±2 ans, ±50% km | 4 | 0.90 |
| `modele` | meme modele, ±3 ans | 6 | 0.75 |
| `modele_europe` | idem, tous pays | 8 | 0.60 |
| `modele_large` | ±5 ans, tous pays | 12 | 0.40 |

On s'arrete au premier palier qui **atteint son propre seuil**. Plus le
palier est large, plus il faut d'annonces pour qu'il dise quelque chose :
trois Golf du meme millesime, meme energie et meme boite sont un marche ;
trois Golf "toutes energies, cinq ans d'ecart, kilometrage du simple au
double" sont trois voitures differentes.

Si aucun palier n'atteint son seuil, **il n'y a pas d'estimation** : pas de
prix de marche, pas d'ecart, pas de gain, et le verdict est `unknown`
(A ESTIMER). La reponse affichee dit alors ce qui manque, en nombres :
"3 annonces comparables en base, il en faut 12".

Mesure qui a impose cette regle : lue sur une page de resultats base vide,
une Golf 7 1.6 TDI de 255 000 km affichee 5 900 EUR ressortait "A SAISIR,
45% sous le marche, +4 749 EUR de gain". Ses trois comparables : une e-Golf
electrique, un break TDI et une GTE hybride rechargeable. Six voitures qui
se comparent entre elles ne sont pas un marche.

**Les familles d'energie ne se melangent jamais.** Un palier large accepte de
comparer une essence et un diesel ; il ne compare jamais une electrique, une
hybride rechargeable et une thermique. Batterie, autonomie, aides a l'achat,
marche de l'occasion : rien n'est comparable, et aucun facteur de decote ne
rattrape l'ecart de niveau de prix. Mesure : une e-Golf de 163 000 km a
8 490 EUR sortait "8% au-dessus du marche, A FUIR" sur un echantillon de
Golf 1.6 TDI.

Chaque comparable est ensuite **ramene aux conditions du vehicule cible** :
age, kilometrage, boite, options, type de vendeur, pays. Les courbes sont
multiplicatives et explicites dans `valuation/adjust.py` ; elles peuvent etre
reajustees sur vos propres donnees via `fit_depreciation`.

Avant tout calcul, les comparables sont dedupliques : une meme voiture
publiee sur trois sites ne compte qu'une fois, au prix le plus bas, celui
auquel on peut reellement l'acheter. Sans cela l'estimation derive vers le
haut de facon systematique, parce que les marchands crosspostent beaucoup
plus que les particuliers et affichent des prix plus eleves : toutes les
annonces paraitraient alors meilleures qu'elles ne sont.

L'identite d'un vehicule est construite sur le kilometrage exact, jamais
arrondi. Une tolerance parait plus sure et ne l'est pas : sur un marche de
deux cents modeles identiques les kilometrages sont denses, et toute marge
fusionne des voitures qui se ressemblent seulement. Perdre un vrai
comparable coute plus cher que manquer un doublon.

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
