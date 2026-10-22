"""Tests sur les données fictives : chaque fonction F1 à F7 et la règle « rien d'inventé »."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "scripts"))

from generer_donnees_fictives import generer  # noqa: E402

from sie_tender.affaire import EspaceTravail  # noqa: E402
from sie_tender.f1_fiche import construire_fiche  # noqa: E402
from sie_tender.f3_quantites import importer_quantites  # noqa: E402
from sie_tender.f4_cotations import generer_demandes  # noqa: E402
from sie_tender.f5_dpgf import associer, construire_dpgf, importer_devis  # noqa: E402
from sie_tender.f6_archives import Bibliotheque  # noqa: E402
from sie_tender.f7_controle import controler, exporter_dossier  # noqa: E402
from sie_tender.ingestion import importer_dce  # noqa: E402
from sie_tender.models import CategorieExigence, Page, Piece, Statut, TypePiece  # noqa: E402
from sie_tender.pipeline import analyser_affaire, analyser_dce  # noqa: E402


@pytest.fixture(scope="session")
def donnees(tmp_path_factory):
    d = tmp_path_factory.mktemp("donnees")
    generer(d)
    return d


@pytest.fixture(scope="session")
def consultation(donnees):
    return analyser_dce(donnees / "dce_fictif_valbourg", Bibliotheque.indexer(donnees / "archives_fictives"),
                        aujourdhui=date(2026, 10, 22))


def test_classement_des_pieces(donnees):
    pieces = {p.fichier: p for p in importer_dce(donnees / "dce_fictif_valbourg")}
    assert pieces["01_RC_Valbourg.pdf"].type == TypePiece.RC
    assert pieces["03_CCTP_lot02_electricite.pdf"].type == TypePiece.CCTP
    assert pieces["03_CCTP_lot02_electricite.pdf"].lot == "2"
    assert pieces["07_plan_RDC_lot02.dwg"].lisible is False


def test_f1_fiche(consultation):
    f = consultation.fiche
    assert f.reference.valeur == "VAL-2026-014"
    assert "27 novembre 2026" in f.date_limite_remise.valeur
    assert f.visite_obligatoire.valeur == "oui"
    assert f.date_limite_remise.source.page == 2
    assert [l.numero for l in consultation.lots] == ["1", "2", "3", "4"]
    assert f.decision is None  # jamais décidé par l'outil


def test_f1_donnee_absente_marquee_a_verifier():
    piece = Piece(fichier="RC.pdf", type=TypePiece.RC, pages=[Page(numero=1, texte="Règlement de la consultation. Objet : travaux.")])
    fiche, _ = construire_fiche([piece])
    assert fiche.date_limite_remise.valeur is None
    assert fiche.date_limite_remise.statut == Statut.A_VERIFIER


def test_f2_exigences_sourcees(consultation):
    rse = [e for e in consultation.exigences if e.categorie == CategorieExigence.RSE]
    assert any("70 heures" in e.texte for e in rse)
    assert any(e.categorie == CategorieExigence.INTERFACE and e.lot == "4" for e in consultation.exigences)
    assert all(e.source.fichier and e.source.page for e in consultation.exigences)
    total = sum(c.ponderation for c in consultation.criteres)
    assert total == 100
    assert len(consultation.criteres[1].sous_criteres) == 4


def test_f6_trame_et_validite(consultation):
    titres = [s.titre for s in consultation.trame_memoire]
    assert "Méthodologie d'intervention en site occupé" in titres
    site = next(s for s in consultation.trame_memoire if s.titre.startswith("Méthodologie"))
    assert site.passages_proposes[0].affaire == "2024_ecole_maternelle_fictive_Orvault"
    assurance = next(p for p in consultation.pieces_a_fournir if "assurance" in p.intitule.lower())
    assert assurance.statut == Statut.A_VERIFIER  # attestation d'archive périmée


def test_f3_a_f7(donnees, tmp_path):
    esp = EspaceTravail(tmp_path / "affaires")
    a = esp.creer()
    for f in (donnees / "dce_fictif_valbourg").iterdir():
        a.deposer("dce", f.name, f.read_bytes())
    analyser_affaire(a, Bibliotheque.indexer(donnees / "archives_fictives"))
    c = a.c

    lignes, _ = importer_quantites(donnees / "chiffrage_fictif" / "metre_lot02.xlsx")
    assert len(lignes) == 9
    assert sum(l.statut == Statut.A_VERIFIER for l in lignes) == 1
    c.quantites = lignes

    c.demandes_cotation = generer_demandes(c)
    assert c.demandes_cotation and c.demandes_cotation[0].echeance_retour == "17/11/2026"

    for f in ("devis_elec_distribution_ouest.xlsx", "devis_lumiere_plus.pdf"):
        d = importer_devis(donnees / "chiffrage_fictif" / f, lot="2")
        associer(c, d)
        c.devis.append(d)
    c.dpgf = construire_dpgf(c)
    led = next(p for p in c.dpgf if p.code == "2.1")
    assert led.prix_materiel == 39.9 and led.prix_unitaire_vente is None  # pas de coefficients SIE
    c.coefficients.taux_horaire, c.coefficients.frais_generaux, c.coefficients.aleas, c.coefficients.marge = 50, 10, 0, 10
    c.dpgf = construire_dpgf(c)
    led = next(p for p in c.dpgf if p.code == "2.1")
    assert led.prix_unitaire_vente == round(39.9 * 1.1 * 1.1, 2)

    constats = controler(a, aujourdhui=date(2026, 11, 1))
    assert any(k.gravite == "bloquant" and k.objet == "Décision" for k in constats)
    zip_path = exporter_dossier(a)
    assert zip_path.exists()


def test_dce_de_test_varies(tmp_path):
    from generer_dce_test import generer as generer_tests
    zips = {z.stem: z for z in generer_tests(tmp_path)}

    c = analyser_dce(zips["dce_college_lot_unique"], aujourdhui=date(2026, 10, 22))
    assert c.fiche.date_limite_remise.valeur == "15/12/2026 à 16h00"
    assert c.fiche.visite_obligatoire.valeur == "non"
    assert [l.intitule for l in c.lots] == ["Lot unique"]

    c = analyser_dce(zips["dce_hopital_3_lots"], aujourdhui=date(2026, 10, 22))
    assert len(c.lots) == 3 and c.fiche.visite_obligatoire.valeur == "oui"
    assert c.fiche.objet.valeur.endswith("du bâtiment B (médecine et SSR)")  # ligne coupée recollée

    c = analyser_dce(zips["dce_bailleur_incomplet"], aujourdhui=date(2026, 10, 22))
    assert c.fiche.date_limite_remise.statut == Statut.A_VERIFIER and c.fiche.date_limite_remise.valeur is None
    assert all(cr.statut == Statut.A_VERIFIER for cr in c.criteres)  # 60 + 30 = 90 %
