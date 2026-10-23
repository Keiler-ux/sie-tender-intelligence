"""Chaque page de l'interface s'affiche sans erreur sur une affaire fictive."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "scripts"))

PAGES = ["affaires", "fiche", "analyse", "assistant", "quantites", "cotations", "dpgf", "memoire", "controle", "archives"]


@pytest.fixture(scope="module")
def environnement(tmp_path_factory):
    from generer_donnees_fictives import generer

    from sie_tender.affaire import EspaceTravail
    from sie_tender.f3_quantites import importer_quantites
    from sie_tender.f6_archives import Bibliotheque
    from sie_tender.models import CitationIA, ReponseIA
    from sie_tender.pipeline import analyser_affaire

    d = tmp_path_factory.mktemp("app")
    generer(d / "exemples")
    Bibliotheque.indexer(d / "exemples" / "archives_fictives").sauver(d / "index.json")
    a = EspaceTravail(d / "affaires").creer()
    for f in (d / "exemples" / "dce_fictif_valbourg").iterdir():
        a.deposer("dce", f.name, f.read_bytes())
    analyser_affaire(a, Bibliotheque.charger(d / "index.json"))
    a.c.quantites, _ = importer_quantites(d / "exemples" / "chiffrage_fictif" / "metre_lot02.xlsx")
    a.c.reponses_ia.append(ReponseIA(type="question", question="Date ?", texte="27 novembre", moteur="test",
                                     citations=[CitationIA(fichier="01_RC_Valbourg.pdf", page=2, extrait="x", verifiee=False)]))
    a.sauver()
    return d, a.id


@pytest.mark.parametrize("page", PAGES)
def test_page(environnement, monkeypatch, page):
    d, aid = environnement
    for k, v in {"SIE_DATA": str(d), "SIE_ESPACE": str(d / "affaires"), "SIE_INDEX": str(d / "index.json"),
                 "SIE_EXEMPLES": "1", "SIE_LLM": "aucun"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("SIE_MOT_DE_PASSE", raising=False)
    at = AppTest.from_file(str(RACINE / "app.py"), default_timeout=120)
    at.session_state["affaire"] = aid
    at.session_state["_page_test"] = page
    at.run()
    assert not at.exception, [e.value for e in at.exception]
