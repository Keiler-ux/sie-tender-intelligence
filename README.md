# SIE Tender Intelligence

Outil d'aide à la réponse aux appels d'offres pour SIE (projet commande entreprise, IMT Atlantique, 2026).
Il couvre le parcours complet du cahier des charges, du dépôt du DCE au dossier prêt à relire.

| Étape | Fonction | Module |
| --- | --- | --- |
| Import du DCE, classement des pièces | | `sie_tender/ingestion.py` |
| Fiche de renseignement GO / NO GO | F1 | `sie_tender/f1_fiche.py` |
| Exigences, critères, RSE, interfaces, pièces à fournir | F2 | `sie_tender/f2_exigences.py` |
| Import du métré Excel du bureau d'études | F3 | `sie_tender/f3_quantites.py` |
| Brouillons de demandes de cotation | F4 | `sie_tender/f4_cotations.py` |
| Devis, association aux postes, DPGF provisoire | F5 | `sie_tender/f5_dpgf.py` |
| Archives, recherche, trame du mémoire | F6 | `sie_tender/f6_archives.py` |
| Contrôle final et export du dossier | F7 | `sie_tender/f7_controle.py` |
| Modèle de données commun | | `sie_tender/models.py` |
| Interface | | `app.py` (Streamlit) |

## Règles de conception

- **Rien n'est inventé.** Chaque information extraite porte sa source (fichier, page, article). Une donnée absente est marquée « à vérifier ».
- **SIE décide.** GO / NO GO, lots retenus, fournisseurs, prix, temps de pose, frais, aléas et marges sont saisis par SIE. Sans ces coefficients, le prix de vente reste vide.
- **Confidentialité.** Par défaut, aucun LLM n'est utilisé et tout tourne en local. Une API externe est bloquée tant que `SIE_LLM_EXTERNE_AUTORISE=oui` n'est pas défini, ce qui ne doit être fait qu'avec l'accord écrit de SIE. Aucun document SIE ne doit être ajouté à ce dépôt (voir `.gitignore`).
- **Rien n'est envoyé.** Les demandes de prix et le dossier final sont des fichiers à relire ; l'envoi et le dépôt restent manuels.

## Installation

```bash
python -m venv .venv && source .venv/bin/activate   # Windows : .venv\Scripts\activate
pip install -r requirements.txt
```

OCR des PDF scannés (facultatif) : installer Tesseract avec la langue française (`tesseract-ocr` et `tesseract-ocr-fra`).

## Essai rapide sur les données fictives

```bash
python scripts/generer_donnees_fictives.py exemples     # DCE, archives, métré et devis inventés
python scripts/generer_dce_test.py exemples/dce_test      # 3 autres DCE : lot unique, 3 lots, DCE incomplet
python scripts/demo_bout_en_bout.py exemples            # parcours F1 à F7 en ligne de commande
python scripts/evaluer.py                                # qualité de l'extraction et temps, contre scripts/attendus/
python -m pytest -q                                      # tests
```

## Interface

```bash
python -m sie_tender indexer exemples/archives_fictives --index archives_index.json
streamlit run app.py
```

Puis dans le navigateur : « Affaires » pour déposer un DCE (ou créer une affaire de démonstration), puis les étapes 1 à 7 dans l'ordre. Chaque page propose l'étape suivante en bas.

Parcours prévu par le cahier des charges :

- **Rôles** (barre latérale) : Direction, Bureau d'études, Chargé d'affaires, Responsable technique, Dossier final. Seule la direction enregistre le GO / NO GO ; chaque validation garde le nom et le rôle (historique sur la fiche et au contrôle).
- **Fiche (F1)** : tous les lots avec les activités SIE repérées (CFO, CFA, SSI, CVC, plomberie, désenfumage), autres intervenants (BET, contrôleur technique, SPS, OPC). Les lots ne se cochent qu'après GO ; le collaborateur chargé de la visite et la date de visite se saisissent alors. Export dans le modèle Excel de SIE : balises `{{reference}}`, `{{objet}}`, `{{decision}}`, `{{responsable_visite}}`… et une ligne `{{lot_numero}} {{lot_intitule}} {{lot_retenu}}` répétée pour chaque lot.
- **Analyse (F2)** : matrice d'interfaces lot × lot tirée des CCTP de tous les lots et des pièces communes, synthèse des seuls lots retenus (prestations, exigences, risques, interfaces, RSE dont heures d'insertion, questions) exportable en Word.
- **Mémoire (F6)** : si le DCE contient un cadre de réponse, ses chapitres forment le plan, dans l'ordre ; sinon les critères du RC. Les passages repris gardent leur source, les références d'affaires comparables se choisissent, les pièces jointes (planning, notes de calcul, synoptiques) s'ajoutent, et la liste des compléments à fournir s'exporte en Word.

## Version téléchargeable (gratuite)

