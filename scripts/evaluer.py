"""Mesure la qualité de l'extraction (F1, F2, F6) sur des DCE dont le résultat attendu est connu.

    python scripts/evaluer.py                      # DCE fictifs + attendus de scripts/attendus/
    python scripts/evaluer.py DOSSIER_DCE ATTENDU.json

Format d'un fichier attendu (toutes les clés sont facultatives) :
    {"fiche": {"reference": "VAL-2026-014", "date_limite_remise": "27 novembre 2026", "lieu": null},
     "lots": ["1", "2"], "criteres": {"Prix": 40}, "interfaces_min": 1, "heures_insertion": [70],
     "cadre_reponse": true}
Une valeur attendue est juste si elle est contenue dans la valeur extraite (sans accents ni casse).
null signifie : l'information est absente du DCE, l'outil doit laisser « à vérifier » (rien d'inventé).

Pour un DCE réel de SIE : écrire l'attendu à la main, lancer l'évaluation en local, ne rien committer.
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "scripts"))

from sie_tender.f2_synthese import heures_insertion, matrice_interfaces  # noqa: E402
from sie_tender.llm import AucunLLM  # noqa: E402
from sie_tender.models import Statut  # noqa: E402
from sie_tender.pipeline import analyser_dce  # noqa: E402
from sie_tender.texte import normaliser  # noqa: E402


def evaluer(dce: Path, attendu: dict) -> tuple[list[tuple[str, bool, str]], float]:
    t0 = time.perf_counter()
    c = analyser_dce(dce, llm=AucunLLM())
    duree = time.perf_counter() - t0
    res: list[tuple[str, bool, str]] = []
    for champ, val in attendu.get("fiche", {}).items():
        ch = getattr(c.fiche, champ)
        if val is None:
            res.append((f"fiche.{champ} (absent)", ch.statut == Statut.A_VERIFIER and not ch.valeur, ch.valeur or "à vérifier"))
        else:
            res.append((f"fiche.{champ}", bool(ch.valeur) and normaliser(val) in normaliser(ch.valeur), ch.valeur or "—"))
    if "lots" in attendu:
        trouves = [l.numero for l in c.lots]
        res.append(("lots", trouves == attendu["lots"], ", ".join(trouves)))
    if "criteres" in attendu:
        trouves = {cr.intitule: cr.ponderation for cr in c.criteres}
        for intitule, pond in attendu["criteres"].items():
            ok = any(normaliser(intitule) in normaliser(k) and v == pond for k, v in trouves.items())
            res.append((f"critère {intitule}", ok, str(trouves.get(intitule, "—"))))
    if "interfaces_min" in attendu:
        n = len(matrice_interfaces(c))
        res.append((f"interfaces ≥ {attendu['interfaces_min']}", n >= attendu["interfaces_min"], str(n)))
    if "heures_insertion" in attendu:
        h = sorted(x for x, _ in heures_insertion(c.exigences))
        res.append(("heures d'insertion", h == sorted(attendu["heures_insertion"]), str(h)))
    if "cadre_reponse" in attendu:
        res.append(("cadre de réponse", bool(c.cadre_reponse) == attendu["cadre_reponse"],
                    c.cadre_reponse.fichier if c.cadre_reponse else "non trouvé"))
    return res, duree


def main(args: list[str]) -> int:
    if len(args) == 2:
        cas = [(Path(args[0]), json.loads(Path(args[1]).read_text(encoding="utf-8")))]
    else:
        from generer_dce_test import generer as generer_test
        from generer_donnees_fictives import generer
        tmp = Path(tempfile.mkdtemp())
        generer(tmp)
        generer_test(tmp)
        cas = [(tmp / f.stem, json.loads(f.read_text(encoding="utf-8"))) for f in sorted((RACINE / "scripts" / "attendus").glob("*.json"))]
    total = justes = 0
    for dce, attendu in cas:
        res, duree = evaluer(dce, attendu)
        print(f"\n{dce.name}  ({duree:.1f} s)")
        for nom, ok, obtenu in res:
            print(f"  {'OK ' if ok else 'ÉCHEC'}  {nom:32} {obtenu[:70]!r}")
        total += len(res)
        justes += sum(ok for _, ok, _ in res)
    print(f"\nScore : {justes}/{total} ({100 * justes / max(total, 1):.0f} %)")
    return 0 if justes == total else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
