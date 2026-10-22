#!/bin/bash
# Lance l'outil SIE sur ce Mac (double-clic). Premier lancement : quelques minutes d'installation.
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3.11 ou plus récent est nécessaire : https://www.python.org/downloads/"
  read -r -p "Appuyez sur Entrée pour fermer."
  exit 1
fi
if [ ! -x .venv/bin/python ]; then
  echo "Installation des dépendances..."
  python3 -m venv .venv && .venv/bin/python -m pip install --upgrade pip >/dev/null \
    && .venv/bin/python -m pip install -r requirements.txt || { read -r -p "L'installation a échoué. Entrée pour fermer."; exit 1; }
fi
echo "L'outil s'ouvre dans le navigateur. Fermez cette fenêtre pour l'arrêter."
SIE_EXEMPLES=1 exec .venv/bin/python -m streamlit run app.py --browser.gatherUsageStats false
