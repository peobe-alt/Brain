# CarExpert

Scanner d'annonces automobiles europeennes : collecte, estimation du prix de
marche, expertise assistee par Claude sur photos et texte, score explique,
alertes.

## Commandes

```bash
pip install -e ".[dev,photos]"
pytest -q                                  # suite complete
carexpert demo                             # demonstration hors ligne
carexpert diagnose -s autoscout24 --url .. # valider une source
carexpert import page.html                 # lire une page ouverte par vous
# extension/ : les memes verdicts poses sur les pages que vous consultez
carexpert serve                            # tableau de bord
```

## Conventions

- **Code et commentaires en anglais, interface et documentation en francais.**
  Le produit s'adresse a un marche francophone ; le code reste lisible par
  n'importe quel developpeur.
- Pas d'accents dans les chaines du code, du CLI ni des fichiers YAML :
  l'affichage terminal et les comparaisons de mots-cles travaillent sur du
  texte desaccentue.
- Les commentaires expliquent **pourquoi**, pas quoi. Un commentaire qui
  paraphrase la ligne suivante est du bruit.
- Toute correction non evidente s'accompagne du test qui l'aurait attrapee.

## Invariants a ne pas casser

Chacun vient d'un defaut reel, mesure :

1. **Aucun plafond dans les requetes qui parcourent la base.** Un `LIMIT` sur
   les annonces a evaluer laissait 80 % du stock sans note, invisible en test.
   Le volume se traite par lots.
2. **Tous les champs du rapport d'expertise sont obligatoires.** Un champ
   optionnel dans le schema est un champ que le modele omet, en commencant par
   ceux qui demandent du travail : alertes, questions au vendeur, leviers de
   negociation.
3. **L'empreinte d'un vehicule utilise le kilometrage exact, jamais arrondi.**
   Toute tolerance fusionne des voitures simplement semblables et vide le
   vivier de comparables.
4. **Les comparables sont dedupliques par vehicule.** Les marchands
   crosspostent plus que les particuliers et affichent plus cher : compter
   chaque copie fait deriver toutes les estimations vers le haut.
5. **La couche experte ne leve jamais d'exception.** Tout echec retombe sur
   l'analyse par regles avec une raison lisible ; une passe profonde porte sur
   un lot et ne doit pas se perdre sur un incident.
6. **Le type reel d'une photo se lit dans ses octets**, jamais dans l'en-tete
   `Content-Type` : les sites servent des pages HTML en 200 a la place d'une
   image manquante.
7. **Sans comparables, le verdict est `unknown`, pas `avoid`.** Ne pas savoir
   situer un prix n'est pas une raison de fuir une voiture saine.
8. **Les alertes se basent sur l'etat stocke**, pas sur ce que la passe en
   cours a recalcule : une veille creee aujourd'hui doit remonter une affaire
   notee hier.
9. **L'identifiant d'annonce ne se devine pas au dernier nombre de l'URL.**
   Sur AutoScout24 le segment `cat_ma73mo2079` porte l'identifiant du
   *modele*, partage par toutes les Volvo V70 : le lire comme identifiant
   d'annonce fait s'ecraser toutes les V70 sur la meme cle unique. Chercher
   l'UUID, puis un parametre `id` de la query, puis seulement un nombre long.
10. **La detection de mots-cles se fait sur des frontieres de mots.** Sinon
   `sw` matche dans `volkswagen`, `import` dans `importante`, et `electrique`
   dans `hayon electrique`. Les negations comptent : `jamais accidente` n'est
   pas `accidente`.
11. **Zero lien d'annonce ne veut pas dire zero annonce.** Sur AutoScout24 les
   `<a>` de titre n'ont pas d'attribut `href` : le site l'ajoute en
   JavaScript. La page publie pourtant ses annonces completes en JSON-LD
   (`SearchResultsPage` -> `ItemList`). On lit donc la liste d'abord, les
   liens ensuite. Verifie sur une page reelle : 0 lien, 14 annonces.
12. **Le superethanol E85 n'est pas du GPL.** Autre carburant, autre
   reservoir, autre cote, et `Fuel.ETHANOL` existe pour ca. Les confondre
   valorisait 4 des 14 Volvo V70 de la page reelle avec la mauvaise courbe.
