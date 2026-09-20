# Sources, collecte et cadre

## Le principe de collecte

Toutes les requetes passent par `sources/fetcher.py`, qui applique :

- le `robots.txt` du site, relu et respecte par defaut ;
- une requete toutes les 2,5 secondes par domaine, jamais en parallele ;
- un cache disque : relancer un scan ne re-sollicite pas le site ;
- un recul exponentiel sur 429 et 503, en respectant `Retry-After`.

Ces reglages sont volontairement lents. Ils se modifient par source dans le
YAML, mais les augmenter engage votre responsabilite.

## L'etat mesure des sources

Mesure du 20 septembre 2026, sur les sites reels, depuis une machine
ordinaire. Ce tableau se refait avec `carexpert diagnose`, et il se perime :
un drapeau de validation dit ce qui marchait le jour ou on l'a pose.

| Source | Verdict | Mesure |
|---|---|---|
| `autoscout24` | **ouverte** | 804 Ko, 20 annonces completes par page, lues en `schema.org` |
| `leparking` | **ouverte** | 476 Ko, 26 annonces reconnues, prix et kilometrage a finir |
| `leboncoin` | fermee | HTTP 403, interstitiel DataDome ; captcha au navigateur reel |
| `lacentrale` | fermee | meme protection |
| `mobile_de`, `coches_net` | non testees | gabarits ecrits d'apres la structure publique |

Deux choses valent d'etre retenues de ces mesures.

**AutoScout24 se lit sans ouvrir une seule annonce.** Sa page de resultats
publie ses vingt annonces completes en JSON-LD : une requete pour vingt
voitures, prix, kilometrage, annee et energie compris. C'est vingt fois
moins de sollicitation que la voie par liens, et c'est la source la plus
fournie du dispositif (150 000 annonces en France, 2,7 millions en Europe).

**leboncoin et La Centrale disent non, et ce non tient.** Mesure faite deux
fois sur chacune : une requete simple recoit un 403 avec interstitiel
DataDome, et un vrai Chromium sans tete recoit un captcha. Ce n'est pas un
probleme technique a resoudre, c'est un refus. La voie qui reste sur ces
deux sites est celle des pages que vous ouvrez vous-meme, plus bas.

## Lire le robots.txt comme la norme le dit

`sources/robots.py` existe parce que `urllib.robotparser` se trompe dans
les deux sens, et parce que le respect du `robots.txt` est la premiere
regle de ce projet.

AutoScout24 ecrit `Disallow: /lst?` pour viser l'ancienne recherche a
parametres. La bibliotheque standard fait passer chaque motif par
`urlunparse(urlparse(path))`, ce qui supprime une query vide : la regle
devient `/lst`, prefixe qui interdit alors `/lst/volkswagen/golf`, page que
le site autorise. Cette seule ligne a fait declarer la plus grosse source
du projet interdite pendant une journee.

Le meme lecteur ne bloque pas assez dans l'autre sens : `Disallow: */util/*`
est compare par `startswith`, or aucune URL ne commence par `*`.

Le lecteur du projet applique RFC 9309 : jokers `*` et `$`, motif compare
au chemin **et** a la query sans les alterer, regle la plus longue
gagnante, `Allow` l'emportant a egalite, `Disallow:` vide qui autorise tout
sans etre une regle de longueur zero.

### Ce qu'un robots.txt dit des robots d'IA

AutoScout24 nomme `GPTBot`, `ClaudeBot`, `CCBot` et `Google-Extended` pour
leur interdire tout le site, tout en autorisant le groupe general. Un robot
nomme lit son groupe et ignore le general, meme plus permissif : le lecteur
le fait, et un test le verrouille.

CarExpert n'est aucun de ces robots et releve du groupe general, qui
l'autorise. L'intention du site merite cependant d'etre connue avant
d'augmenter le rythme ou le volume, et a plus forte raison avant d'en tirer
un produit commercial.

## Valider une source en une commande

```bash
carexpert diagnose --source autoscout24 --url "<URL de recherche>"
```

La commande enchaine, au rythme poli du site :

1. lecture du `robots.txt` et verification que l'URL est autorisee (si elle ne
   l'est pas, elle s'arrete la, sans rien telecharger) ;
2. recuperation de la page de recherche : statut, taille, temps de reponse ;
3. comptage des liens d'annonce reconnus par le motif configure. Si le compte
   est nul, elle deduit la forme des URL d'annonce de la page elle-meme et
   propose un motif de remplacement ;
4. detection d'un rendu JavaScript (marqueurs Next, Nuxt, Remix, Angular) ;
5. ouverture de quelques annonces et verification champ par champ de ce qui
   sort : prix, kilometrage, annee, marque, photos.

