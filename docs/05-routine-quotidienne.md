# Routine quotidienne

Deux heures trente, quatre blocs, un seul chantier par jour. Ce fichier sert a
deux choses : savoir ou en est le projet en ouvrant l'ordinateur, et savoir
quoi faire avant d'avoir a le decider.

## Ou en est le projet, au 18 septembre 2026

Mesure, pas impression :

- 17 commits, tous sur `claude/practical-mayer-gp4tro`.
- 167 tests passent, 1 ignore, en 55 secondes. CI verte a chaque push.
- Le flux complet tient debout : collecte, normalisation, estimation par
  comparables, expertise Claude, score explique, alerte.
- 5 gabarits de sites, **un seul verifie** : `autoscout24`. mobile.de,
  La Centrale, leboncoin et coches.net sont ecrits, jamais confrontes a une
  page reelle.
- L'outil se pilote au navigateur : recherche depuis une URL collee, scan en
  tache de fond, veilles rejouables d'un clic. Le dossier `lancer/` evite le
  terminal.
- 24 invariants dans `CLAUDE.md`, chacun ne d'un defaut mesure.

### Ce qui manque pour que ce soit un site

Sept manques, dans l'ordre ou ils bloquent :

1. **Pas une seule annonce reelle en base.** L'invariant 16 interdit de
   valoriser une vraie voiture sur des comparables inventes. Base vide,
   l'estimation ne sort pas de la demo.
2. **Une seule source verifiee**, donc un seul vivier de comparables, donc des
   verdicts "A ESTIMER" des qu'on quitte les modeles les plus courants.
3. **Rien ne tourne quand l'ordinateur est ferme.** Aucun Dockerfile, aucun
   hebergeur, aucune planification : `carexpert watch run` existe, rien ne
   l'appelle.
4. **L'etat des scans vit dans la memoire du process.** Un redemarrage efface
   l'historique du lanceur.
5. **Le tableau de bord n'a pas de porte.** Mis en ligne tel quel, il est
   ouvert a qui trouve l'adresse.
6. **SQLite en fichier local.** Postgres est annonce dans `.env.example`,
   aucun test ne l'exerce, `psycopg` n'est pas dans les dependances.
7. **Aucun historique de prix.** La baisse est lue sur la carte du jour puis
   jetee, alors que c'est le meilleur signal d'un vendeur pret a negocier.

## Operationnel, ca veut dire quoi

Objectif a six semaines, fin octobre. Huit cases. Tant qu'il en manque une, le
site n'est pas operationnel, quelle que soit l'impression que donne l'ecran.

- [ ] Le site repond a une adresse publique, ordinateur ferme.
- [ ] Un scan quotidien part seul, a heure fixe, et remplit la base.
- [ ] Deux sources reelles verifiees, taux d'extraction mesure sur 200 annonces.
- [ ] Une estimation s'appuie en moyenne sur 10 comparables reels ou plus.
- [ ] Une affaire mise en ligne le matin declenche une alerte sur le telephone
      dans l'heure.
- [ ] Le tableau de bord demande un mot de passe.
- [ ] La base survit a un redemarrage et part en sauvegarde chaque nuit.
- [ ] Quelqu'un d'autre ouvre le site, cree une veille, comprend un verdict,
      sans que tu sois a cote.

La derniere case est la plus dure, et c'est la seule qui dise si l'outil
existe vraiment.

## Les quatre blocs

### 1. Ouverture, 15 minutes

```bash
git pull
pytest -q          # vert obligatoire ; rouge, c'est le chantier du jour
carexpert serve    # a laisser ouvert dans un onglet
```

Relever trois chiffres, toujours les memes, en haut du tableau de bord :
annonces en base, annonces notees, affaires a 75 et plus. Les ecrire dans le
journal avant toute autre chose. Une routine sans chiffres devient une
impression en une semaine.

### 2. Le chantier du jour, 90 minutes

Un seul, choisi la veille, deja ecrit dans le journal. On ne choisit pas son
chantier le matin : c'est exactement la que part la premiere heure.

Il se termine par un commit qui dit **pourquoi**, accompagne du test qui
aurait attrape le defaut s'il s'agit d'une correction non evidente.

Si les 90 minutes passent sans que ce soit fini, on ne prolonge pas : on note
ou on en est, la suite devient le chantier de demain. Un chantier qui deborde
trois jours de suite n'est pas trop gros, il est mal decoupe : le refendre en
trois choses qui se commitent chacune.

### 3. Le tour du marche, 20 minutes

Fermer l'editeur. Ouvrir le site en acheteur, pas en developpeur.

