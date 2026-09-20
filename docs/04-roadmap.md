# Suite

## Ce qui est fait

- collecte polie, adaptateurs de sites en YAML, extraction sans selecteurs CSS
- lecture du `robots.txt` selon RFC 9309 (`sources/robots.py`), jokers et
  query compris : celui de la bibliotheque standard bloquait `/lst/...` sur
  une regle `/lst?`, et laissait passer ce que `*/util/*` protege
- trois paliers d'extraction : `schema.org`, etat embarque des applications
  React (`sources/embedded.py`), rendu Chromium en dernier recours
- lecture des pages que l'utilisateur ouvre lui-meme (`sources/captured.py`,
  `carexpert import`) et compagnon Chrome (`extension/`) pour les sites qui
  refusent la collecte
- normalisation multilingue (FR, DE, EN, IT, ES) des modeles et energies
- detection de signaux dans le texte, avec gestion des negations
- estimation par comparables, avec confiance plafonnee par la taille de
  l'echantillon : premier verdict a huit comparables
- expertise Claude sur photos et texte, sortie structuree, cout mesure
- base de faiblesses connues par motorisation
- score explique, veilles, alertes (console, webhook, Telegram, email)
- tableau de bord web et API JSON
- diagnostic de source en une commande, qui montre ce qu'il a vu plutot que
  de le decrire : familles d'URL de la page, forme de recherche que le site
  declare, motif de lien deduit
- 292 tests, tous hors ligne, dont la mesure de separation affaires / pieges

## L'etat des sources, au 20 septembre 2026

| Source | Verdict | Mesure |
|---|---|---|
| `autoscout24` | ouverte | 20 annonces completes par page, en `schema.org` |
| `leparking` | ouverte | 26 annonces reconnues, prix et kilometrage a finir |
| `leboncoin` | fermee | DataDome : 403 en requete, captcha au navigateur |
| `lacentrale` | fermee | meme protection |

Voir `docs/02-sources-et-legal.md`. Les sites fermes restent lisibles par
`carexpert import` et l'extension, sur les pages que vous ouvrez.

## Prochaines etapes, par ordre de valeur

1. **Remplir la base depuis AutoScout24.** La source est ouverte et se lit
   sans ouvrir une seule annonce. Sans donnees reelles en base, il n'y a pas
   de comparables, donc pas de verdict : c'est le prealable a tout le reste.
2. **Finir leparking.** Ses pages de detail ne publient ni `schema.org` ni
   etat embarque reconnu, et sa page de resultats n'a pas encore livre prix
   ni kilometrage. Un agregateur qui republie leboncoin et La Centrale avec
   un lien vers la source vaut l'effort.
3. **Mesurer la prime professionnelle.** `SELLER_FACTOR` vaut 1,07 par
   decision, pas par mesure, et le commentaire le dit. Avec un vivier reel
   elle se calcule : memes modeles, medianes pro et particulier a age et
   kilometrage egaux. Elle pese lourd sur AutoScout24, dont 73 % des
   annonces sont professionnelles.
4. **Annoter la liste en place dans l'extension** plutot qu'au chargement :
   une observation des changements du DOM couvrirait le defilement infini
   et les listes rendues en retard.
5. **Historiser les prix sur trois mois.** La baisse de prix est le meilleur
   signal d'un vendeur pret a negocier, et personne ne l'affiche.
6. **Refit automatique des courbes de decote** par segment, une fois
   quelques milliers d'annonces collectees. Le code existe deja
   (`valuation.adjust.fit_depreciation`), il reste a le brancher.
7. **Arbitrage transfrontalier.** Les facteurs pays sont dans le modele ; il
   manque le cout d'import complet (transport, malus, carte grise, TVA).
8. **Reconnaissance de doublons par photo** (hachage perceptuel).
9. **Application mobile.** L'API JSON est la ; une alerte utile se lit sur un
   telephone, pas dans un terminal.
10. **Historique VIN** quand un fournisseur est branche.

## Modele economique possible

- gratuit : une veille, analyse par regles ;
- payant : veilles illimitees, expertise Claude sur photos, alertes
  instantanees, arbitrage europeen ;
- pro (marchands) : detection des vehicules sous-cotes a racheter, avec
  estimation de marge apres remise en etat.

Le cout marginal d'une expertise profonde est de quelques centimes ; la
valeur d'une bonne affaire detectee se compte en centaines d'euros. Le
rapport est favorable, a condition de garder la passe large gratuite.