13. **Un verdict exige de quoi le tenir.** Pas de comparables, ou une
   confiance sous `min_confidence_for_verdict`, et la reponse est `unknown`
   (A ESTIMER). Sur un marche fourni la confiance mesuree va de 0,48 a 0,70
   (p5-p90) : le seuil ne se declenche que quand la base est trop mince, ce
   qui est la regle sur un modele rare.
14. **Une page de resultats ne donne jamais le descriptif.** Or c'est la que
   se cachent les pieges ("moteur a revoir", "vendu sans controle
   technique"). Les meilleures annonces du tour sont donc rouvertes une a une
   (`_detail_pass`), puis reestimees : la page d'annonce fait foi, la liste
   bouche ses trous (code postal, mise en circulation).
15. **Un chemin que les tests n'empruntent jamais finit par casser.** La
   passe profonde appelait une fonction inexistante : aucun test n'allait
   jusque-la, faute de cle API. Tout chemin conditionne par une cle ou un
   reseau doit avoir son test avec un double.
16. **Une annonce reelle ne se compare qu'a des annonces reelles.** Le marche
   synthetique (`sources/demo.py`) mesure la qualite du classement, il ne fixe
   aucun prix. Mesure : une Golf reelle a 15 990 EUR estimee a 6 935 EUR face
   a 10 comparables inventes, avec 0,58 de confiance et un verdict "A FUIR".
   La confiance ne protege pas : de fausses annonces sont parfaitement
   coherentes entre elles.
17. **La puissance se lit aussi dans le texte de la carte.** Sur une page de
   resultats AutoScout24, trois annonces sur six ne la donnent nulle part
   ailleurs que dans `235 kW (320 Ch)`. Sans elle, une Golf R de 320 ch se
   compare a une 1.2 TSI.
18. **Un prix qui vient de baisser est ecrit sur la carte.** Le lire evite
   d'attendre un second passage, c'est-a-dire le lendemain, pour voir une
   baisse que le vendeur affiche aujourd'hui.
19. **Une URL de recherche collee se suit de page en page.** Une recherche
   Volkswagen Golf en France, c'est 1 849 annonces sur 93 pages : n'en lire
   qu'une, c'est voir 20 annonces en croyant avoir tout vu. Le parcours
   s'arrete des qu'une page n'apporte rien de nouveau.
20. **`canonical_url` ne s'applique qu'aux URL d'annonce.** Sur une URL de
   recherche, `sort` et `atype` ne sont pas du tracage mais les filtres
   choisis dans l'interface du site : les retirer change silencieusement la
   recherche de l'utilisateur.
21. **Un plafond de collecte compte les requetes, jamais les succes.** La
   passe de detail comptait les annonces effectivement enrichies : sur un
   site dont les pages d'annonce ne rendaient pas de descriptif, elle a
   parcouru toute la base. Mesure sur un balayage complet d'un modele :
   1 979 requetes au lieu de 114, soit une heure et quart de sollicitation
   du site au rythme poli au lieu de cinq minutes.
22. **La valorisation ne charge que les colonnes qu'elle lit.** Estimer une
   voiture chargeait 400 lignes entieres, donc les photos et la charge brute
   de chaque comparable, deserialisees depuis JSON puis jetees : 1 200
   `json.loads` par estimation, 31 ms au lieu de 8.
23. **Une classe CSS ne se redefinit pas plus bas dans la feuille.** Les
   pages ajoutees reutilisaient `.card`, `.panel` et `.bar`, deja portees par
   la grille des affaires et la fiche d'annonce : le titre de la recherche
   sortait en petites capitales grises, les veilles s'empilaient au centre, et
   la barre de confiance changeait d'epaisseur sur une page que personne
   n'avait touchee.
24. **Un scan termine n'est plus un scan en cours.** L'etat courant reste
   renseigne apres la fin pour l'historique ; le lire comme "en cours"
   laissait la banniere affichee et tous les boutons grises pour de bon,
   c'est-a-dire un outil qui ne sert qu'une fois.
25. **Un rendu cote client ne veut pas dire des annonces absentes.** Une
   application React ne va pas chercher sa premiere page de resultats apres
   l'affichage : elle la serialise dans le HTML pour son hydratation.
   Mesure sur la forme servie par leboncoin : 0 lien d'annonce, 0 noeud
   schema.org, et pourtant les 6 annonces completes dans la premiere
   reponse HTTP, prix, kilometrage et code postal compris. Conclure "il faut
   un navigateur" coute trente fois le prix d'une requete, pour rien.
26. **Les objets d'annonce se reconnaissent a leur forme, pas a leur
   chemin.** `props.pageProps.searchData.ads[0]` est aussi fragile qu'un
   selecteur CSS. Une annonce, c'est une identite, un prix, et un
   kilometrage ou une annee ; une facette de filtre a un prix et un
   identifiant mais ni l'un ni l'autre. Et un conteneur qui porte les
   criteres de la recherche ressemble a une annonce : s'il est retenu, les
   vingt annonces qu'il contient disparaissent avec lui, sans erreur.
27. **Le nom du vendeur n'est pas le titre de l'annonce.** Aplatir un objet
   imbrique fait remonter `seller.name` en `name`, et `name` est un titre.
   Sur la forme La Centrale, ou aucune annonce ne porte de titre, les
   quatre annonces s'appelaient "Garage Martin" et "Groupe Rhone Auto", et
   la marque se perdait avec. L'identite d'un vendeur ne se lit qu'a son
   chemin complet.
28. **Une hybride n'est pas une electrique, meme quand son libelle dit
   "electrique".** leboncoin ecrit "Hybride essence/electrique" pour une
   hybride simple. Lue dans l'ordre naturel du tableau des carburants, une
   Yaris hybride sortait en electrique, donc valorisee sur la courbe de
   decote d'une batterie qu'elle n'a pas. Meme famille que l'invariant 12.
29. **Une URL de recherche fausse ne repond pas par une erreur.** Sur
   leparking, `/voitures-occasion/` au pluriel redirige sur l'accueil :
   HTTP 200, 170 Ko, une page pleine de liens, que rien ne distingue d'une
   recherche sans resultat. Le diagnostic a accuse le motif de lien
   pendant deux tours. Le site publiait pourtant la bonne forme dans le
   `SearchAction` de son JSON-LD : `/voiture-occasion/` au singulier. Une
   source qui ne rend rien se verifie d'abord sur ce que le site declare
   de lui-meme, avant de corriger quoi que ce soit chez nous.
30. **Un identifiant d'annonce n'est pas forcement un nombre.** leparking
   nomme les siennes `K5L7PC4Q` : huit caracteres, lettres et chiffres, sans
   separateur. Trois motifs de lien exigeant `\d{4,}` ont echoue d'affilee
   sur une page qui affichait 75 annonces, et la deduction automatique est
   restee muette pour la meme raison. Un identifiant, c'est ce qui distingue
   deux annonces, quel que soit son alphabet.
31. **Un diagnostic qui dit "ouvrir la page et relever la forme des URL" ne
   diagnostique rien.** La page fait 476 Ko. Elle connait pourtant ses
   propres familles d'URL : les compter et en donner un exemple chacune a
   resolu en un regard ce que trois tours de devinette n'avaient pas trouve.
   Un outil qui constate doit montrer ce qu'il a vu.
32. **Un site qui refuse la collecte ne refuse pas d'etre lu.** leboncoin
   repond 403 a une requete et un captcha a un navigateur sans tete : c'est
   non. Mais la personne qui cherche une Twingo regarde deja la page, que le
   site lui a servie volontairement. `sources/captured.py` lit ce qu'elle a
   ouvert, et n'a aucun code reseau : il ne peut pas acquerir une page tout
   seul, et c'est ce qui laisse cette voie ouverte quand les autres sont
   fermees. Payer un service pour imiter une empreinte TLS et faire tourner
   des adresses residentielles reste du contournement, sous-traite.
33. **Une extension pose ses pastilles dans la page de quelqu'un d'autre.**
   Tout y est prefixe `carexpert-` et rien n'est redefini globalement : une
   regle qui deborde casse la mise en page du site, et c'est nous que
   l'utilisateur accuse. Meme famille que l'invariant 23, a ceci pres que la
   feuille de style n'est plus la notre. Et c'est le service worker qui
   appelle CarExpert, jamais le script de contenu : celui-ci s'execute dans
   la page, donc sous la politique de securite du site, qui interdit
   d'appeler une adresse inconnue.
34. **L'accord entre deux comparables ne prouve rien.** Deux points
   s'alignent toujours. L'accord et l'extrapolation pesaient 55 % de la
   confiance contre 45 % a la taille de l'echantillon : assez pour qu'un
   seul comparable atteigne 0,44, au-dessus du seuil de verdict. Mesure sur
   une page de six Twingo consultee dans le navigateur : "A SAISIR, 12,4 %
   sous le marche", rendu sur deux annonces de la meme page. La confiance ne
   depasse donc jamais ce que la taille de l'echantillon autorise seule, ce
   qui place le premier verdict a huit comparables et ne touche pas un
   marche fourni.
35. **Un chiffre ne s'affiche que quand on le tient.** Une pastille grise
   "A ESTIMER" suivie de "12,4 % sous le marche" se lit comme une bonne
   affaire : le chiffre l'emporte sur le mot, et il vient de l'echantillon
   qu'on vient justement de juger trop maigre. Le refus de juger doit se
   dire, pas se contredire a la ligne suivante.
36. **Un badge commercial ne dit pas le carburant a lui seul.** Renault vend
   la Megane "E-Tech electrique" et la Clio "E-Tech hybride". En remontant
   HYBRID au-dessus d'ELECTRIC pour corriger l'invariant 28, le badge
   "e-tech" a fait basculer toutes les electriques Renault de grande serie
   en hybrides. Les mots explicites tranchent d'abord, les badges ensuite.
37. **Un serveur local ouvert a toutes les origines est ouvert a tout le
   web.** `carexpert serve` tourne en permanence pendant qu'on navigue, et
   le partage d'origine autorisait tout `https://` pour que l'extension
   puisse parler : n'importe quelle page visitee pouvait alors lire
   `/api/deals`, donc l'inventaire, les prix et les veilles. Seules les
   origines `chrome-extension://` et assimilees sont admises.
38. **Un `robots.txt` se lit comme la norme le dit, pas comme il parse.**
   `urllib.robotparser` se trompe dans les deux sens, mesure sur le fichier
   reel d'AutoScout24. Il bloque trop sur `?` : le site ecrit
   `Disallow: /lst?` pour viser la recherche a parametres, et la
   bibliotheque fait passer le motif par `urlunparse(urlparse(path))`, ce
   qui supprime la query vide et laisse `/lst`, prefixe qui interdit alors
   `/lst/volkswagen/golf`. Cette seule ligne a fait declarer la plus grosse
   source du projet interdite alors que le site l'autorise. Et il ne bloque
   pas assez sur les jokers : `Disallow: */util/*` est compare par
   `startswith`, or aucune URL ne commence par `*`. `sources/robots.py`
   applique RFC 9309 : jokers `*` et `$`, motif compare au chemin **et** a
   la query, regle la plus longue gagnante, `Allow` l'emportant a egalite.
39. **Un site peut nommer les robots d'IA pour les ecarter.** AutoScout24
   interdit tout a GPTBot, ClaudeBot, CCBot et Google-Extended, tout en
   autorisant le groupe general. Un robot nomme lit son groupe et ignore le
   general, meme plus permissif. CarExpert n'est aucun d'eux, mais
   l'intention du site se lit et se respecte avant d'augmenter le rythme.

## Collecte

Le `robots.txt` est respecte par defaut, une requete toutes les 2,5 secondes
par domaine, rien en parallele, et les protections anti-bot ne se contournent
pas. Voir `docs/02-sources-et-legal.md` avant de toucher a une source.

L'extraction se fait en trois paliers, du moins cher au plus cher : le
balisage `schema.org/Car` publie par les sites, puis le JSON que la page
embarque pour son hydratation (`sources/embedded.py`, ce qui rend leboncoin
et La Centrale lisibles sans navigateur), puis un rendu Chromium qui ne
demarre que si les deux premiers reviennent vides. Jamais de selecteurs CSS.
Une nouvelle source s'ajoute par un fichier YAML dans
`src/carexpert/sources/sites/`, sans code Python.

## Structure

```
src/carexpert/
  sources/     collecte, adaptateurs YAML, extraction, diagnostic
  normalize/   vocabulaire commun, signaux du texte
  valuation/   comparables, courbes de decote, estimation
  expert/      appel Claude, base de faiblesses connues, photos, cout
  scoring/     score explique
  alerts/      veilles et notifications
  api/ web/    tableau de bord et API JSON
extension/     compagnon Chrome: annote les annonces que vous consultez
```

## Tests

Tout tourne hors ligne. Le marche synthetique (`sources/demo.py`) contient de
vraies affaires et des pieges connus, ce qui permet de mesurer la qualite du
classement et pas seulement l'absence d'erreur. Les appels reseau sont testes
contre un serveur HTTP local, jamais contre un site reel.
