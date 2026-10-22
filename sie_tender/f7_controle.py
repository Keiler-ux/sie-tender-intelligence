"""F7 : contrôle final et export du dossier pour validation humaine.

Le dossier exporté est destiné à la relecture et au dépôt manuel par SIE :
rien n'est envoyé à la plateforme de l'acheteur.
"""
from __future__ import annotations

import re
import zipfile
from datetime import date
from pathlib import Path

from .affaire import Affaire
from .export import exporter_excel, exporter_trame_docx
from .f2_exigences import PIECES_CONNUES
from .f5_dpgf import controles_dpgf, exporter_dpgf
from .models import CategorieExigence, Constat, Statut, StatutSection
from .texte import normaliser

MOTS_RSE = {
    "insertion": r"insertion|clause sociale",
    "dechets": r"dechet|tri\b|bordereau",
    "nuisances": r"nuisance|bruit|poussiere",
    "consommations": r"consommation|energie|economie",
    "deplacements": r"deplacement|covoiturage|transport",
    "environnement": r"environnement|carbone|reemploi|recycl",
}


# Noms de fichiers courants, plus courts que les intitulés des RC
FICHIERS = [
    (r"\bref(?:erence)?s?\b", "Références de travaux similaires", "candidature"),
    (r"acte (?:d )?engagement|\bae\b|\batte\b", "Acte d'engagement", "offre"),
    (r"visite", "Attestation de visite", "offre"),
    (r"assurance|decennale|\brc pro\b", "Attestations d'assurance (RC, décennale)", "candidature"),
    (r"planning", "Planning prévisionnel", "offre"),
]


def _classer_fichier(nom: str) -> tuple[str | None, str]:
    """Rattache un fichier déposé à une pièce attendue d'après son nom."""
    n = normaliser(Path(nom).stem.replace("_", " ").replace("-", " ")).replace("'", " ")
    for motif, intitule, etape in FICHIERS:
        if re.search(motif, n):
            return intitule, etape
    for motif, intitule, etape in PIECES_CONNUES:
        if re.search(motif, n):
            return intitule, etape
    return None, "annexe"