Prendre les cinq annonces les mieux notees. Pour chacune : lire le verdict et
ses raisons, ouvrir l'annonce chez le vendeur, repondre a une seule question,
**est-ce que j'irais la voir ?**. Trois lignes dans le journal : ce qui a
sonne juste, ce qui a sonne faux, ce qui manquait a l'ecran.

C'est le bloc qu'on saute quand on est presse. C'est aussi le seul qui empeche
l'outil de devenir techniquement correct et inutilisable.

### 4. Cloture, 15 minutes

```bash
git commit                                       # pourquoi, pas quoi
git push -u origin claude/practical-mayer-gp4tro
```

Puis quatre lignes dans `docs/journal.md`, et le chantier de demain ecrit en
une phrase qui commence par un verbe. Fin de journee : ne pas rouvrir.

## Le journal

Une entree par jour dans `docs/journal.md`, quatre lignes, jamais plus, la
plus recente en haut :

```
## 18/09
Chiffres : 0 en base, 0 notees, 0 affaire a 75+.
Fait : premier scan reel AutoScout24, 114 annonces, 9 rejets d'extraction.
Vu : "A ESTIMER" sort sur 6 annonces sur 10, faute de comparables.
Suite : mesurer le taux d'extraction sur 200 annonces.
```

Ce qui n'est pas dans le journal n'a pas eu lieu : demain matin, c'est la
seule memoire disponible.

## Aujourd'hui, 18 septembre

La matinee est deja passee en commits (pilotage navigateur, plafond de
collecte compte en requetes). Le chantier ci-dessous est celui de
l'apres-midi.

Chantier : **du reel en base, sur un seul modele.** Tout le reste attend :
sans comparables reels, les six autres manques ne se mesurent meme pas.

1. Faire sur AutoScout24 une recherche large d'un modele fourni (Golf, 308,
   Clio), en France, et garder l'URL telle quelle, filtres compris.
2. `carexpert diagnose -s autoscout24 --url "..."`, puis corriger ce qu'elle
   signale : le gabarit YAML d'abord, le code seulement s'il ne suffit pas.
3. `carexpert scan -s autoscout24 --url "..." --limit 400`. Au rythme poli,
   compter une dizaine de minutes. Ne pas chercher a la raccourcir.
4. Noter trois chiffres : annonces entrees en base, comparables par
   estimation, confiance mediane. Ce sont eux qui diront si le vivier est
   assez fourni pour qu'un verdict tienne.
5. Ouvrir trois fiches et lire le verdict. Une confiance sous le seuil doit
   donner "A ESTIMER", jamais "A FUIR" (invariants 7 et 13). Si "A FUIR" sort
   sur une voiture saine, c'est le chantier de demain, avant tout le reste.

Puis tour du marche sur ces memes annonces, et cloture normale. Chantier
attendu demain : mesurer le taux d'extraction sur 200 annonces et corriger les
trois champs manquants les plus frequents.

## Les six semaines

| Semaine | Le chantier | Ce qui devient vrai |
| --- | --- | --- |
| S38, 18-21/09 | du reel en base sur un modele, taux d'extraction mesure sur 200 annonces | AutoScout24 tient en conditions reelles |
| S39 | deuxieme source, meme protocole, `verified: true` seulement apres mesure | deux viviers de comparables |
| S40 | Dockerfile, Postgres, hebergeur, sauvegarde de nuit | le site repond a une adresse |
| S41 | scan quotidien planifie, alertes Telegram branchees | l'outil travaille sans toi |
| S42 | mot de passe sur le tableau de bord, historique de prix sur trois mois | on peut le montrer, et il sait ce que personne n'affiche |
| S43 | cinq personnes l'utilisent une semaine, sans toi a cote | la derniere case |

Rien de neuf avant la semaine 43. Une fonction ajoutee pendant qu'une case
reste vide est une fonction que personne n'utilisera.

## Les quatre regles qui evitent de perdre une journee

1. **Mesurer avant, mesurer apres, et mettre les deux chiffres dans le
   commit.** "Plus rapide" n'est pas une mesure ; "31 ms au lieu de 8" en est
   une (invariant 22).
2. **Aucune source ne passe `verified: true` sur une impression.** Un taux
   d'extraction sur 200 annonces, ou rien.
3. **Toute correction non evidente arrive avec son test.** Un chemin que les
   tests n'empruntent jamais finit par casser (invariant 15).
4. **Un jour saute : on ne rattrape pas.** On reprend le lendemain. Une
   routine qui se rattrape devient une dette, et une dette finit par se
   laisser tomber.
