# L'extension navigateur

## Le probleme qu'elle resout

Trois des cinq sites de la liste rendent leurs annonces en JavaScript, et le
mieux protege d'entre eux - leboncoin - est justement celui ou les
particuliers vendent. Un collecteur y a le choix entre echouer et contourner
une protection que ce projet a decide de ne pas contourner.

Une page que le navigateur a deja affichee n'a aucun de ces deux problemes :
le JavaScript a tourne, la session est celle de l'utilisateur, et la lire ne
coute rien au site puisque la requete a deja eu lieu, faite par un humain,
pour lui-meme.

L'extension ne demande donc **jamais rien au site**. Elle lit la page
ouverte, l'envoie a CarExpert qui tourne sur la machine, et dessine le
verdict par-dessus.

## Ce qu'elle affiche

Sur une page de resultats, un bandeau au-dessus de chaque annonce : la note,
la position par rapport au marche, le gain estime. Sur une page d'annonce,
une fiche en bas a droite : ce qui fait la note, les alertes lues dans le
texte, les questions a poser au vendeur, et un bouton d'expertise
approfondie qui envoie les photos a Claude - sur clic, parce que c'est le
seul geste qui coute de l'argent.

## Ce qu'elle n'affiche pas

**Aucun prix tant que la base ne le porte pas.** C'est la regle la plus
importante de l'extension, et elle vient d'un defaut mesure.

Sur la toute premiere page lue, la base est vide : les seules annonces
connues sont les six de la page ouverte. Une premiere version affichait
quand meme un prix de marche - median de trois voitures qui n'avaient rien
en commun - et donc un ecart, un gain, et un verdict. Une Golf 7 1.6 TDI de
255 000 km a 5 900 EUR ressortait "A SAISIR, 45% sous le marche, +4 749 EUR
de gain", face a une e-Golf electrique, un break TDI et une GTE hybride.

Un bandeau qui dit "A ESTIMER" et affiche "45% sous le marche" dans la meme
ligne est lu comme un prix de marche : c'est le chiffre qu'on retient, pas
l'etiquette. Desormais, quand l'estimation n'existe pas :

```
  ?   A ESTIMER   prix non situe: 3 annonces comparables en base sur 12
```

et la fiche dit ce qu'il manque et comment le combler : chaque page de
resultats lue nourrit la base. Sur une recherche Golf, la page suivante
suffit souvent :

```
  83  A SAISIR    24% sous le marche, estime a 11 729 EUR   +2 779 EUR
  63  A VERIFIER  9% sous le marche, estime a 6 471 EUR       +571 EUR
```

La meme Golf de 255 000 km, une fois comparee a de vraies Golf de 230 000 a
260 000 km, ressort a "9% sous le marche" : une affaire ordinaire, pas une
aubaine.

Le chiffre du score disparait lui aussi tant qu'il n'y a pas de position
prix : un score sans marche n'est pas une note d'affaire, seulement la
lecture du texte et de l'etat.

## Installation

Le tableau de bord la sert : onglet **Extension**, bouton *Telecharger*, ou
directement le dossier `src/carexpert/extension/` dont la page affiche le
chemin. Puis `chrome://extensions` -> Mode developpeur -> *Charger
l'extension non empaquetee*. Firefox 121 et plus :
`about:debugging#/runtime/this-firefox`.

## Ce qui sort de la machine

Rien, sauf sur clic d'expertise approfondie, qui envoie l'annonce et ses
photos a Claude. La page lue, les prix, les notes restent sur 127.0.0.1.

## Le serveur local n'est pas ouvert a tout le monde

Un serveur qui ecoute sur 127.0.0.1 est joignable par **n'importe quelle**
page ouverte dans le navigateur : un site peut poster vers localhost depuis
son propre JavaScript, et la requete part meme si la reponse lui est
illisible. Sans filtre, une page hostile remplirait la base de faux
comparables - et une annonce reelle ne se compare qu'a des annonces reelles.

Les requetes qui portent une origine ne sont donc acceptees que depuis les
domaines declares dans `sources/sites/*.yaml`, depuis une extension de
navigateur, ou depuis le tableau de bord lui-meme. La comparaison se fait
sur le domaine complet, jamais sur "contient" : `autoscout24.pirate.example`
n'est pas AutoScout24.

## Ajouter un site

Deposer le YAML dans `src/carexpert/sources/sites/`, puis ajouter ses
domaines aux `matches` et aux `host_permissions` du manifest. Un test refuse
tout site connu de CarExpert qui ne serait suivi par l'extension, et tout
motif que le navigateur rejetterait - un seul motif invalide fait refuser
l'extension entiere, pas seulement ce site.
