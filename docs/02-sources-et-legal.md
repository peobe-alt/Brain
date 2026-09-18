# Sources, collecte et cadre

## Le principe de collecte

Toutes les requetes passent par `sources/fetcher.py`, qui applique :

- le `robots.txt` du site, relu et respecte par defaut ;
- une requete toutes les 2,5 secondes par domaine, jamais en parallele ;
- un cache disque : relancer un scan ne re-sollicite pas le site ;
- un recul exponentiel sur 429 et 503, en respectant `Retry-After`.

Ces reglages sont volontairement lents. Ils se modifient par source dans le
YAML, mais les augmenter engage votre responsabilite.

## Ce qui est dans le depot, et ce qui reste a valider

Les fichiers `sources/sites/*.yaml` portent tous `verified: false`. Les
gabarits d'URL de recherche y sont ecrits d'apres la structure publique
connue de chaque site, **mais n'ont pas ete testes en conditions reelles**.
Ils sont un point de depart, pas une garantie.

En revanche, l'extraction d'une page d'annonce ne depend pas de ces
gabarits : elle lit le balisage `schema.org/Car` que les sites publient
eux-memes pour le referencement. C'est la partie robuste.

**La methode fiable au quotidien :** construire la recherche dans
l'interface du site, avec ses filtres, copier l'URL, puis

```bash
carexpert scan --source autoscout24 --url "<URL collee>" --deep 5
```

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

## Recherches qui ne quittent jamais le navigateur

Certains sites, TheParking par exemple, rangent toute leur recherche derriere
un `#` :

```
https://www.theparking.eu/#!/used-cars/V70.html?id_energie=1&id_motorisation=12
```

Cette URL ne demande pas ce qu'elle a l'air de demander. Un fragment (tout ce
qui suit le `#`) n'est **jamais** transmis au serveur : c'est la norme
(RFC 3986, section 3.5), pas une protection du site. Le modele, l'energie et
la motorisation restent dans le navigateur, et la requete recue est
simplement `https://www.theparking.eu/`, la page d'accueil.

Consequence : toute conclusion tiree de la reponse porte sur une page que
personne n'a demandee. Le diagnostic le dit maintenant en toutes lettres
(verdict `RECHERCHE RESTEE DANS LE NAVIGATEUR`), affiche l'URL reellement
envoyee et rend les filtres perdus, decodes. Il ne va pas plus loin : ouvrir
les annonces de la page d'accueil couterait des requetes pour un echantillon
hors sujet.

La sortie, quand elle existe, est une URL servie par le serveur. Le
diagnostic ne se contente pas de le dire, il rend deux pistes qu'il a deja
payees :

- **les URL que la page se donne a elle-meme** (`rel=canonical`,
  `rel=alternate`). Une page ne peut pas declarer une canonique qu'elle ne
  sert pas sans casser son propre referencement : ces URL existent donc cote
  serveur ;
- **les sitemaps annonces par le `robots.txt`**, deja telecharge pour la
  verification de politesse. Un sitemap est, par definition, l'inventaire
  des URL que le site veut voir indexees, donc rendues par le serveur. Sur
  un site dont la recherche est cliente, c'est le meilleur point d'entree.

Aucune requete supplementaire n'est emise pour les obtenir. Reste ensuite a
ouvrir une annonce depuis la recherche et copier **son** URL. Sans cela, la
source n'est pas collectable en HTTP simple, et c'est le meme arbitrage que
pour un site rendu en JavaScript (section suivante), qui recoit d'ailleurs
les memes pistes.

Une exception voulue : quand le `robots.txt` **interdit** l'URL, aucune piste
n'est affichee. Un refus n'est pas une impasse technique a contourner.

Le meme garde-fou existe cote collecte : `carexpert scan --url "<URL a
fragment>"` retire le fragment, previent dans les logs et dit quelle URL
part vraiment, au lieu de parcourir la page d'accueil et d'annoncer
"0 annonce".

## Notre reseau, ou le site ?

Un proxy d'entreprise, un VPN ou un bac a sable de CI repond 403 ou 407 au
CONNECT, et le site n'est jamais joint. Le diagnostic distingue ce cas
(`SORTIE RESEAU BLOQUEE`) d'un refus venu du site, pour deux raisons :

- **la conclusion est inverse.** Un site qui bloque a signifie son refus et
  on s'arrete. Une sortie reseau bloquee ne dit rien du site : desactiver la
  source sur cette base reviendrait a abandonner une source jamais testee ;
- **la reprise est inutile.** Un refus de politique n'est pas une panne
  passagere. La boucle de reprise y repassait quatre fois, sur quinze
  secondes, pour une reponse qui ne changera jamais. Elle ne le fait plus.

Dans ce cas le rapport n'affiche que ce qu'il a pu observer. Pas de
"robots.txt absent, autorise oui" : ces lignes seraient des valeurs par
defaut presentees comme des mesures.

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
client : une simple requete HTTP ne renvoie pas les annonces. Trois options,
par ordre de preference :

1. **Flux officiel ou partenariat.** C'est la seule voie propre a l'echelle.
   La plupart de ces sites ont une offre professionnelle.
2. **Alertes natives du site**, puis expertise annonce par annonce avec
   `carexpert analyse-url`.
3. **Rendu headless** (Playwright) pour un usage personnel et a faible
   volume. Non inclus ici, volontairement.

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
