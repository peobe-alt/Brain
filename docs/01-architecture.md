# Architecture

## Le probleme, pose correctement

Trouver une bonne affaire dans des milliers d'annonces demande trois choses
que les filtres des sites ne font pas :

1. **Savoir ce que vaut la voiture.** Un filtre "moins de 15 000 EUR" ne dit
   pas si 14 900 EUR est cher ou donne pour ce modele, cette annee, ce
   kilometrage.
2. **Lire l'annonce en entier.** L'information decisive est dans la phrase
   "vendu en l'etat", dans une photo ou l'aile ne reflete pas la lumiere comme
   la portiere, dans un habitacle trop use pour le kilometrage annonce.
3. **Faire les deux sur tout le marche, tous les jours.** Une bonne affaire
   entre particuliers part en quelques heures.

CarExpert repond a ces trois points dans cet ordre.

## Le flux

```
   entree                     normalisation        estimation
 ┌───────────────┐            ┌───────────────┐    ┌──────────────┐
 │ AutoScout24   │ scan poli  │ marque/modele │    │ comparables  │
 │ leparking     │ ───────>   │ energie/boite │    │ + courbes de │
 └───────────────┘            │ options       │──> │   decote     │
                              │ signaux texte │    │ = prix juste │
 ┌───────────────┐            └───────────────┘    └───────┬──────┘
 │ les pages que │ import ou          ^                    │
 │ vous ouvrez   │ extension          │                    │
 └───────────────┘ ───────────────────┘                    │
                        ┌──────────────────────────────────┘
                        v
                ┌──────────────┐        ┌──────────────┐      ┌─────────┐
                │ passe large  │  top N │ expertise    │      │ score   │
                │ regles seules│ ─────> │ Claude       │ ───> │ + alerte│
                │ (gratuite)   │        │ photos+texte │      │         │
                └──────────────┘        └──────────────┘      └─────────┘
```

Les deux entrees se rejoignent des la normalisation : une annonce lue dans
une page que vous avez ouverte suit exactement le meme chemin qu'une annonce
collectee, et compte comme comparable au meme titre.

## Les couches

| Module | Role | Pourquoi c'est la |
|---|---|---|
| `sources/` | collecte | Un adaptateur par site, decrit en YAML, sans code Python. L'extraction procede en trois paliers, du moins cher au plus cher, et jamais par selecteurs CSS. |
| `sources/robots.py` | le droit de lire | Le `robots.txt` relu selon RFC 9309. Celui de la bibliotheque standard se trompe dans les deux sens, et nous avait fait declarer interdite la plus grosse source du projet. |
| `sources/captured.py` | les pages que vous ouvrez | Aucun code reseau, volontairement : c'est ce qui laisse cette voie ouverte quand un site refuse la collecte. |
| `normalize/` | vocabulaire commun | `Serie 3`, `3er`, `3 Series` doivent devenir la meme chose, sinon les comparables sont faux. |
| `normalize/signals.py` | lecture du texte | Transforme la prose en signaux typés, avec gestion des negations (`jamais accidente`). |
| `valuation/` | prix de marche | Comparables choisis par paliers, ramenes aux conditions du vehicule cible, mediane robuste. |
| `expert/` | expertise | Claude lit photos et texte avec une sortie structuree. Une base de faiblesses connues par motorisation oriente son regard. |
| `scoring/` | decision | Un score 0-100 additif, ou chaque point est justifie. |
| `alerts/` | notification | Veilles permanentes, une alerte par annonce et par veille. |
| `api/` + `web/` | interface | Rendu serveur, sans etape de build. Les endpoints JSON permettent de brancher un vrai front plus tard. |
| `extension/` | compagnon Chrome | Les memes verdicts poses sur les pages du site que vous consultez. Le service worker appelle CarExpert ; le script de contenu, lui, s'execute sous la politique de securite du site. |

## Trois paliers d'extraction

Lire une page coute, et les trois moyens de la lire ne coutent pas la meme
chose. Ils sont donc essayes dans l'ordre, et le suivant ne demarre que si le
precedent revient vide :

1. **Le balisage `schema.org`** (`sources/structured.py`). Les sites le
   publient pour les moteurs de recherche, donc ils le tiennent a jour. Une
   page de resultats AutoScout24 y porte ses vingt annonces completes : une
   requete pour vingt voitures.
2. **L'etat embarque** (`sources/embedded.py`). Une application React ne va
   pas chercher sa premiere page de resultats apres l'affichage : elle la
   serialise dans le HTML pour son hydratation. Meme cout qu'une requete
   ordinaire. Mesure sur leboncoin : zero lien d'annonce, zero noeud
   schema.org, et pourtant les six annonces completes dans la premiere
   reponse HTTP.
