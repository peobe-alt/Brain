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
