#!/bin/sh
set -e
# En ligne, un mot de passe est obligatoire.
if [ -z "$SIE_MOT_DE_PASSE" ] && [ "$SIE_SANS_MOT_DE_PASSE" != "1" ]; then
  echo "Erreur : définir SIE_MOT_DE_PASSE (ou SIE_SANS_MOT_DE_PASSE=1 pour un usage strictement local)." >&2
  exit 1
fi
# Mode démo : données fictives et archives indexées au premier démarrage.
if [ "$SIE_DEMO" = "1" ] && [ ! -f "$SIE_INDEX" ]; then
  python scripts/generer_donnees_fictives.py "${SIE_DATA:-/data}/exemples"
  python -m sie_tender indexer "${SIE_DATA:-/data}/exemples/archives_fictives" --index "$SIE_INDEX"
fi
exec streamlit run app.py --server.port "$PORT" --server.address 0.0.0.0 --server.headless true \
  --browser.gatherUsageStats false --server.maxUploadSize 300
