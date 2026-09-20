# CarExpert

**Un expert automobile qui lit les annonces a votre place.**

Le probleme n'est pas de trouver des annonces : il y en a des milliers. Le
probleme est que les filtres d'un site savent trier par prix, pas dire si un
prix est bon. Une Golf a 13 500 EUR est une affaire ou une arnaque selon
l'annee, le kilometrage, la boite, l'entretien, et selon ce que le vendeur a
ecrit en petit au milieu de sa description.

CarExpert scanne les annonces, estime ce que chaque voiture vaut reellement
d'apres le marche, lit le texte et regarde les photos comme le ferait un
expert, et ne vous remonte que ce qui merite un deplacement.

---

## Le point essentiel, en chiffres

Mesure sur un marche de test dont la verite est connue :

| | Ecart au prix de marche | Score CarExpert |
|---|---|---|
| Vraies affaires | -21 % | **84 / 100** |
| Annonces au prix | -2 % | 51 / 100 |
| **Pieges** (moteur fatigue, compteur douteux, vendu en l'etat) | **-37 %** | **30 / 100** |

Les pieges sont les annonces **les moins cheres du marche**. Un tri par prix
les met en premiere page. Apres lecture du texte et de l'etat, ils tombent a
30 et sortent du top 25, qui ne contient plus que de vraies affaires.

C'est exactement ce que le produit doit faire, et c'est verifie par un test.

---

## Essayer en une commande

Aucune cle API, aucun reseau requis : un marche synthetique est genere
localement pour voir toute la chaine fonctionner.

```bash
pip install -e .
carexpert demo
```

```
250 annonces vues, 250 nouvelles, 0 ignorees
0 collectees, 250 estimees, 0 expertisees en profondeur

  id  score  avis       vehicule                                prix    marche   ecart
  27     92  A SAISIR   Volkswagen Golf Confortline 2021       9 140   12 901  +3 761
  51     90  A SAISIR   Renault Captur Zen hybride 2020        7 600    9 137  +1 537
  49     89  A SAISIR   Mercedes Classe C Break diesel 2021   13 250   17 704  +4 454
```

Puis le detail d'une annonce, avec le raisonnement complet :

```bash
carexpert show 27
carexpert serve        # tableau de bord sur http://127.0.0.1:8000
```

---

## Sur de vraies annonces

**Commencer par la : verifier qu'une source fonctionne vraiment.**

```bash
carexpert diagnose --source autoscout24 --url "<URL de recherche collee>"
```

En une trentaine de secondes, et quelques requetes seulement, la commande
repond : le robots.txt autorise-t-il cette URL, le site repond-il, les
annonces sont-elles dans le HTML ou rendues en JavaScript, le motif de lien
est-il bon (sinon elle en propose un, deduit de la page), et les champs
essentiels sortent-ils correctement sur un echantillon d'annonces. Elle
termine par la liste de ce qu'il faut corriger.

```
+------------- PARTIELLEMENT EXPLOITABLE -------------+
| 2/3 annonces exploitables                           |
+-----------------------------------------------------+
 robots.txt          present
 autorise            oui
 delai impose        1.0 s
 motif de lien       /offres/[^?#]+
 annonces detectees  3
 motif suggere       /annonce/[^/]+-?\d{4,}.html

A faire maintenant
  > Champs manquants sur l'echantillon: price_eur (1), km (1), year (1).
  > Ajouter des selecteurs CSS de secours dans sites/autoscout24.yaml.
```

Une fois la source validee :

```bash
cp .env.example .env          # y mettre ANTHROPIC_API_KEY pour l'expertise photo
```

La methode la plus fiable : construire la recherche dans l'interface du site,
avec ses filtres, copier l'URL, et la donner a CarExpert.

```bash
carexpert scan --source autoscout24 --url "<URL de recherche collee>" --deep 5
```

Ou par criteres :

```bash
carexpert scan --source autoscout24 --make Peugeot --model 308 \
               --price-max 15000 --km-max 120000 --year-min 2018 --deep 5
```

Mettre une recherche sous surveillance :

```bash
carexpert watch add golf-gtd --make Volkswagen --model Golf \
                   --price-max 18000 --min-score 78 --channel telegram
carexpert watch run
```

Expertiser une annonce precise, tout de suite :

```bash
carexpert analyse-url "https://www.autoscout24.fr/offres/..."
```

---

## Sans rien demander au site : l'extension navigateur

Trois sites sur cinq affichent leurs annonces en JavaScript, et le mieux
protege est celui ou les particuliers vendent. L'extension contourne le
probleme par l'autre bout : elle lit **la page que vous avez deja ouverte**.
Le JavaScript a tourne, la session est la votre, et le site ne recoit pas une
requete de plus.

Le verdict s'affiche alors sur ses annonces a lui :

```
  83  A SAISIR    24% sous le marche, estime a 11 729 EUR   +2 779 EUR
  63  A VERIFIER  9% sous le marche, estime a 6 471 EUR       +571 EUR
```

Installation sans terminal : onglet **Extension** du tableau de bord, bouton
*Telecharger*, puis `chrome://extensions` -> Mode developpeur -> *Charger
l'extension non empaquetee*.

**Tant que la base est vide, elle ne dit rien.** C'est le point important.
Sur la premiere page lue, les seules annonces connues sont celles de cette
page : six voitures qui se comparent entre elles ne sont pas un marche, donc
aucun prix n'est affiche.

```
  ?   A ESTIMER   prix non situe: 3 annonces comparables en base sur 12
```

Chaque page lue nourrit la base, et les estimations s'ouvrent au fur et a
mesure. Le detail est dans [`docs/05-extension.md`](docs/05-extension.md).

---

## Ce que l'outil regarde vraiment

**Le prix.** Des comparables sont cherches en base par paliers (meme modele,
meme energie, meme boite, annee et kilometrage proches, meme pays), puis
chacun est **ramene aux conditions du vehicule analyse** : age, kilometrage,
boite, options, type de vendeur, pays. Le prix juste est la mediane robuste
des prix ainsi ajustes, assortie d'une confiance qui pondere son poids dans
la note.

Chaque palier porte son propre seuil : trois annonces suffisent quand elles
sont strictement comparables, il en faut douze au palier le plus large. En
dessous, il n'y a pas d'estimation du tout - ni prix, ni ecart, ni gain -
et l'outil affiche ce qui lui manque. Et les familles d'energie ne se
melangent jamais : une electrique ne se compare pas a une thermique, quel
que soit le nombre d'annonces disponibles.

**Le texte.** Une couche de signaux typés lit ce que le vendeur dit, et ce
qu'il evite de dire : `vendu en l'etat`, `compteur non garanti`,
`distribution a faire`, mais aussi `carnet d'entretien complet`,
`premiere main`. Les negations sont gerees : `jamais accidente` n'est pas
`accidente` (ce detail, seul, evite des milliers de faux positifs).

**Les photos.** Claude examine les photos et le dossier complet : jeux de
carrosserie, teintes de peinture differentes, usure de l'habitacle par
rapport au kilometrage annonce, pneus depareilles, voyants au tableau de
bord, angles systematiquement evites.

**Le modele precis.** Une base de faiblesses connues par motorisation oriente
l'analyse : courroie humide PureTech, chaine N47, DSG 7 a sec, consommation
d'huile du 1.2 TCe, sante de batterie sur electrique. Ce sont des points a
verifier, presentes comme tels.

**La coherence de l'ensemble.** Une decote de 30 % sans raison ecrite n'est
pas une aubaine : c'est une question sans reponse, et elle est traitee comme
un signal d'alerte.

Chaque point du score est justifie, ligne par ligne :

```
  +31.6  Position prix: 21% sous le marche (16 comparables, confiance 85%)
   +8.0  Avis de l'expert: grab
   +7.5  Etat apparent: note d'etat 90/100
   -11.1  Remise en etat: 1 300 EUR a prevoir, soit 12% du prix demande
```

---

## Deux passes, pour que ca reste payable

Envoyer mille annonces a un modele multimodal coute cher. La chaine est donc
coupee en deux : **tout** est collecte, normalise, estime et note par les
regles internes (local, instantane, gratuit), puis **seules les meilleures
candidates** partent chez Claude avec leurs photos pour une expertise reelle,
avant d'etre renotees.

`--deep 10` sur un scan de 800 annonces, c'est dix appels modele.

### Ce que ca coute

Une expertise approfondie, c'est le dossier de l'annonce plus une dizaine de
photos redimensionnees : de l'ordre de **0,10 a 0,15 EUR** par vehicule au
tarif Opus 5. Dix par jour, tous les jours, tiennent dans une quarantaine
d'euros par mois. A comparer aux quelques centaines d'euros que represente
une seule bonne affaire detectee, ou evitee.

L'ordre de grandeur ci-dessus est calcule, pas mesure : l'outil compte les
tokens reellement consommes et affiche le montant a la fin de chaque scan,
donc le premier vrai scan donnera votre chiffre.

```
12 annonces collectees, 340 estimees, 10 expertisees en profondeur, cout 1.34 EUR
```

Sans cle API, l'outil fonctionne quand meme : l'expertise se fait alors sur
les regles seules, et le dit.

### Quand l'appel echoue

Un scan approfondi passe sur une dizaine de vehicules d'affilee. Une limite
de debit, un refus, une reponse tronquee ou une photo qui n'en est pas une ne
doivent pas couter le lot entier : chaque echec retombe sur l'analyse par
regles, avec la raison affichee en clair, et le scan continue.

```
repli sur les regles  Volkswagen Golf 1.6 TDI: limite de debit atteinte apres
                      plusieurs tentatives: reduire --deep ou reessayer
```

---

## Sources

| Source | Pays | Etat |
|---|---|---|
| `demo` | - | marche synthetique, hors ligne, pour tests et demonstration |
| `autoscout24` | DE FR IT ES NL BE AT | gabarit a valider, extraction `schema.org` |
| `mobile_de` | DE AT | gabarit a valider |
| `lacentrale` | FR | rendu JavaScript, passer par `--url` ou un flux officiel |
| `leboncoin` | FR | protection forte, usage manuel recommande |
| `coches_net` | ES | rendu JavaScript |

Les trois sites rendus en JavaScript, et leboncoin, sont lisibles par
l'extension navigateur : c'est le navigateur qui affiche la page, CarExpert
se contente de la lire.

Ajouter un site = deposer un fichier YAML dans `src/carexpert/sources/sites/`.
Aucun code Python.

L'extraction ne repose pas sur des selecteurs CSS fragiles mais sur le
balisage `schema.org/Car` que les sites publient pour le referencement.

**A lire avant toute collecte reelle : [`docs/02-sources-et-legal.md`](docs/02-sources-et-legal.md).**
Le `robots.txt` est respecte par defaut, une requete toutes les 2,5 secondes
par domaine, rien en parallele, et les protections anti-bot ne se contournent
pas.

---

## Documentation

- [Architecture](docs/01-architecture.md) : le flux, les couches, les choix techniques
- [Sources et cadre legal](docs/02-sources-et-legal.md) : politique de collecte, CGU, RGPD
- [Le scoring](docs/03-scoring.md) : estimation, ponderation, limites
- [L'extension navigateur](docs/05-extension.md) : lire la page ouverte,
  et ne rien affirmer que la base ne porte pas
- [Suite](docs/04-roadmap.md) : prochaines etapes et modele economique

---

## Developpement

```bash
pip install -e ".[dev,photos]"
pytest                      # 222 tests
carexpert sources           # sources disponibles
carexpert diagnose -s autoscout24 --url "..."   # valider une source
```

Structure :

```
src/carexpert/
  sources/     collecte (adaptateurs YAML, extraction schema.org, fetcher poli)
  normalize/   vocabulaire commun + lecture des signaux du texte
  valuation/   comparables, courbes de decote, estimation
  expert/      Claude (photos + texte) et base de faiblesses connues
  scoring/     score explique
  alerts/      veilles et notifications
  api/ web/    tableau de bord et API JSON
  extension/   extension navigateur (MV3)
```

---

## Ce que l'outil ne fait pas

- Il ne remplace ni un essai, ni un controle technique, ni un passage sur un
  pont. Une expertise sur photos reste une expertise sur photos.
- Il ne collecte pas les coordonnees des vendeurs. C'est volontaire.
- Il n'a pas acces a l'historique VIN : c'est la donnee qui lui manque le
  plus, et elle s'achete.
- Les gabarits d'URL de recherche des sites reels ne sont pas encore valides
  en conditions reelles. Le chemin `--url` fonctionne, lui, immediatement, et
  `carexpert diagnose` dit en une commande ce qu'il reste a corriger.
