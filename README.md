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
| Annonces au prix | +2 % | 56 / 100 |
| **Pieges** (moteur fatigue, compteur douteux, vendu en l'etat) | **-39 %** | **31 / 100** |

Les pieges sont les annonces **les moins cheres du marche**. Un tri par prix
les met en premiere page. Apres lecture du texte et de l'etat, ils tombent a
31 et sortent du classement : le top 20 ne contient pas un seul piege, mais
seize vraies affaires et quatre annonces au prix.

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
300 annonces vues, 300 nouvelles, 0 mises a jour, 0 baisses de prix, 0 ignorees
0 annonces collectees (0 nouvelles), 300 estimees, 0 expertisees en profondeur, 0 alertes

  id  score  avis       vehicule                                  prix   marche   ecart
 106     93  A SAISIR   Renault Clio Intens diesel 2022          9 060   11 412  +2 352
  27     92  A SAISIR   Volkswagen Golf Confortline 2021         9 140   12 898  +3 758
 131     91  A SAISIR   Dacia Sandero Stepway GPL 2017           2 350    2 870    +520
```

Puis le detail d'une annonce, avec le raisonnement complet :

```bash
carexpert show 27
carexpert deals        # le classement, sans rien recollecter
carexpert serve        # tableau de bord sur http://127.0.0.1:8000
```

---

## Sur de vraies annonces

**Commencer par la : verifier qu'une source fonctionne vraiment.**

```bash
carexpert diagnose -s autoscout24 --make Volkswagen --model Golf
```

En une trentaine de secondes, et quelques requetes seulement, la commande
repond : le `robots.txt` autorise-t-il cette URL, le site repond-il, les
annonces sont-elles dans le HTML ou rendues en JavaScript, le motif de lien
est-il bon, et les champs essentiels sortent-ils sur un echantillon. Quand
quelque chose manque, elle montre ce qu'elle a vu plutot que de le decrire :
les familles d'URL de la page avec un exemple chacune, la forme de recherche
que le site declare pour lui-meme, un motif de lien deduit de la page.

Sortie reelle, AutoScout24, 20 septembre 2026 :

```
+--------- SOURCE EXPLOITABLE (PAGE DE RESULTATS) ---------+
| 20 annonces lues directement sur la page de resultats    |
| via schema.org, dont 20 completes                        |
+----------------------------------------------------------+
 robots.txt             present
 autorise               oui
 reponse                HTTP 200 - 804180 octets en 0.4 s
 annonces sur la liste  20 lues sans ouvrir d'annonce (20 completes)
 lues via               schema.org
 rendu                  JavaScript detecte mais les annonces sont dans la page
```

Une source qui refuse se lit aussi clairement : **REFUS DU SITE**, avec le
nom de la protection quand la reponse le porte. Un refus n'est pas une
panne, et les deux ne se corrigent pas pareil.

Une fois la source validee :

```bash
cp .env.example .env          # y mettre ANTHROPIC_API_KEY pour l'expertise photo
carexpert scan -s autoscout24 --make Renault --model Twingo --limit 400
```

Comptez deux minutes : vingt requetes de liste pour 400 annonces, puis les
vingt meilleures rouvertes une a une pour lire leur descriptif, au rythme
poli du site.

Par URL collee, ce qui reste la voie la plus sure quand les filtres sont
complexes :

```bash
carexpert scan -s autoscout24 --url "<URL de recherche collee>" --deep 5
```

Le parcours suit la pagination tout seul et s'arrete des qu'une page
n'apporte rien de nouveau.

Mettre une recherche sous surveillance :

```bash
carexpert watch add twingo --make Renault --model Twingo \
                   --price-max 6000 --min-score 78 --channel telegram
carexpert watch run
```

Expertiser une annonce precise, tout de suite :

```bash
carexpert analyse-url "https://www.autoscout24.fr/offres/..."
```

### Sur les sites qui refusent la collecte

```bash
carexpert import ~/Downloads/leboncoin-twingo.html
```

Vous cherchez sur le site, normalement, dans votre navigateur. Vous
enregistrez la page. CarExpert la lit, l'estime et la classe comme le reste.
L'extension Chrome fait la meme chose sans passer par le disque.

---

## Ce que l'outil regarde vraiment

**Le prix.** Des comparables sont cherches en base par paliers (meme modele,
meme energie, meme boite, annee et kilometrage proches, meme pays), puis
chacun est **ramene aux conditions du vehicule analyse** : age, kilometrage,
boite, options, type de vendeur, pays. Le prix juste est la mediane robuste
des prix ainsi ajustes, assortie d'une confiance qui pondere son poids dans
la note.

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

Etat mesure le 20 septembre 2026, sur les sites reels. Il se refait avec
`carexpert diagnose`, et il se perime.

| Source | Pays | Etat |
|---|---|---|
| `autoscout24` | DE FR IT ES NL BE AT | **ouverte** : 20 annonces completes par page de resultats, en `schema.org` |
| `leparking` | FR BE DE ES IT NL | **ouverte** : agregateur, republie leboncoin et La Centrale avec lien vers la source |
| `ouestfrance-auto` | FR | rendu serveur, non testee |
| `mobile_de` | DE AT | non testee |
| `leboncoin` | FR | **fermee** : DataDome, 403 en requete, captcha au navigateur |
| `lacentrale` | FR | **fermee** : meme protection |
| `coches_net` | ES | rendu client, non testee |
| `demo` | - | marche synthetique, hors ligne, pour tests et demonstration |

Ajouter un site = deposer un fichier YAML dans `src/carexpert/sources/sites/`.
Aucun code Python.

### Les sites qui refusent la collecte

leboncoin et La Centrale disent non, et ce non tient : mesure faite deux
fois sur chacune. Le projet ne le contourne pas.

Mais vous, quand vous cherchez une Twingo, vous **regardez deja la page** :
le site vous l'a servie, volontairement, comme a un visiteur. La lire n'est
pas de la collecte.

```bash
carexpert import ~/Downloads/leboncoin-twingo.html   # une page enregistree
carexpert import ~/Downloads/annonces/               # un dossier entier
```

Au quotidien, l'extension Chrome (`extension/`) fait la meme chose sans
enregistrer de fichier : vous naviguez normalement, elle pose une pastille
sur chaque annonce - **A SAISIR**, **A VERIFIER**, **A FUIR**, **A ESTIMER**
quand elle n'a pas assez de comparables pour se prononcer. Voir
[`extension/README.md`](extension/README.md).

`sources/captured.py` n'a aucun code reseau et ne peut acquerir aucune page
tout seul. C'est ce qui laisse cette voie ouverte quand les autres sont
fermees, et un test le verrouille.

### Les trois paliers d'extraction

Du moins cher au plus cher, et dans cet ordre :

1. **`schema.org`** (`sources/structured.py`) : le balisage que le site
   publie pour les moteurs. Une requete, zero JavaScript.
2. **Etat embarque** (`sources/embedded.py`) : le JSON que la page hydrate.
   Meme cout. C'est ce qui rend leboncoin et La Centrale lisibles sans
   navigateur, une fois la page ouverte par vous.
3. **Rendu Chromium** (`sources/browser.py`) : ne demarre que si les deux
   premiers reviennent vides, et s'arrete net devant une page de defi.

Jamais de selecteurs CSS : ils changent a chaque deploiement.

**A lire avant toute collecte reelle : [`docs/02-sources-et-legal.md`](docs/02-sources-et-legal.md).**
Le `robots.txt` est respecte par defaut - lu selon RFC 9309, jokers et
query compris, parce que celui de la bibliotheque standard se trompe dans
les deux sens. Une requete toutes les 2,5 secondes par domaine, rien en
parallele, et les protections anti-bot ne se contournent pas.

---

## Documentation

- [Architecture](docs/01-architecture.md) : le flux, les couches, les choix techniques
- [Sources et cadre legal](docs/02-sources-et-legal.md) : politique de collecte, CGU, RGPD
- [Le scoring](docs/03-scoring.md) : estimation, ponderation, limites
- [Suite](docs/04-roadmap.md) : prochaines etapes et modele economique

---

## Developpement

```bash
pip install -e ".[dev,photos]"
pytest -q                   # 292 tests, tous hors ligne
carexpert init              # creer la base (les autres commandes le font seules)
carexpert sources           # sources disponibles et leur etat
carexpert diagnose -s autoscout24 --make Volkswagen --model Golf
carexpert import page.html  # lire une page que vous avez ouverte
```

Le rendu navigateur, troisieme palier de collecte, est une dependance a part :

```bash
pip install -e ".[browser]" && python -m playwright install chromium
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
extension/     compagnon Chrome : pastilles sur les annonces consultees
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