3. **Un rendu Chromium** (`sources/browser.py`). Trente fois le prix d'une
   requete, plusieurs secondes, et une page de defi anti-bot l'arrete net.
   D'ou l'ordre.

Les objets d'annonce du palier 2 sont reconnus **a leur forme**, pas a leur
chemin : `props.pageProps.searchData.ads[0]` est aussi fragile qu'un
selecteur CSS. Une annonce, c'est une identite, un prix, et un kilometrage ou
une annee.

## Quand un site dit non

Certains sites refusent la collecte automatisee, et ce refus tient : 403 avec
interstitiel sur une requete simple, captcha sur un navigateur sans tete. Le
projet ne le contourne pas, et l'architecture en tient compte plutot que de
s'y heurter.

La personne qui cherche une Twingo **regarde deja la page** : le site la lui
a servie, volontairement, comme a un visiteur. `sources/captured.py` lit ce
qu'elle a ouvert. Ce module n'a aucun code reseau, et c'est une decision de
conception, pas un oubli : il ne peut pas acquerir une page tout seul, ce qui
rend la promesse verifiable plutot que declarative. Un test la verrouille en
faisant echouer toute requete sortante.

Deux facons de l'utiliser, la meme chaine derriere :

- `carexpert import page.html` sur une page enregistree ou un dossier ;
- l'extension Chrome (`extension/`), qui pose une pastille sur chaque annonce
  pendant que vous naviguez. C'est le service worker qui appelle CarExpert,
  jamais le script de contenu : celui-ci s'execute dans la page, donc sous la
  politique de securite du site, qui interdit d'appeler une adresse inconnue.

## Deux passes, pour une raison de cout

Analyser mille annonces avec un modele multimodal coute cher. La chaine est
donc separee en deux :

- **passe large** : tout est collecte, normalise, estime et note avec les
  regles internes. Local, instantane, gratuit.
- **passe profonde** : seules les meilleures candidates partent chez Claude
  avec leurs photos, puis sont renotees.

`--deep 10` sur un scan de 800 annonces, c'est dix appels modele, pas huit
cents. Et une analyse profonde n'est jamais ecrasee par une passe large
ulterieure.

Compter environ 0,10 a 0,15 EUR par expertise approfondie. Les tokens
reellement consommes sont enregistres par annonce et totalises a la fin du
scan : le montant affiche est mesure, pas estime.

## Degradation plutot qu'echec

La couche experte ne leve jamais d'exception. Une limite de debit, un refus
du modele, une reponse tronquee, une cle invalide : chaque cas retombe sur
l'analyse par regles avec une raison lisible, et le scan poursuit. Un lot de
dix expertises ne se perd pas sur un incident.

Deux consequences de conception :

- **tous les champs du rapport sont obligatoires.** Un champ optionnel dans
  le schema est un champ que le modele peut omettre, et il omet en priorite
  ce qui demande du travail : les alertes, les questions au vendeur, les
  leviers de negociation. Une liste vide reste une reponse valable, mais
  c'est alors une decision, pas un silence ;
- **le type reel d'une photo est lu dans ses octets**, jamais dans son
  en-tete `Content-Type`. Les sites d'annonces repondent regulierement 200
  avec une page HTML a la place d'une image manquante ; l'envoyer a l'API
  comme du base64 `image/jpeg` ferait echouer l'expertise entiere.

## Passer a l'echelle

Deux regles, verifiees sur une base de 5 000 annonces :

- **aucun plafond dans la selection.** Un `LIMIT` sur les annonces a evaluer
  est invisible en test et catastrophique en production : sur 5 000 annonces,
  un plafond a 1 000 laissait 80 % du stock sans note, donc absent du tableau
  de bord et des alertes. Le volume se traite par lots, pas par troncature.
- **on ne recalcule que ce qui a bouge.** Une estimation est refaite si elle
  n'existe pas, si elle a plus de 24 heures, ou si le prix a change depuis.
  La fraicheur se lit dans la base (`analyzed_at` face a `price_changed_at`),
  pas dans ce que le scan en cours a vu passer : c'est vrai quel que soit le
  chemin par lequel l'annonce est entree.

Mesure : 5 000 annonces evaluees en 30 s, soit 6 ms par annonce. Le scan
suivant, sans changement, ne recalcule rien.

## Choix techniques

- **SQLite par defaut**, Postgres en changeant une variable d'environnement.
  Rien a installer pour commencer.
- **Pas de numpy** : la regression de decote est resolue par elimination de
  Gauss sur un systeme 3x3, une trentaine de lignes.
- **Pillow optionnel** : sans lui, les photos partent non redimensionnees.
- **Sortie structuree** cote modele : le rapport d'expertise est un objet
  valide, jamais du texte a parser.
