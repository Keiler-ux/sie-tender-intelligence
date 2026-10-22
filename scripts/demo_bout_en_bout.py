"""Scénario de démonstration complet sur les données fictives (F1 à F7).

    python scripts/generer_donnees_fictives.py exemples
    python scripts/demo_bout_en_bout.py exemples

Les décisions et coefficients saisis ici simulent ce que ferait SIE.
"""
from __future__ import annotations

import shutil
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sie_tender.affaire import EspaceTravail  # noqa: E402
from sie_tender.f3_quantites import importer_quantites  # noqa: E402
from sie_tender.f4_cotations import exporter_demandes, generer_demandes  # noqa: E402
from sie_tender.f5_dpgf import associer, construire_dpgf, importer_devis  # noqa: E402
from sie_tender.f6_archives import Bibliotheque  # noqa: E402
from sie_tender.f7_controle import controler, exporter_dossier  # noqa: E402
from sie_tender.models import StatutSection  # noqa: E402
from sie_tender.pipeline import analyser_affaire  # noqa: E402


def main(racine: Path) -> None:
    esp = EspaceTravail(racine / "affaires_demo")
    a = esp.creer()
    for f in (racine / "dce_fictif_valbourg").iterdir():
        a.deposer("dce", f.name, f.read_bytes())

    # F1, F2, F6 : analyse du DCE et recherche dans les archives
    biblio = Bibliotheque.indexer(racine / "archives_fictives")
    analyser_affaire(a, biblio)
    c = a.c
    print(f"[F1] {c.fiche.intitule.valeur} | remise : {c.fiche.date_limite_remise.valeur} | visite obligatoire : {c.fiche.visite_obligatoire.valeur}")
    print(f"[F1] lots : {', '.join(l.numero + ' ' + l.intitule for l in c.lots)}")

    # Décision de la direction (simulée)
    c.fiche.decision, c.fiche.decideur = "GO", "Direction SIE (simulation)"
    for l in c.lots:
        l.retenu = l.numero == "2"
    a.valider("fiche", "fiche", "Direction SIE (simulation)")
    print(f"[F2] {len(c.exigences)} exigences, {len(c.criteres)} critères, {len(c.pieces_a_fournir)} pièces à fournir")

    # F3 : métré du bureau d'études
    lignes, journal = importer_quantites(racine / "chiffrage_fictif" / "metre_lot02.xlsx")
    c.quantites, c.journal = lignes, c.journal + journal
    print(f"[F3] {journal[-1]}")
    for q in c.quantites:
        if q.remarque:
            print(f"      à vérifier : {q.designation} ({q.remarque})")

    # F4 : demandes de cotation
    c.demandes_cotation = generer_demandes(c)
    exporter_demandes(c, a.dossier("exports") / "demandes_cotation.xlsx")
    print(f"[F4] {len(c.demandes_cotation)} demandes : " + ", ".join(f"{d.famille} ({len(d.lignes)} lignes)" for d in c.demandes_cotation))

    # F5 : devis, association, DPGF
    for f in ("devis_elec_distribution_ouest.xlsx", "devis_lumiere_plus.pdf"):
        d = importer_devis(racine / "chiffrage_fictif" / f, lot="2")
        associer(c, d)
        c.devis.append(d)
        print(f"[F5] {d.fournisseur} : {len(d.lignes)} lignes, {sum(1 for l in d.lignes if l.ligne_quantite_id)} associées")
    c.coefficients.taux_horaire, c.coefficients.frais_generaux = 52.0, 12.0   # saisis par SIE (simulation)
    c.coefficients.aleas, c.coefficients.marge = 3.0, 8.0
    c.dpgf = construire_dpgf(c)
    print(f"[F5] DPGF : {len(c.dpgf)} postes, {sum(1 for p in c.dpgf if p.alertes)} avec alertes")
    for p in c.dpgf:
        if p.alertes:
            print(f"      {p.designation} : {' ; '.join(p.alertes)}")

    # Mémoire : SIE retient les passages et complète les sections (simulation partielle)
    for s in c.trame_memoire:
        if s.passages_proposes and s.statut == StatutSection.PROPOSE:
            s.contenu = s.passages_proposes[0].texte
            s.statut = StatutSection.VALIDE

    # F7 : contrôle et export
    for f in (racine / "chiffrage_fictif" / "pieces_reponse").iterdir():
        a.deposer("pieces_reponse", f.name, f.read_bytes())
    constats = controler(a, aujourdhui=date(2026, 11, 20))
    print(f"[F7] {sum(k.gravite == 'bloquant' for k in constats)} bloquants, {sum(k.gravite == 'a_verifier' for k in constats)} à vérifier")
    for k in constats:
        if k.gravite == "bloquant":
            print(f"      BLOQUANT {k.objet} : {k.message}")
    zip_path = exporter_dossier(a)
    a.sauver()
    print(f"[F7] dossier : {zip_path}")


if __name__ == "__main__":
    racine = Path(sys.argv[1] if len(sys.argv) > 1 else "exemples")
    shutil.rmtree(racine / "affaires_demo", ignore_errors=True)
    main(racine)
