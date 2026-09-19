# CarExpert, l'extension

Marque les bonnes affaires sur les pages d'annonces que **vous** consultez.

Elle ne collecte rien. Elle lit la page que le site vous a servie, l'envoie a
CarExpert qui tourne sur votre machine, et repose un verdict par annonce sur
les cartes de la page. Sans page ouverte par vous, il n'y a rien a lire :
c'est ce qui laisse cette voie ouverte sur les sites qui refusent la collecte
automatisee.

## Installation, une fois

1. Lancer CarExpert :

   ```bash
   carexpert serve
   ```

2. Ouvrir `chrome://extensions` dans Chrome.
3. Activer **Mode developpeur**, en haut a droite.
4. **Charger l'extension non empaquetee**, et choisir ce dossier
   (`extension/`).

C'est tout. Pas de magasin, pas de validation, pas de compte.

## Usage

Cherchez normalement sur leboncoin, La Centrale, leparking, AutoScout24 ou
Ouest-France Auto. Les pastilles apparaissent seules sur la page de
resultats :

| Pastille | Sens |
|---|---|
| **A SAISIR** (vert) | sous le marche, et rien de suspect dans l'annonce |
| **A VERIFIER** (orange) | interessante, mais un point a controler |
| **A FUIR** (rouge) | au-dessus du marche, ou un signal grave dans le texte |
| **A ESTIMER** (gris) | pas assez de comparables pour situer ce prix |

Le gris n'est pas un echec : sans comparables, CarExpert refuse d'inventer
une cote (invariant 7). Plus vous parcourez d'annonces d'un meme modele, plus
le vivier se remplit et plus les verdicts se precisent.

Le bouton de la barre d'outils relance l'analyse a la main, et permet de
changer l'adresse du serveur si vous ne tournez pas sur le port par defaut.

## Si rien ne s'affiche

Une banniere en bas a droite dit toujours ce qui s'est passe.

**« CarExpert injoignable »** : le serveur n'est pas lance, ou pas sur
l'adresse indiquee. `carexpert serve`, puis verifiez l'adresse dans le
bouton de l'extension.

**« Aucune annonce reconnue »** : le site a change la forme de son etat
embarque. Enregistrez la page (Fichier > Enregistrer sous) et regardez avec
`carexpert import page.html --verbose`.

## Ce qu'elle ne fait pas

Elle ne vous reveille pas parce qu'une annonce vient d'etre publiee. Une page
consultee est un instantane. Pour la veille, utilisez les alertes natives du
site : elles vous previennent, et l'extension juge ce qu'elles remontent.

Elle n'envoie rien ailleurs que sur votre machine. Le serveur est le votre,
la base de donnees est la votre.
