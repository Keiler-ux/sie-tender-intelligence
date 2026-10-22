"""Ligne de commande.

    python -m sie_tender indexer  <dossier_archives>  --index archives.json
    python -m sie_tender analyser <dce.zip|dossier>  [--index archives.json] [--sortie resultats/]
    python -m sie_tender chercher "<requête>" --index archives.json
"""
from __future__ import annotations

import argparse
import sys

from .export import exporter_tout
from .f6_archives import Bibliotheque
from .pipeline import analyser_dce


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sie_tender", description="Outil d'aide à la réponse aux appels d'offres (groupe 1)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("analyser", help="analyser un DCE (dossier ou ZIP)")
    a.add_argument("dce")
    a.add_argument("--index", help="index des archives (créé par 'indexer')")
    a.add_argument("--sortie", default="resultats")

    i = sub.add_parser("indexer", help="indexer un dossier d'archives")
    i.add_argument("archives")
    i.add_argument("--index", default="archives_index.json")

    c = sub.add_parser("chercher", help="rechercher dans les archives")
    c.add_argument("requete")
    c.add_argument("--index", default="archives_index.json")
    c.add_argument("-k", type=int, default=5)

    args = ap.parse_args(argv)
    if args.cmd == "indexer":
        b = Bibliotheque.indexer(args.archives)
        b.sauver(args.index)
        print(f"{len(b.documents)} documents, {len(b.morceaux)} passages indexés -> {args.index}")
    elif args.cmd == "chercher":
        b = Bibliotheque.charger(args.index)
        for aff, score in b.affaires_similaires(args.requete):
            print(f"[affaire] {aff} (score {score})")
        for p in b.rechercher(args.requete, k=args.k):
            print(f"\n[{p.score}] {p.source.fichier} p.{p.source.page}\n{p.texte[:400]}…")
    else:
        b = Bibliotheque.charger(args.index) if args.index else None
        cons = analyser_dce(args.dce, b)
        chemins = exporter_tout(cons, args.sortie)
        a_verif = [k for k, ch in cons.fiche.champs().items() if ch.statut.value == "a_verifier"]
        print(f"{len(cons.pieces)} pièces, {len(cons.lots)} lots, {len(cons.exigences)} exigences, "
              f"{len(cons.criteres)} critères, {len(cons.pieces_a_fournir)} pièces à fournir, "
              f"{len(cons.trame_memoire)} sections de mémoire")
        if a_verif:
            print("Champs à vérifier :", ", ".join(a_verif))
        for nom, ch in chemins.items():
            print(f"{nom:9s} -> {ch}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
