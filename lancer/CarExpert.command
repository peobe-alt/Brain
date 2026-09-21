#!/bin/bash
# Double-cliquez sur ce fichier pour ouvrir CarExpert. (macOS et Linux)
#
# Sur Mac, macOS bloque tout fichier telecharge depuis internet, quel que
# soit son contenu, et sur les versions recentes le "clic droit > Ouvrir" ne
# suffit plus. Le chemin qui marche partout, une fois pour toutes:
#
#   1. ouvrez Terminal (Spotlight, tapez "Terminal");
#   2. tapez  bash  suivi d'un espace;
#   3. glissez ce fichier dans la fenetre Terminal;
#   4. Entree.
#
# Pour que le double-clic marche ensuite: Reglages Systeme > Confidentialite
# et securite > descendez jusqu'au message qui parle de CarExpert.command >
# "Ouvrir quand meme".

cd "$(dirname "$0")/.." || exit 1

arreter() {
  echo
  read -r -p "  Appuyez sur Entree pour fermer."
  exit 1
}

PYTHON=""
for candidat in python3 python3.12 python3.11 python; do
  if command -v "$candidat" >/dev/null 2>&1; then PYTHON="$candidat"; break; fi
done

if [ -z "$PYTHON" ]; then
  echo
  echo "  Python 3 n'est pas installe sur cet ordinateur."
  echo "  Telechargez-le sur https://www.python.org/downloads/ puis reessayez."
  arreter
fi

# Tout s'installe dans un dossier .venv a cote du programme, jamais dans le
# Python du systeme. Sans cela, les Python installes par Homebrew ou fournis
# avec Linux refusent l'installation ("externally-managed-environment"), et
# le lanceur s'arretait sur un message que personne ne peut interpreter.
if [ ! -x ".venv/bin/python" ]; then
  echo "  Premiere ouverture: installation en cours, comptez une minute..."
  "$PYTHON" -m venv .venv || {
    echo
    echo "  La creation de l'environnement a echoue."
    echo "  Sur Ubuntu/Debian: sudo apt install python3-venv, puis reessayez."
    arreter
  }
fi

VENV=".venv/bin/python"

if ! "$VENV" -c "import carexpert" >/dev/null 2>&1; then
  echo "  Installation des composants, comptez une minute..."
  "$VENV" -m pip install --quiet --upgrade pip
  "$VENV" -m pip install --quiet -e ".[photos]" || {
    echo
    echo "  L'installation a echoue. Envoyez le message ci-dessus pour diagnostic."
    arreter
  }
fi

"$VENV" -m carexpert.cli serve
