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
python scripts/demo_bout_en_bout.py exemples            # parcours F1 à F7 en ligne de commande
python -m pytest -q                                      # tests
```

## Interface

```bash
python -m sie_tender indexer exemples/archives_fictives --index archives_index.json
streamlit run app.py
```

Puis dans le navigateur : « Affaires » pour déposer un DCE, puis les étapes 1 à 7 dans l'ordre.

## Ligne de commande

```bash
python -m sie_tender analyser chemin/vers/dce.zip --index archives_index.json --sortie resultats/
python -m sie_tender chercher "intervention en site occupé" --index archives_index.json
```

## LLM (facultatif)

| `SIE_LLM` | Effet |
| --- | --- |
| `aucun` (défaut) | règles uniquement |
| `ollama` | modèle local via Ollama (`SIE_LLM_MODELE=mistral` par exemple), rien ne sort de la machine |
| `anthropic` | API externe, bloquée sans `SIE_LLM_EXTERNE_AUTORISE=oui` |

Le LLM ne sert qu'à compléter les champs de la fiche restés vides. Chaque réponse doit citer un extrait, qui est recherché dans la pièce : s'il n'y est pas, le champ reste « à vérifier ».

## Limites connues

- Les règles d'extraction ont été mises au point sur un DCE fictif : elles devront être ajustées sur les consultations historiques de SIE.
- La recherche dans les archives est lexicale (BM25). Une recherche par vecteurs pourra être ajoutée si les essais le justifient.
- L'association devis / postes est une proposition à valider ; la lecture des devis PDF ne gère que des lignes simples.
- Le format de la fiche suit les champs du cahier des charges, à aligner sur le modèle exact de SIE.
