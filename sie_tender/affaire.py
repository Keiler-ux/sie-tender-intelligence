"""Espace de travail : un dossier par affaire, partagé par toutes les étapes.

    affaires/<id>/
      consultation.json   l'état complet, au format du modèle commun
      dce/                pièces du DCE déposées
      quantites/          fichiers Excel du bureau d'études
      devis/              devis fournisseurs reçus
      pieces_reponse/     attestations, AE signé, planning... déposés par SIE
      exports/            fichiers produits par l'outil
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from .models import Consultation, Validation

SOUS_DOSSIERS = ("dce", "quantites", "devis", "pieces_reponse", "exports")


class Affaire:
    def __init__(self, racine: Path, consultation: Consultation):
        self.racine = racine
        self.c = consultation

    @property
    def id(self) -> str:
        return self.c.id

    def dossier(self, nom: str) -> Path:
        d = self.racine / nom
        d.mkdir(parents=True, exist_ok=True)
        return d

    def sauver(self) -> None:
        (self.racine / "consultation.json").write_text(self.c.model_dump_json(indent=2), encoding="utf-8")

    def deposer(self, sous_dossier: str, nom: str, contenu: bytes) -> Path:
        chemin = self.dossier(sous_dossier) / Path(nom).name
        chemin.write_bytes(contenu)
        return chemin

    def valider(self, objet_id: str, etape: str, valideur: str, commentaire: str | None = None, role: str | None = None) -> None:
        self.c.validations.append(Validation(objet_id=objet_id, etape=etape, valideur=valideur, role=role,
                                             date=datetime.now(), commentaire=commentaire))

    def est_valide(self, etape: str) -> bool:
        return any(v.etape == etape for v in self.c.validations)


class EspaceTravail:
    def __init__(self, racine: str | Path = "affaires"):
        self.racine = Path(racine)
        self.racine.mkdir(parents=True, exist_ok=True)

    def creer(self, nom_dossier: str | None = None) -> Affaire:
        c = Consultation(dossier="")
        rep = self.racine / c.id
        for s in SOUS_DOSSIERS:
            (rep / s).mkdir(parents=True, exist_ok=True)
        c.dossier = str(rep / "dce")
        a = Affaire(rep, c)
        a.sauver()
        return a

    def ouvrir(self, affaire_id: str) -> Affaire:
        rep = self.racine / affaire_id
        c = Consultation.model_validate_json((rep / "consultation.json").read_text(encoding="utf-8"))
        return Affaire(rep, c)

    def lister(self) -> list[tuple[str, str]]:
        out = []
        for rep in sorted(self.racine.iterdir(), reverse=True):
            f = rep / "consultation.json"
            if f.exists():
                c = Consultation.model_validate_json(f.read_text(encoding="utf-8"))
                titre = c.fiche.intitule.valeur or c.fiche.objet.valeur or "(DCE non analysé)"
                out.append((c.id, titre))
        return out

    def supprimer(self, affaire_id: str) -> None:
        shutil.rmtree(self.racine / affaire_id, ignore_errors=True)
