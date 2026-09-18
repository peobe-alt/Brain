# Suite

## Ce qui est fait

- collecte polie, adaptateurs de sites en YAML, extraction `schema.org`
- normalisation multilingue (FR, DE, EN, IT, ES) des modeles et energies
- detection de signaux dans le texte, avec gestion des negations
- estimation par comparables, avec confiance et refit des courbes
- expertise Claude sur photos et texte, sortie structuree
- base de faiblesses connues par motorisation
- score explique, veilles, alertes (console, webhook, Telegram, email)
- tableau de bord web et API JSON
- diagnostic de source en une commande (`carexpert diagnose`)
- couche experte resistante aux pannes, avec cout mesure par scan
- 94 tests, dont la mesure de separation affaires / pieges

## Prochaines etapes, par ordre de valeur

1. **Valider deux sources reelles de bout en bout.** Prendre AutoScout24 et
   mobile.de, lancer `carexpert diagnose` sur une recherche reelle, corriger
   ce qu'elle signale, mesurer le taux d'extraction sur 200 annonces, puis
   passer `verified: true`. C'est le prealable a tout le reste : sans donnees
   reelles en base, il n'y a pas de comparables, donc pas d'estimation.
2. **Historiser les prix sur trois mois.** La baisse de prix est le meilleur
   signal d'un vendeur pret a negocier, et c'est une donnee que personne
   n'affiche.
3. **Refit automatique des courbes de decote** par segment, une fois quelques
   milliers d'annonces collectees. Le code existe deja
   (`valuation.adjust.fit_depreciation`), il reste a le brancher sur un job.
4. **Arbitrage transfrontalier.** Les facteurs pays sont deja dans le modele ;
   il manque le calcul complet du cout d'import (transport, malus, carte
   grise, TVA) pour transformer un ecart DE/FR en gain net reel.
5. **Reconnaissance de doublons par photo** (hachage perceptuel) : le meme
   vehicule reposte sous une autre annonce, ou revendu par un marchand apres
   rachat a un particulier.
6. **Application mobile.** L'API JSON est deja la ; une alerte utile se lit
   sur un telephone, pas dans un terminal.
7. **Historique VIN** quand un fournisseur est branche : c'est la donnee qui
   manque le plus a une expertise sur photos.

## Modele economique possible

- gratuit : une veille, analyse par regles ;
- payant : veilles illimitees, expertise Claude sur photos, alertes
  instantanees, arbitrage europeen ;
- pro (marchands) : detection des vehicules sous-cotes a racheter, avec
  estimation de marge apres remise en etat.

Le cout marginal d'une expertise profonde est de quelques centimes ; la
valeur d'une bonne affaire detectee se compte en centaines d'euros. Le
rapport est favorable, a condition de garder la passe large gratuite.
