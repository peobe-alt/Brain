#!/bin/bash
# Double-cliquez sur ce fichier pour ouvrir CarExpert. (macOS et Linux)
#
# La premiere fois, macOS peut refuser de l'executer. Dans ce cas: clic droit
# sur le fichier, "Ouvrir", puis "Ouvrir" a nouveau dans la fenetre qui
# s'affiche. Une seule fois, ensuite le double-clic suffit.

cd "$(dirname "$0")/.." || exit 1

if ! command -v python3 >/dev/null 2>&1; then
  echo
  echo "  Python 3 n'est pas installe sur cet ordinateur."
  echo "  Telechargez-le sur https://www.python.org/downloads/ puis reessayez."
  echo
  read -r -p "  Appuyez sur Entree pour fermer."
  exit 1
fi

if ! python3 -c "import carexpert" >/dev/null 2>&1; then
  echo "  Premiere ouverture: installation en cours, comptez une minute..."
  python3 -m pip install --quiet --upgrade pip
  python3 -m pip install --quiet -e ".[photos]" || {
    echo
    echo "  L'installation a echoue. Envoyez le message ci-dessus pour diagnostic."
    read -r -p "  Appuyez sur Entree pour fermer."
    exit 1
  }
fi

python3 -m carexpert.cli serve