def controler(a: Affaire, aujourdhui: date | None = None) -> list[Constat]:
    c = a.c
    aujourdhui = aujourdhui or date.today()
    out: list[Constat] = []

    def ajout(g, objet, msg):
        out.append(Constat(gravite=g, objet=objet, message=msg))

    # Décision et lots
    if c.fiche.decision != "GO":
        ajout("bloquant", "Décision", "Décision GO non enregistrée par la direction.")
    if not any(l.retenu for l in c.lots):
        ajout("bloquant", "Lots", "Aucun lot retenu.")
    for nom, ch in c.fiche.champs().items():
        if ch.statut == Statut.A_VERIFIER and nom in ("date_limite_remise", "reference", "visite_obligatoire"):
            ajout("a_verifier", "Fiche", f"Champ « {nom} » non confirmé.")
    limite = None
    from .f4_cotations import lire_date
    limite = lire_date(c.fiche.date_limite_remise.valeur)
    if limite and limite < aujourdhui:
        ajout("bloquant", "Délai", f"Date limite de remise dépassée ({limite:%d/%m/%Y}).")

    # Pièces à fournir
    deposes = {}
    for f in sorted(a.dossier("pieces_reponse").iterdir()):
        intitule, _ = _classer_fichier(f.name)
        if intitule:
            deposes.setdefault(intitule, []).append(f.name)
    produits = {"DPGF": bool(c.dpgf), "Mémoire technique": bool(c.trame_memoire)}
    for pf in c.pieces_a_fournir:
        if pf.intitule in produits:
            if not produits[pf.intitule]:
                ajout("bloquant", pf.intitule, "Pièce à produire avec l'outil, non encore générée.")
            continue
        if pf.intitule in deposes:
            continue
        if pf.document_propose:
            if pf.validite and pf.validite < aujourdhui:
                ajout("bloquant", pf.intitule, f"Document proposé ({pf.document_propose}) périmé depuis le {pf.validite:%d/%m/%Y}.")
            else:
                ajout("a_verifier", pf.intitule, f"Non déposé ; document d'archive proposé : {pf.document_propose}.")
        else:
            ajout("bloquant", pf.intitule, "Pièce manquante.")
    if (c.fiche.visite_obligatoire.valeur == "oui" and "Attestation de visite" not in deposes
            and not any(pf.intitule == "Attestation de visite" for pf in c.pieces_a_fournir)):
        ajout("bloquant", "Attestation de visite", "Visite obligatoire : attestation non déposée.")

    # Chiffrage
    if not c.quantites:
        ajout("bloquant", "Quantités", "Aucun métré importé.")
    elif any(q.statut == Statut.A_VERIFIER for q in c.quantites):
        ajout("a_verifier", "Quantités", f"{sum(q.statut == Statut.A_VERIFIER for q in c.quantites)} lignes de métré à vérifier.")
    sans_prix = [p for p in c.dpgf if p.prix_unitaire_vente is None]
    if c.dpgf and sans_prix:
        ajout("bloquant", "DPGF", f"{len(sans_prix)} postes sans prix de vente.")
    out += [x for x in controles_dpgf(c) if x.gravite != "info"]

    # Mémoire
    a_completer = [s.titre for s in c.trame_memoire if s.statut == StatutSection.A_COMPLETER and not s.contenu]
    if a_completer:
        ajout("bloquant", "Mémoire", "Sections à compléter : " + " ; ".join(a_completer))
    non_valides = [s.titre for s in c.trame_memoire if s.statut != StatutSection.VALIDE]
    if non_valides and not a_completer:
        ajout("a_verifier", "Mémoire", f"{len(non_valides)} sections non validées par SIE.")
    texte_memoire = normaliser(" ".join((s.contenu or "") + " " + s.titre for s in c.trame_memoire))
    for sous in sorted({e.sous_categorie for e in c.exigences if e.categorie == CategorieExigence.RSE and e.sous_categorie}):
        if sous in MOTS_RSE and not re.search(MOTS_RSE[sous], texte_memoire):
            ajout("a_verifier", "RSE", f"Exigence RSE « {sous} » du DCE non traitée dans le mémoire.")

    # Validations humaines
    for etape in ("fiche", "quantites", "dpgf", "memoire"):
        if not a.est_valide(etape):
            ajout("a_verifier", "Validation", f"Étape « {etape} » non validée.")
    c.controles = out
    return out


def exporter_dossier(a: Affaire, modele_dpgf: str | Path | None = None) -> Path:
    """Construit le ZIP du dossier de réponse, avec la liste de contrôle en tête."""
    c = a.c
    exp = a.dossier("exports")
    dpgf = exporter_dpgf(c, exp / "DPGF.xlsx", modele_dpgf)
    memoire = exp / "Memoire_technique.docx"
    exporter_trame_docx(c, memoire)
    analyse = exp / "Analyse_DCE.xlsx"
    exporter_excel(c, analyse)
    constats = controler(a)
    liste = exp / "00_Liste_de_controle.md"
    lignes = ["# Liste de contrôle avant dépôt", "", f"Affaire : {c.fiche.intitule.valeur or c.id}", "",
              "Dossier préparé par l'outil pour relecture. Le dépôt sur la plateforme reste fait par SIE.", "",
              "| Gravité | Objet | Constat |", "| --- | --- | --- |"]
    ordre = {"bloquant": 0, "a_verifier": 1, "info": 2}
    for k in sorted(constats, key=lambda x: ordre.get(x.gravite, 3)):
        lignes.append(f"| {k.gravite} | {k.objet} | {k.message} |")
    if not constats:
        lignes.append("| info | Dossier | Aucun manque détecté. |")
    liste.write_text("\n".join(lignes), encoding="utf-8")

    zip_path = exp / f"Dossier_reponse_{c.id}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(liste, "00_Liste_de_controle.md")
        for f in sorted(a.dossier("pieces_reponse").iterdir()):
            intitule, etape = _classer_fichier(f.name)
            rep = {"candidature": "01_Candidature", "offre": "02_Offre"}.get(etape, "03_Annexes")
            z.write(f, f"{rep}/{f.name}")
        z.write(dpgf, "02_Offre/DPGF.xlsx")
        z.write(memoire, "02_Offre/Memoire_technique.docx")
        z.write(analyse, "04_Travail_interne_ne_pas_deposer/Analyse_DCE.xlsx")
    return zip_path