Elle se termine par un verdict et la liste des corrections a apporter. C'est
le premier reflexe avant d'ajouter ou de reactiver une source.

## Forme des URL d'annonce

Le motif de lien d'une source sert a deux choses : reconnaitre une annonce
dans une page de resultats, et en tirer son identifiant. Les deux comptent.

AutoScout24, par exemple, publie ses annonces sous la forme

```
/offres/<slug>-cat_ma<marque>mo<modele>-<uuid>
```

L'identifiant de l'annonce est l'UUID final. Le segment `cat_ma73mo2079`
contient l'identifiant du *modele*, partage par tous les vehicules du meme
modele : le confondre avec celui de l'annonce ferait s'ecraser toutes les
Volvo V70 sur une seule ligne en base, sans aucun message d'erreur.

Les liens sont aussi normalises avant d'etre suivis : une page de resultats
pointe la meme voiture depuis sa vignette, son titre et sa photo, avec des
parametres de suivi differents a chaque fois. Sans normalisation, le
robot ouvre trois fois la meme annonce.

## Sites rendus en JavaScript

`lacentrale`, `leboncoin` et `coches.net` rendent leurs resultats cote
client. On en deduisait qu'il fallait un navigateur. C'est faux, et la
nuance vaut la peine d'etre comprise.

Une application React ne va pas chercher sa premiere page de resultats apres
l'affichage : ce serait un aller-retour de plus et la place perdue dans les
moteurs de recherche. Elle **serialise la reponse du serveur dans le HTML**
et la donne au navigateur pour son hydratation. Les annonces sont donc dans
la premiere reponse HTTP, sous un nom connu :

| Forme | Ou | Qui |
|---|---|---|
| `<script id="__NEXT_DATA__">` | routeur pages Next.js | La Centrale |
| `self.__next_f.push([1, "..."])` | routeur applicatif Next.js | leboncoin |
| `window.__NUXT__` | Nuxt | |
| `__INITIAL_STATE__`, `__APOLLO_STATE__`, `__remixContext` | le reste | |

`sources/embedded.py` lit ces formes. Il ne suit aucun chemin fige :
`props.pageProps.searchData.ads[0]` serait aussi fragile qu'un selecteur CSS
et casserait au premier deploiement. Il parcourt l'arbre et retient les
objets qui **ressemblent** a une annonce : une identite, un prix, et un
kilometrage ou une annee. Le site peut deplacer son etat, renommer ses
routes, changer la forme de ses props ; une annonce ressemble toujours a une
annonce.

### Les trois paliers de collecte

Du moins cher au plus cher, et dans cet ordre :

1. **schema.org** (`structured.py`) : le balisage que le site publie pour les
   moteurs. Une requete, zero JavaScript.
2. **Etat embarque** (`embedded.py`) : le JSON que la page hydrate. Meme
   cout : une requete.
3. **Rendu navigateur** (`browser.py`) : un vrai Chromium. Trente a soixante
   sous-requetes, quelques secondes de calcul pour le site comme pour nous.

Le troisieme palier ne demarre que si les deux premiers reviennent sans
annonces (`render.escalate`, vrai par defaut). Sur une source qui n'en a pas
besoin, le navigateur ne demarre jamais. `requires_js: true` dans un YAML
**autorise** le rendu, il ne l'impose pas.

```bash
pip install -e ".[browser]" && python -m playwright install chromium
carexpert diagnose -s leboncoin --url "<URL collee>"   # dit par quel palier ca sort
carexpert scan -s leboncoin --url "<URL collee>" --browser
```

`carexpert diagnose` affiche la ligne « lues via », et signale un rendu
navigateur quand il a eu lieu : une source qui bascule silencieusement en
rendu coute trente fois plus cher, cela doit se voir.

### Ce que le rendu ne fait pas

Il rend une page avec un vrai navigateur, au meme rythme poli que le reste,
`robots.txt` respecte. C'est tout.

Il ne resout pas de captcha, ne forge ni ne rejoue de jeton de protection, ne
fait pas tourner d'adresses, et ne se deguise pas en navigateur de quelqu'un
d'autre. Quand un site repond par une page de defi, `detect_challenge` la
reconnait et la collecte **s'arrete**, avec une raison lisible. Un site qui
nous defie a dit non ; la reponse a un non, c'est un accord de donnees, pas
un contournement.

L'agent utilisateur suit la meme regle : la chaine de Chromium, plus le
jeton du projet pour que le site puisse identifier et joindre qui le lit.
`CAREXPERT_USER_AGENT` permet de le changer, et ce qu'on y met engage celui
qui l'y met.

