# Comment une annonce est notee

## En une phrase

Le score repond a : *est-ce que ca vaut le coup de traverser la region pour
aller voir cette voiture ce soir ?*

## Le prix de marche

Pour chaque annonce, on cherche des comparables en base, par paliers de plus
en plus larges :

| Palier | Criteres | Confiance |
|---|---|---|
| `strict` | meme finition, meme carrosserie, puissance ±12%, meme energie, meme boite, ±1 an, ±30% km, meme pays | 1.00 |
| `finition` | meme finition, meme carrosserie, puissance ±20%, meme energie, ±2 ans, ±50% km, meme pays | 0.92 |
| `motorisation` | meme carrosserie, puissance ±25%, meme energie, ±3 ans, ±60% km, meme pays | 0.85 |
| `motorisation_eu` | idem, ±70% km, tous pays | 0.72 |
| `modele` | meme modele, puissance ±40%, ±3 ans, tous pays | 0.55 |
| `modele_large` | meme modele, puissance ±55%, ±5 ans, tous pays | 0.40 |

On s'arrete au premier palier qui atteint huit comparables.

**L'ordre dans lequel on elargit compte autant que les largeurs.** La
geographie est elargie tot, parce que `country_factor` ramene deja un prix
etranger sur le marche local : un comparable allemand est un vrai
comparable, simplement converti. La motorisation, la carrosserie et la
finition sont tenues le plus longtemps possible, parce que rien ne ramene
une voiture qui n'est tout simplement pas la meme voiture. Un Scenic 110 et
un Scenic 160 partagent un badge et pas grand-chose d'autre ; un break n'est
pas une berline a aucun prix.

Un champ vide n'est pas un desaccord : une annonce qui ne declare ni
puissance ni finition reste un comparable. Beaucoup de sites n'en publient
pas, et les ecarter couterait plus de vrais comparables que la rare finition
mal appariee.

Chaque comparable est ensuite **ramene aux conditions du vehicule cible** :
age, kilometrage, motorisation, boite, options, type de vendeur, pays. La
puissance est corrigee par paire et non par le facteur commun, parce qu'elle
n'a de sens que si les deux annonces la declarent : la plier dans
`vehicle_factor` ferait deriver toutes les estimations des sources qui
l'omettent. Les courbes sont
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
| Vraies affaires | -26% | **86** |
| Prix de marche | +3% | 57 |
| Pieges | **-36%** | **22** |

Les pieges sont *les moins chers du marche*. Un tri par prix les placerait en
tete. Apres analyse du texte et de l'etat, ils tombent a 22 et disparaissent
du top 25, ou il n'en reste aucun.

C'est toute la raison d'etre de l'outil.

## Ce que font les badges des sites, et ce qu'ils ne font pas

Leboncoin, AutoScout24 et La Centrale affichent un indicateur de prix sur
chaque annonce. Celui de leboncoin est documente par le site lui-meme, dans
ses articles d'aide "L'indicateur prix", version acheteur et version vendeur :

- il compare le prix de l'annonce a celui d'annonces similaires recemment
  publiees sur leboncoin ;
- il repose uniquement sur des donnees techniques declarees par le vendeur :
  marque, modele, annee de mise en circulation, kilometrage, motorisation ;
- cinq niveaux existent, de "nettement inferieur au marche" a "nettement
  plus eleve". Pour l'acheteur, seuls les trois favorables sont mis en
  avant : Tres bonne affaire, Bonne affaire, Prix equitable ;
- l'historique d'entretien "ne peut pas etre evalue automatiquement", et le
  site conseille au vendeur de le mentionner dans la description. La
  description n'est donc pas lue, les photos non plus ;
- quand la voiture est "trop specifique" ou dans un "etat atypique",
  reparations a prevoir ou non roulant, il n'y a pas d'indicateur ;
- pour le niveau le plus bas, le site demande au vendeur d'ecrire "les
  particularites qui justifient ce prix". Il sait qu'un prix tres bas
  appelle une justification, mais il ne verifie pas qu'elle existe.

Ce qui n'est pas ecrit mais s'en deduit : la reference est faite de prix
demandes, pas de prix de transaction, sur un seul site ou les particuliers
dominent. Rien n'indique que la finition, les options ou le type de vendeur
entrent dans le calcul. Et le badge est symetrique par construction : moins
cher egale meilleur. C'est exactement ce que le tableau ci-dessus met en
defaut, puisque les pieges sont les annonces les moins cheres.

### Ce que cela change pour le score

Le badge fait la partie du score qui coute le moins cher a reproduire, la
position prix. Tout ce que le site declare ne pas faire est le reste du
score : risques lus dans le texte, etat sur photos, coherence entre
kilometrage et usure, budget de remise en etat, faiblesses connues de la
motorisation, questions au vendeur. Quatre consequences :

1. **Le badge du site est une entree, pas un concurrent.** Quand une annonce
   nous arrive avec son badge, la position prix est deja calculee sur une
   base bien plus large que la notre. Elle sert de prior a l'estimation quand
   il n'y a pas de comparables, avec une confiance etiquetee comme telle, et
   le verdict n'est plus `unknown` faute de references.
2. **Un badge tres favorable sans justification ecrite est un signal
   d'alerte**, pas une aubaine. L'analyse par regles le fait deja sur la
   decote inexpliquee ; brancher ce signal sur le badge le rend disponible
   meme sans estimation propre.
3. **L'absence de badge est une information.** Voiture rare ou voiture
   abimee : la lecture de la description tranche, et le site ne la fait pas.
4. **Ne pas chercher a recalculer la position prix mieux que le site sur ses
   propres annonces.** Il a la base, nous ne l'aurons pas. Notre estimation
   sert la ou il n'y a pas de badge, pour comparer entre sites, et pour
   ancrer sur une cote officielle.

Sources : [l'indicateur prix, version acheteur](https://assistance.leboncoin.info/hc/fr/articles/31578868268818-L-indicateur-prix-votre-rep%C3%A8re-pour-situer-le-tarif-d-un-v%C3%A9hicule-par-rapport-au-march%C3%A9)
et [version vendeur](https://assistance.leboncoin.info/hc/fr/articles/360009615560-L-indicateur-prix-voiture-votre-rep%C3%A8re-pour-ajuster-le-tarif-de-votre-v%C3%A9hicule-et-le-situer-par-rapport-au-march%C3%A9).

## Limites assumees

- L'estimation vaut ce que vaut la base : peu d'annonces collectees sur un
  modele, peu de fiabilite. Le champ `confiance` le dit, il faut le lire.
- Les courbes de decote sont des moyennes europeennes. Un modele a forte
  cote locale (4x4 en montagne, cabriolet sur la cote) sera mal estime tant
  que la base n'est pas assez fournie pour que les comparables locaux
  prennent le dessus.
- L'expertise se fait sur photos. Elle ne remplace ni l'essai, ni le controle
  technique, ni un passage sur un pont.
