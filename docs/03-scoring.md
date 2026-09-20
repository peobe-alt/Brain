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

Seules les dimensions connues **des deux cotes** participent a cet
ajustement. Ce qu'une annonce ne dit pas ne joue pas en sa faveur : une
valeur absente valait auparavant le coefficient d'une voiture neuve a zero
kilometre, ce qui remettait chaque comparable "a l'etat neuf" pour rejoindre
une cible dont on ne savait rien. Mesure sur un scan reel de 399 Twingo :
une Twingo a 1 800 EUR estimee 31 465 EUR, premiere du classement. Et sans
age **ni** kilometrage, il n'y a plus rien pour situer la voiture : la
reponse est A ESTIMER, pas une cote.

Avant tout calcul, les comparables sont dedupliques : une meme voiture
publiee sur trois sites ne compte qu'une fois, au prix le plus bas, celui
auquel on peut reellement l'acheter. Sans cela l'estimation derive vers le
haut de facon systematique, parce que les marchands crosspostent beaucoup
plus que les particuliers et affichent des prix plus eleves : toutes les
annonces paraitraient alors meilleures qu'elles ne sont.

**Une annonce reelle ne se compare qu'a des annonces reelles**, et
reciproquement. Le marche synthetique (`sources/demo.py`) mesure la qualite
du classement, il ne fixe aucun prix. Mesure avant cette separation : une
Golf reelle a 15 990 EUR estimee a 6 935 EUR face a dix comparables
inventes, avec 0,58 de confiance et un verdict "A FUIR". La confiance ne
protege pas d'elle-meme : de fausses annonces sont parfaitement coherentes
entre elles.

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

Elle ne depasse jamais ce que la taille de l'echantillon autorise a elle
seule, et cette limite vient d'un defaut mesure. **L'accord entre deux
comparables ne prouve rien : deux points s'alignent toujours.** Or l'accord
et la distance d'extrapolation pesaient 55 % de la somme contre 45 % a la
taille de l'echantillon, assez pour qu'un seul comparable atteigne 0,44,
au-dessus du seuil de verdict. Mesure sur une page de six Twingo consultee
dans le navigateur : "A SAISIR, 12,4 % sous le marche", rendu sur deux
annonces de la meme page. Le plafond ne touche pas un marche fourni, ou la
confiance mesuree va de 0,48 a 0,70 (p5-p90) ; il ne se declenche que quand
la base est trop mince, ce qui est la regle sur un modele rare.

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

## Le quatrieme verdict

Un verdict exige de quoi le tenir. Sans aucun comparable, ou avec une
confiance sous `min_confidence_for_verdict` (0,35 par defaut), la reponse
n'est pas **A FUIR** mais **A ESTIMER** : ne pas savoir situer un prix n'est
pas une raison de fuir une voiture saine.

Les questions sont posees dans cet ordre, et la premiere qui repond tranche :

1. l'expertise a-t-elle rendu un avis defavorable ? **A FUIR**, quel que soit
   le prix - un defaut grave ne se rachete pas par une remise ;
2. la confiance tient-elle ? sinon **A ESTIMER** ;
3. score au moins 75 ? **A SAISIR** ;
4. score au moins 55 ? **A VERIFIER** ;
5. sinon **A FUIR**.

Le plafond par taille d'echantillon place le premier verdict possible entre 5
et 11 comparables selon le palier atteint. Et comme la recherche s'arrete au
premier palier qui en reunit huit, un echantillon plus mince que cela vient
forcement d'un palier large : il reste **A ESTIMER**.

Enfin, **un chiffre ne s'affiche que quand on le tient**. Une pastille grise
**A ESTIMER** suivie de "12,4 % sous le marche" se lit comme une bonne
affaire : le chiffre l'emporte sur le mot, et il vient justement de
l'echantillon qu'on vient de juger trop maigre. Le refus de juger se dit, il
ne se contredit pas a la ligne suivante.

## Pourquoi le prix seul ne suffit pas

Mesure sur le marche de demonstration, ou la verite est connue :

| Categorie | n | Ecart au prix de marche | Score median |
|---|---|---|---|
| Vraies affaires | 20 | -21 % | **84 / 100** |
| Annonces au prix | 215 | +2 % | 56 / 100 |
| Pieges | 25 | **-39 %** | **31 / 100** |

Les pieges sont *les moins chers du marche*. Un tri par prix les placerait en
tete. Apres analyse du texte et de l'etat, ils tombent a 31 et disparaissent
du classement : le top 20 ne contient pas un seul piege, mais seize vraies
affaires et quatre annonces au prix.

C'est toute la raison d'etre de l'outil.

## Limites assumees

- L'estimation vaut ce que vaut la base : peu d'annonces collectees sur un
  modele, peu de fiabilite. L'outil ne s'en remet pas au lecteur pour le
  voir - sous le seuil, il repond **A ESTIMER** et n'affiche aucun ecart.
- Les courbes de decote sont des moyennes europeennes. Un modele a forte
  cote locale (4x4 en montagne, cabriolet sur la cote) sera mal estime tant
  que la base n'est pas assez fournie pour que les comparables locaux
  prennent le dessus.
- L'expertise se fait sur photos. Elle ne remplace ni l'essai, ni le controle
  technique, ni un passage sur un pont.
