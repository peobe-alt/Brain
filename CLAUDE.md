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

## Collecte

Le `robots.txt` est respecte par defaut, une requete toutes les 2,5 secondes
par domaine, rien en parallele, et les protections anti-bot ne se contournent
pas. Voir `docs/02-sources-et-legal.md` avant de toucher a une source.

L'extraction repose sur le balisage `schema.org/Car` publie par les sites,
pas sur des selecteurs CSS. Une nouvelle source s'ajoute par un fichier YAML
dans `src/carexpert/sources/sites/`, sans code Python.

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
```

## Tests

Tout tourne hors ligne. Le marche synthetique (`sources/demo.py`) contient de
vraies affaires et des pieges connus, ce qui permet de mesurer la qualite du
classement et pas seulement l'absence d'erreur. Les appels reseau sont testes
contre un serveur HTTP local, jamais contre un site reel.
