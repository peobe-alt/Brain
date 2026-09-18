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
- deduplication des vehicules crosspostes, estimation incrementale
- 135 tests, dont la mesure de separation affaires / pieges

## Le constat

Le moteur est bon. La base est vide. Et la vision d'un produit public
repose sur une collecte massive que ce depot lui-meme decrit comme
impossible. Tout ce qui suit decoule de ces trois phrases.

**Aucune donnee reelle.** Les cinq sources sont marquees `verified: false`.
Les annonces de la demo sont synthetiques. L'estimation repose entierement
sur des comparables en base : sur une base vide, elle ne produit rien.

**La mesure de qualite est circulaire.** Le marche de demonstration est
genere avec les memes courbes de decote que l'estimateur. La separation
affaires / pieges prouve que la lecture des signaux fonctionne. Elle ne
prouve ni que l'estimation de prix est juste, ni que l'expertise photo
apporte du signal : aucune des deux n'a ete mesuree sur du reel.

**Le differenciant n'est pas le prix.** Leboncoin, AutoScout24 et La
Centrale affichent deja un badge "bon prix" face au marche. Ce que personne
ne fait, c'est lire l'annonce comme un mandataire : incoherences, defauts
connus de la motorisation, questions a poser, leviers de negociation. C'est
la que la valeur se paie, et c'est la partie du code la plus aboutie.

**La contradiction a trancher.** Scanner leboncoin en masse est fragile
techniquement (protection anti-bot, rendu JavaScript) et expose
juridiquement (CGU, directive bases de donnees). Ce depot respecte le
`robots.txt` et ne contourne rien, et c'est la bonne ligne. Un index
massif pre-crawle n'est donc pas une option : cela se regle par des accords
de donnees, pas par du code.

## La direction : une recherche a la demande, pas un index

L'utilisateur saisit ses criteres : Scenic, 2018, essence, 200 km autour de
Lyon. CarExpert lance une collecte polie ciblee sur cette requete, estime
chaque annonce contre la cote officielle et les comparables deja en base,
expertise en profondeur les meilleures, et renvoie un classement explique.

C'est asynchrone : quelques minutes, puis une notification. Le rythme poli
devient acceptable parce que c'est une tache de fond, pas une page qui
charge. Chaque recherche enrichit la base, donc les comparables
s'ameliorent avec l'usage.

C'est exactement ce que fait deja `carexpert scan --url ... --deep 5`,
emballe dans une file de taches et une interface. La meme brique sert a
"colle une annonce, obtiens l'expertise" (`carexpert analyse-url`), qui est
le plus petit produit livrable.

Ordre de grandeur d'une recherche :

| Poste | Cout |
|---|---|
| collecte | une centaine de requetes HTTP, au rythme poli |
| expertise profonde | 5 a 10 appels modele, environ 1 EUR |
| delai | quelques minutes |

Un prix unitaire de quelques euros est viable, et "trois recherches
gratuites puis payant" en decoule naturellement.

**La cote officielle comme ancre.** En France c'est un marche B2B :
L'Argus, Autobiz et Autovista proposent des API professionnelles. Budgeter
un abonnement. Dans le code, la cote devient le *prior* de l'estimation et
les comparables la *preuve*, ponderes par la confiance. Des le premier jour
on a un prix ; il se raffine quand la base se remplit.

## Prochaines etapes, par ordre de valeur

1. **Une source reelle qui marche de bout en bout.** AutoScout24, qui
   publie du `schema.org` sans JavaScript. Lancer `carexpert diagnose`,
   corriger ce qu'il signale, mesurer le taux d'extraction sur deux cents
   annonces, passer `verified: true`. Tout le reste en depend. Ne pas
   ajouter de sixieme YAML avant.
2. **Un jeu d'evaluation reel.** Cinquante annonces reelles jugees a la
   main, seul ou avec un mandataire : prix juste, a voir, a fuir. C'est la
   seule facon de savoir si l'expertise photo aide ou fait du bruit, et de
   regler le score sans se raconter d'histoires. Ce jeu devient un test.
3. **La cote officielle comme ancre** de l'estimation : un fournisseur
   derriere une interface, un cache, et le melange cote / comparables
   pondere par la confiance.
4. **La geographie.** Code postal et coordonnees sur les annonces,
   geocodage gratuit via l'API Adresse de l'Etat, filtre de distance dans
   les requetes et dans l'API. Aujourd'hui le rayon et le code postal
   existent dans `SearchQuery` mais ne servent a rien, et le code postal
   n'est meme pas stocke en base. Cela demande des migrations de schema :
   ajouter Alembic d'abord, le projet n'en a pas.
5. **Comparables par version, puissance et carrosserie** dans les paliers.
   La version est stockee mais jamais utilisee : un Scenic dCi 110 et un
   Scenic TCe 160 sont le meme vehicule pour l'estimateur. Sur donnees
   reelles ce sera la premiere source de bruit.
6. **La recherche a la demande comme objet** : une table de recherches, un
   worker, un endpoint de creation, une page de resultats avec le
   classement explique. C'est le produit.
7. **Comptes, quotas, paiement**, en dernier, quand une recherche a ete
   utilisee par quelqu'un d'autre que son auteur.

## Ce qu'on ne fait pas maintenant

- **L'application mobile.** Une page responsive et une notification
  suffisent pour valider. L'API JSON est deja la pour plus tard.
- **L'arbitrage transfrontalier.** Les facteurs pays sont dans le modele ;
  le calcul complet du cout d'import attend d'avoir des comparables dans
  deux pays.
- **L'historique VIN.** Histovec exige les donnees du proprietaire et ne
  s'utilise pas en masse. A brancher le jour ou un fournisseur existe.
- **Une sixieme source.** Une source verifiee vaut mieux que cinq gabarits.
- **Etoffer la base de faiblesses connues** avant que le jeu d'evaluation
  ne dise ce qui manque vraiment.

## Dettes a regler au passage

Elles couteront plus cher plus tard :

- la date de premiere mise en circulation n'est pas stockee, l'age est donc
  arrondi a l'annee ;
- le filtre de criteres est duplique trois fois (pipeline, veilles, demo) ;
- aucune migration de schema : `create_all` seulement ;
- `ruff` est declare mais la CI ne le lance pas.

## Modele economique

- gratuit : trois recherches, ou une veille, analyse par regles ;
- payant : recherches a l'unite ou en abonnement, expertise Claude sur
  photos, veilles illimitees, alertes instantanees ;
- pro (marchands) : detection des vehicules sous-cotes a racheter, avec
  estimation de marge apres remise en etat.

Le cout marginal d'une recherche se compte en euros ; la valeur d'une bonne
affaire detectee, ou evitee, en centaines d'euros. Le rapport est
favorable, a condition de garder la passe large gratuite et de payer la
cote.