Chaque étiquette `v*` poussée sur GitHub publie une archive dans l'onglet **Releases** du dépôt (workflow `release.yml`).

```bash
git tag v0.2.0 && git push --tags   # ou : onglet Actions > Version téléchargeable > Run workflow
```

Pour l'utilisateur : installer Python 3.11+, décompresser l'archive, puis double-cliquer sur `lancer_windows.bat` (Windows) ou `lancer_mac.command` (Mac ; si macOS bloque le fichier : clic droit, Ouvrir). Le premier lancement installe les dépendances. Les données restent sur l'ordinateur, dans le dossier `affaires/`.

## Version web (gratuite, Streamlit Community Cloud)

1. Se connecter sur [share.streamlit.io](https://share.streamlit.io) avec le compte GitHub propriétaire du dépôt.
2. **Create app** → dépôt `Keiler-ux/sie-tender-intelligence`, branche `main`, fichier `app.py`.
3. **Advanced settings** → Python 3.12, et dans **Secrets** :
   ```toml
   SIE_MOT_DE_PASSE = "un-mot-de-passe-long"
   SIE_DEMO = "1"
   ```
4. **Deploy**. Dans les réglages de l'application, garder la visibilité privée et inviter les membres de l'équipe.

À savoir : le stockage y est temporaire (les affaires disparaissent au redémarrage) et les fichiers sont hébergés chez Streamlit. Cette version est une démonstration : **aucun document SIE** sans l'accord écrit de SIE.

## Docker (serveur SIE ou autre hébergeur)

```bash
docker build -t sie-tender .
docker run -p 8501:8501 -v sie_donnees:/data -e SIE_MOT_DE_PASSE=... sie-tender
```

`SIE_DEMO=1` ajoute les données fictives. Les affaires sont conservées dans le volume `/data`.

## Ligne de commande

```bash
python -m sie_tender analyser chemin/vers/dce.zip --index archives_index.json --sortie resultats/
python -m sie_tender chercher "intervention en site occupé" --index archives_index.json
```

## IA (facultative)

L'outil fonctionne sans IA. Quand elle est activée, elle ajoute :

- **Assistant IA** : synthèse du DCE pour la décision GO / NO GO, et questions libres sur le DCE ;
- **Fiche** : complément des champs restés « à vérifier » ;
- **Mémoire** : brouillon de section à partir des passages d'archives et des exigences du DCE, avec des `[À COMPLÉTER]` là où l'information manque (une section ne peut pas être validée tant qu'il en reste).

Chaque réponse cite les pages du DCE utilisées, et chaque citation est recherchée mot pour mot dans les pièces : celles qu'on ne retrouve pas sont signalées « à vérifier ».

| Moteur | Réglages (variables d'environnement ou secrets Streamlit) | À savoir |
| --- | --- | --- |
| Aucun (défaut) | — | règles uniquement |
| Claude | `ANTHROPIC_API_KEY`, `SIE_LLM_EXTERNE_AUTORISE = "oui"`, `SIE_LLM = "anthropic"` ; facultatif : `SIE_LLM_MODELE` (défaut `claude-opus-5-5`), `SIE_LLM_EFFORT` (`low`, `medium`, `high`), `ANTHROPIC_WORKSPACE_ID` (si la clé n'est rattachée à aucun espace de travail) | service externe et payant (clé sur console.anthropic.com, facturée à part de l'abonnement Claude). Pour chaque affaire, l'utilisateur doit confirmer que le DCE est fictif, public ou couvert par un accord écrit de SIE. Coût estimé affiché après chaque réponse. |
| Ollama | `SIE_LLM = "ollama"`, `SIE_LLM_MODELE` (ex. `mistral`) | gratuit, rien ne sort du poste ; version téléchargeable uniquement, PC avec 16 Go de RAM conseillés |

Le moteur se choisit aussi dans la barre latérale (« Configurer l'IA ») parmi ceux qui sont disponibles.

## Limites connues

- Les règles d'extraction ont été mises au point sur un DCE fictif : elles devront être ajustées sur les consultations historiques de SIE.
- La recherche dans les archives est lexicale (BM25). Une recherche par vecteurs pourra être ajoutée si les essais le justifient.
- L'association devis / postes est une proposition à valider ; la lecture des devis PDF ne gère que des lignes simples.
- Le format de la fiche suit les champs du cahier des charges ; l'export au modèle SIE attend que SIE fournisse son fichier.
- Le score de `scripts/evaluer.py` sur les DCE fictifs ne mesure rien de réel (les règles ont été écrites sur ces DCE). Il servira sur les consultations historiques de SIE, avec des attendus écrits à la main et gardés hors du dépôt.
- Les rôles ne sont pas des comptes : chacun choisit son rôle dans la barre latérale. Une vraie authentification par utilisateur reste à faire si SIE l'utilise en production.