### Quand un site refuse : lire ce que vous regardez deja

Mesure sur leboncoin, deux fois : HTTP 403 avec interstitiel DataDome sur une
requete simple, captcha sur un vrai Chromium sans tete. La Centrale repond
pareil. C'est un non, et il ne se discute pas.

Le debat anti-bot passe pourtant a cote d'une distinction. Un collecteur
demande au site des pages que personne n'a demandees. Une personne qui
cherche une Twingo **regarde deja la page** : le site la lui a servie,
volontairement, comme a un visiteur. La lire n'est pas de la collecte, rien
n'est contourne, et le site n'a rien eu a decider puisque c'est un humain qui
a navigue.

```bash
# Vous cherchez sur le site, normalement. Puis Fichier > Enregistrer sous.
carexpert import ~/Downloads/leboncoin-twingo.html
carexpert import ~/Downloads/annonces/          # un dossier entier
```

Au quotidien, l'extension Chrome (`extension/`) fait la meme chose sans
enregistrer de fichier : elle publie la page affichee sur `POST /api/capture`
et repose un verdict par annonce sur les cartes. Verifie dans un vrai
Chromium, sur une page servie sous l'origine du site avec sa politique de
securite active : 6 annonces, 6 pastilles. Voir `extension/README.md`.

`sources/captured.py` lit ces pages avec les memes paliers qu'un scan, et
n'a aucun code reseau : sans page ouverte par vous, il n'y a rien a lire.
C'est exactement ce qui laisse cette voie ouverte quand les autres sont
fermees. La source se deduit du lien canonique que la page a garde.

Ce que cela ne donne pas : la veille automatique. Une page capturee est un
instantane. Pour le suivi quotidien, les alertes natives du site previennent,
et `carexpert analyse-url` ou `carexpert import` expertise ce qu'elles
remontent.

### Ce qui existe ailleurs, et pourquoi ce n'est pas ici

Des services vendent l'acces aux annonces leboncoin (Scrapfly, acteurs
Apify, et les bibliotheques qui vont avec). Techniquement, ils fonctionnent
en imitant l'empreinte TLS de Chrome (JA3/JA4), en faisant tourner des
adresses residentielles francaises et en traitant les captchas. C'est le
contournement, sous-traite : le fait de le payer ne change pas sa nature.
Leurs propres auteurs notent qu'on y passe plus de temps a maintenir le
contournement qu'a exploiter les donnees.

Il n'existe pas d'API publique leboncoin, et ce n'est pas un oubli : leur
modele repose sur les annonces premium et les abonnements professionnels.
Les API partenaires existantes servent a **publier** des annonces
(Ubiflow, AllYouCanPost), pas a lire le marche.

### Les voies qui restent, dans l'ordre

1. **Flux officiel ou partenariat.** La seule voie propre a l'echelle. La
   plupart de ces sites ont une offre professionnelle.
2. **Agregateur.** `leparking` republie les annonces de leboncoin, La
   Centrale, ParuVendu et des sites de concessions, avec un lien vers la
   source. Une requete chez lui couvre plusieurs sites. En contrepartie son
   prix date du dernier passage de son robot : reperer chez lui, conclure
   chez la source.
3. **Alertes natives du site**, puis `carexpert analyse-url` annonce par
   annonce.

## Le cadre a connaitre

Ce depot ne donne pas de conseil juridique, mais ces points structurent le
produit :

- **Conditions d'utilisation.** La plupart des sites d'annonces interdisent
  l'extraction automatisee dans leurs CGU. Une veille personnelle a faible
  volume et une collecte industrielle revendue ne se traitent pas pareil.
- **Droit des bases de donnees** (directive 96/9/CE). Extraire une partie
  substantielle d'une base protegee est interdit, meme si chaque donnee
  isolee est publique.
- **Donnees personnelles** (RGPD). Un numero de telephone, un nom de vendeur
  particulier, une plaque d'immatriculation sont des donnees personnelles.
  CarExpert ne collecte pas les coordonnees des vendeurs : ce n'est pas un
  oubli. Garder cette limite.
- **Protections anti-bot.** Ne pas les contourner. Un site qui bloque a
  signifie son refus, et le contournement change la nature juridique de
  l'acte.

La ligne de conduite du projet : collecter peu, lentement, ce qui est
publiquement affiche, pour un usage d'aide a la decision, et passer a un
accord commercial des que l'usage devient serieux.

## Ajouter une source

Deposer un YAML dans `sources/sites/`. Aucun code Python n'est necessaire.
Voir `sources/sites/README.md` pour les champs disponibles.
