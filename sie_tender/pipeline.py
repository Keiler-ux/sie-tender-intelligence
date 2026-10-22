"""Enchaînement complet du groupe 1 : DCE -> fiche -> exigences -> trame du mémoire."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from .f1_fiche import construire_fiche
from .f2_exigences import extraire_criteres, extraire_exigences, extraire_pieces_a_fournir
from .f6_archives import Bibliotheque, construire_trame, contexte_affaire, proposer_pieces
from .ingestion import importer_dce
from .llm import ClientLLM, client_depuis_env
from .models import Consultation


def analyser_dce(dce: str | Path, bibliotheque: Bibliotheque | None = None, llm: ClientLLM | None = None,
                 aujourdhui: date | None = None) -> Consultation:
    llm = llm or client_depuis_env()
    c = Consultation(dossier=str(dce))
    c.journal.append(f"Analyse avec LLM : {llm.nom}.")
    c.pieces = importer_dce(dce)
    for p in c.pieces:
        if p.remarque:
            c.journal.append(f"Import : {p.fichier} : {p.remarque}")

    c.fiche, c.lots = construire_fiche(c.pieces, llm, c.journal)
    c.exigences = extraire_exigences(c.pieces)
    c.criteres = extraire_criteres(c.pieces)
    if not c.criteres:
        c.journal.append("F2 : critères de notation non trouvés dans le RC, à saisir.")
    elif any(cr.statut.value == "a_verifier" for cr in c.criteres):
        c.journal.append("F2 : la somme des pondérations ne fait pas 100 %, critères à vérifier.")
    c.pieces_a_fournir = extraire_pieces_a_fournir(c.pieces)

    proposer_pieces(c.pieces_a_fournir, bibliotheque, aujourdhui, c.journal)
    c.trame_memoire = construire_trame(c.criteres, bibliotheque, contexte_affaire(c.fiche.objet.valeur, c.lots), c.journal)
    if bibliotheque is None:
        c.journal.append("F6 : pas de bibliothèque d'archives fournie, trame sans passages proposés.")
    return c


def analyser_affaire(affaire, bibliotheque: Bibliotheque | None = None, llm: ClientLLM | None = None) -> None:
    """Analyse le DCE déposé dans une affaire en gardant son identifiant et le travail du groupe 2."""
    r = analyser_dce(affaire.dossier("dce"), bibliotheque, llm)
    c = affaire.c
    for champ in ("pieces", "fiche", "lots", "exigences", "criteres", "pieces_a_fournir", "trame_memoire"):
        setattr(c, champ, getattr(r, champ))
    c.journal = r.journal
    affaire.sauver()
