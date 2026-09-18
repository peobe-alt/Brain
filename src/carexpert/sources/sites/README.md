# Adaptateurs de sites

Un fichier YAML = une source. Aucun code Python n'est necessaire pour en
ajouter une.

## Champs

| Champ | Role |
|---|---|
| `name` | identifiant interne, utilise par `--source` |
| `countries` | pays couverts, sert au filtrage des recherches |
| `search_url` | gabarit d'URL de recherche, variables entre accolades |
| `listing_link_pattern` | expression reguliere reconnaissant une URL d'annonce |
| `request_delay` | secondes entre deux requetes sur ce domaine |
| `requires_js` | `true` autorise un rendu navigateur si la page revient vide |
| `render` | reglages du rendu : `escalate`, `wait_selector`, `wait_ms`, `wait_until` |
| `selectors` | secours CSS quand le schema.org est absent |
| `verified` | `false` tant que le gabarit n'a pas ete teste en conditions reelles |

Variables disponibles dans `search_url` : `{make} {model} {keywords}
{price_min} {price_max} {year_min} {year_max} {km_max} {postcode} {radius}
{page}`. Les variables vides sont retirees de l'URL automatiquement.

## La methode qui marche toujours

Les gabarits d'URL changent. Plutot que de les deviner :

1. construire la recherche dans l'interface du site, avec ses filtres ;
2. copier l'URL ;
3. `carexpert scan --source autoscout24 --url "<URL collee>"`.

L'extraction, elle, ne depend pas de l'URL : elle lit le balisage
schema.org que les sites publient pour le referencement.

## Sites "rendus en JavaScript"

`requires_js: true` **autorise** le navigateur, il ne l'impose pas. La
collecte tente toujours une requete simple d'abord, et ne demarre un rendu
que si la page revient sans annonces. Sur leboncoin et La Centrale, elle
n'en demarre jamais : leurs annonces sont dans la premiere reponse HTTP,
dans le JSON que la page hydrate. Voir `docs/02-sources-et-legal.md`.

```yaml
requires_js: true
render:
  escalate: true                       # false force le rendu a chaque page
  wait_selector: "[data-test='card']"  # attendre cet element plutot qu'un delai
  wait_ms: 3500
```

Le rendu demande l'extra correspondant, une fois :

```bash
pip install -e ".[browser]" && python -m playwright install chromium
```

## Avant d'activer une source

Lire `docs/02-sources-et-legal.md`. En resume : respecter le `robots.txt`,
garder un rythme lent, ne pas contourner une protection anti-bot, et
preferer une API officielle ou un accord de donnees des que l'usage depasse
la veille personnelle.
