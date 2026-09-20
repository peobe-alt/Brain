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
25. **Un echantillon trop maigre pour son palier ne vaut pas une estimation
   fragile : il n'en vaut aucune.** Chaque palier de comparables porte son
   propre seuil (3 en `strict`, 12 en `modele_large`) et, en dessous,
   `comps_count` vaut zero : pas de prix de marche, pas d'ecart, pas de
   gain, verdict `unknown`. Mesure : une Golf 7 1.6 TDI de 255 000 km a
   5 900 EUR annoncee "A SAISIR, 45% sous le marche, +4 749 EUR" sur trois
   comparables - une e-Golf electrique, un break TDI, une GTE hybride.
26. **Les familles d'energie ne se melangent a aucun palier.** Melanger une
   essence et un diesel au palier large est une approximation ; y melanger
   une electrique ou une hybride rechargeable n'en est pas une. Mesure :
   e-Golf de 163 000 km a 8 490 EUR, "8% au-dessus du marche, A FUIR", sur
   un echantillon de Golf 1.6 TDI.
27. **Ce que l'interface affiche doit tenir sans l'etiquette.** Un bandeau
   qui dit "A ESTIMER" puis "45% sous le marche, estime a 14 248 EUR" est lu
   comme un prix de marche : c'est le chiffre qu'on retient. Sans estimation,
   on affiche ce qui manque, en nombres, et le score lui-meme disparait -
   un score sans position prix n'est pas une note d'affaire.
28. **Une page capturee dans le navigateur ne se rejoue jamais vers le
   site.** C'est toute la raison d'etre de l'extension : la requete a deja
   eu lieu. Une requete de plus se verrait dans les journaux du site et
   retomberait sur les protections que le projet ne contourne pas.
29. **Un serveur local est joignable par n'importe quelle page du
   navigateur.** Un site peut poster vers 127.0.0.1 depuis son propre
   JavaScript et la requete part, meme si la reponse lui reste illisible.
   Les origines sont donc filtrees sur le domaine complet - jamais sur
   "contient", sinon `autoscout24.pirate.example` passe pour AutoScout24.
30. **"A fuir" ne se dit pas d'une voiture ordinaire.** Le verdict le plus
   fort doit reposer sur quelque chose de retenu contre elle, pas sur
   l'absence de merite. Mesure : avec le seuil a 55, 61 des 215 annonces
   correctes du marche synthetique sortaient "A FUIR", dont une Golf de
   2024 a 4% au-dessus du marche, etat 88/100, coupable d'avoir trois
   photos. Le plancher est le score neutre, 45.
31. **Une panne de l'extension s'affiche sur le site du vendeur.** Ce n'est
   pas un terminal: "CarExpert a repondu 500" au-dessus des annonces ne dit
   ni ce qui se passe ni quoi faire. Tout echec inattendu de la capture
   repond une phrase lisible, la trace restant cote serveur. Meme raison
   que l'invariant 5, autre surface.
32. **Rien de lisible au premier passage veut souvent dire "pas encore".**
   Ces sites affichent un bandeau de consentement avant leurs annonces, et
   la page reste vide tant qu'il est la. Une seule reprise, trois secondes
   plus tard, evite d'avoir a cliquer soi-meme; au-dela ce serait une
   boucle.
33. **Un motif de correspondance invalide fait refuser l'extension
   entiere.** `*://*.autoscout24.*/*` n'existe pas : le joker ne vaut que
   pour l'hote entier ou en tete de domaine. Les domaines s'enumerent, et un
   test verifie que chaque site connu de CarExpert est bien suivi.

## Collecte

Le `robots.txt` est respecte par defaut, une requete toutes les 2,5 secondes
par domaine, rien en parallele, et les protections anti-bot ne se contournent
pas. Voir `docs/02-sources-et-legal.md` avant de toucher a une source.

L'extraction repose sur le balisage `schema.org/Car` publie par les sites,
pas sur des selecteurs CSS. Une nouvelle source s'ajoute par un fichier YAML
dans `src/carexpert/sources/sites/`, sans code Python - plus, si le site doit
etre suivi par l'extension, ses domaines dans le manifest.

L'autre voie de collecte ne demande rien au site : l'extension lit la page
que l'utilisateur a deja ouverte (`sources/capture.py`), ce qui donne acces
aux sites rendus en JavaScript sans une requete de plus. Voir
`docs/05-extension.md`.

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
  extension/   extension navigateur (MV3), servie par le tableau de bord
```

## Tests

Tout tourne hors ligne. Le marche synthetique (`sources/demo.py`) contient de
vraies affaires et des pieges connus, ce qui permet de mesurer la qualite du
classement et pas seulement l'absence d'erreur. Les appels reseau sont testes
contre un serveur HTTP local, jamais contre un site reel.
