"""F6 : bibliothèque d'archives, recherche d'affaires similaires et trame du mémoire.

Organisation attendue des archives (un dossier par affaire) :

    archives/
      2023_college_jean_moulin/
        affaire.json          (facultatif : {"activite": "CFO", "annee": 2023, "type": "enseignement"})
        memoire_technique.pdf
        attestation_qualifelec.pdf

La recherche est lexicale (BM25), sans service externe : elle tourne entièrement
en local. Une recherche par vecteurs pourra être ajoutée derrière la même
interface si les essais montrent qu'elle est nécessaire.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from rank_bm25 import BM25Okapi

from .ingestion import lire_fichier
from .models import (Critere, Lot, Passage, PieceAFournir, SectionMemoire, Source, Statut,
                     StatutSection)
from .texte import articles, normaliser

MOTS_VIDES = set("""a au aux avec ce ces dans de des du elle en et eux il je la le les leur lui ma mais me meme mes moi mon
ne nos notre nous on ou par pas pour qu que qui sa se ses son sur ta te tes toi ton tu un une vos votre vous c d j l m n s t y
ete etre est sont sera seront ont avoir fait faire cette cet ces leurs tout tous toute toutes plus moins tres ainsi afin lors
entre sans sous chez dont donc car si""".split())


def jetons(texte: str) -> list[str]:
    mots = re.findall(r"[a-z0-9]+", normaliser(texte))
    out = []
    for m in mots:
        if m in MOTS_VIDES or len(m) < 2:
            continue
        # racinisation légère : pluriels et quelques suffixes fréquents
        for suf in ("ements", "ement", "ations", "ation", "ements", "ites", "ite", "eurs", "eur", "es", "s"):
            if len(m) > len(suf) + 3 and m.endswith(suf):
                m = m[: -len(suf)]
                break
        out.append(m)
    return out


@dataclass
class Morceau:
    affaire: str
    fichier: str
    page: int
    article: str | None
    titre: str | None
    texte: str
    meta: dict = field(default_factory=dict)


@dataclass
class DocumentArchive:
    affaire: str
    fichier: str
    type: str  # memoire | attestation | reference | autre
    validite: str | None = None  # ISO
    meta: dict = field(default_factory=dict)


_VALIDITE = re.compile(
    r"(?:valable|valide|validit[ée]|expir\w*|jusqu'au|fin de validit[ée])[^\d\n]{0,40}(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", re.I)


def _type_document(nom: str, texte: str) -> str:
    n = normaliser(nom + " " + texte[:600])
    if re.search(r"memoire|methodologie|note technique", n):
        return "memoire"
    if re.search(r"attestation|certificat|qualifelec|qualibat|kbis|urssaf|assurance", n):
        return "attestation"
    if re.search(r"reference", n):
        return "reference"
    return "autre"


def _decouper(texte_pages: list[tuple[int, str]], taille: int = 180) -> list[tuple[int, str | None, str | None, str]]:
    """Découpe par article si possible, sinon par fenêtres de ~180 mots."""
    arts = articles(texte_pages)
    out = []
    if len(arts) >= 2:
        for a in arts:
            mots = a.texte.split()
            for i in range(0, max(len(mots), 1), taille):
                bloc = " ".join(mots[i:i + taille])
                if len(bloc) > 60:
                    out.append((a.page, a.numero, a.titre, bloc))
        return out
    for num, texte in texte_pages:
        mots = texte.split()
        for i in range(0, len(mots), taille - 30):
            bloc = " ".join(mots[i:i + taille])
            if len(bloc) > 60:
                out.append((num, None, None, bloc))
    return out


class Bibliotheque:
    def __init__(self, morceaux: list[Morceau] | None = None, documents: list[DocumentArchive] | None = None):
        self.morceaux = morceaux or []
        self.documents = documents or []
        self._bm25 = BM25Okapi([jetons(m.texte + " " + (m.titre or "")) for m in self.morceaux]) if self.morceaux else None

    # --- construction / sauvegarde ---
    @classmethod
    def indexer(cls, dossier: str | Path) -> "Bibliotheque":
        dossier = Path(dossier)
        morceaux, documents = [], []
        for rep in sorted(p for p in dossier.iterdir() if p.is_dir()):
            meta = {}
            if (rep / "affaire.json").exists():
                meta = json.loads((rep / "affaire.json").read_text(encoding="utf-8"))
            for f in sorted(rep.rglob("*")):
                if not f.is_file() or f.name == "affaire.json" or f.name.startswith("."):
                    continue
                piece = lire_fichier(f)
                if not piece.lisible:
                    continue
                pages = [(p.numero, p.texte) for p in piece.pages]
                tout = "\n".join(t for _, t in pages)
                type_doc = _type_document(f.name, tout)
                validite = None
                m = _VALIDITE.search(tout)
                if m:
                    try:
                        validite = date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
                    except ValueError:
                        pass
                documents.append(DocumentArchive(rep.name, f.name, type_doc, validite, meta))
                if type_doc in ("memoire", "reference", "autre"):
                    for page, art, titre, bloc in _decouper(pages):
                        morceaux.append(Morceau(rep.name, f.name, page, art, titre, bloc, meta))
        return cls(morceaux, documents)

    def sauver(self, chemin: str | Path) -> None:
        Path(chemin).write_text(json.dumps({"morceaux": [asdict(m) for m in self.morceaux],
                                            "documents": [asdict(d) for d in self.documents]}, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def charger(cls, chemin: str | Path) -> "Bibliotheque":
        d = json.loads(Path(chemin).read_text(encoding="utf-8"))
        return cls([Morceau(**m) for m in d["morceaux"]], [DocumentArchive(**x) for x in d["documents"]])

    # --- recherche ---
    def rechercher(self, requete: str, k: int = 5, score_min: float = 1.0) -> list[Passage]:
        if not self._bm25:
            return []
        q = jetons(requete)
        if not q:
            return []
        scores = self._bm25.get_scores(q)
        ordre = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
        out = []
        for i in ordre:
            if scores[i] < score_min:
                break
            m = self.morceaux[i]
            out.append(Passage(texte=m.texte, score=round(float(scores[i]), 2), affaire=m.affaire,
                               source=Source(piece_id=f"ARCH:{m.affaire}/{m.fichier}", fichier=f"{m.affaire}/{m.fichier}",
                                             page=m.page, article=m.article, extrait=m.texte[:300])))
        return out

    def affaires_similaires(self, description: str, k: int = 3) -> list[tuple[str, float]]:
        """Classe les affaires par la somme des 3 meilleurs scores de leurs passages."""
        if not self._bm25:
            return []
        scores = self._bm25.get_scores(jetons(description))
        par_affaire: dict[str, list[float]] = {}
        for m, s in zip(self.morceaux, scores):
            par_affaire.setdefault(m.affaire, []).append(float(s))
        classement = [(a, round(sum(sorted(v, reverse=True)[:3]), 2)) for a, v in par_affaire.items()]
        return sorted([c for c in classement if c[1] > 0], key=lambda x: x[1], reverse=True)[:k]


# --- Trame du mémoire ------------------------------------------------------------
SECTIONS_SIE = [
    ("Organisation de l'équipe projet", "organigramme équipe chargé d'affaires conducteur de travaux chef de chantier"),
    ("Moyens humains", "moyens humains effectifs électriciens qualifications habilitations"),
    ("Moyens matériels", "moyens matériels outillage véhicules nacelles"),
    ("Planning d'exécution", "planning phasage délais durée travaux"),
]
_A_COMPLETER_PAR_SIE = {"Organisation de l'équipe projet", "Moyens humains", "Moyens matériels", "Planning d'exécution"}


def _est_prix(c: Critere) -> bool:
    return bool(re.search(r"\bprix\b|montant|cout|financ", normaliser(c.intitule)))


def construire_trame(criteres: list[Critere], biblio: Bibliotheque | None, contexte: str = "",
                     journal: list[str] | None = None) -> list[SectionMemoire]:
    journal = journal if journal is not None else []
    sections: list[SectionMemoire] = []
    techniques = [c for c in criteres if not _est_prix(c)]
    if not techniques:
        journal.append("F6 : aucun critère technique trouvé dans le RC ; trame par défaut SIE, à vérifier.")
    for c in techniques:
        enfants = c.sous_criteres or [c]
        for sc in enfants:
            sections.append(SectionMemoire(titre=sc.intitule, critere=c.intitule, ponderation=sc.ponderation))
    titres = {normaliser(s.titre) for s in sections}
    for titre, _ in SECTIONS_SIE:
        mots = set(jetons(titre))
        if not any(mots & set(jetons(t)) for t in titres):
            sections.append(SectionMemoire(titre=titre, note="Section habituelle des mémoires SIE, non imposée par le RC."))
    requetes = dict(SECTIONS_SIE)
    for s in sections:
        if s.titre in _A_COMPLETER_PAR_SIE:
            s.statut = StatutSection.A_COMPLETER
            s.note = (s.note + " " if s.note else "") + "À compléter par le bureau d'études (données propres à l'affaire)."
        if biblio is None:
            continue
        passages = biblio.rechercher(f"{s.titre} {requetes.get(s.titre, '')}", k=5)
        s.passages_proposes = passages
        if passages and s.titre not in _A_COMPLETER_PAR_SIE:
            s.statut = StatutSection.PROPOSE
        elif not passages:
            s.statut = StatutSection.A_COMPLETER
            s.note = (s.note + " " if s.note else "") + "Aucun passage similaire dans les archives : section à rédiger."
    return sections


def proposer_pieces(pieces_a_fournir: list[PieceAFournir], biblio: Bibliotheque | None, aujourdhui: date | None = None,
                    journal: list[str] | None = None) -> None:
    """Associe aux pièces administratives un document d'archive et signale les validités dépassées."""
    if biblio is None:
        return
    journal = journal if journal is not None else []
    aujourdhui = aujourdhui or date.today()
    correspondances = [
        (r"qualification", r"qualifelec|qualibat|qualification"),
        (r"assurance", r"assurance|decennale"),
        (r"urssaf|vigilance", r"urssaf|vigilance"),
        (r"fiscale", r"fiscal|impots|dgfip"),
        (r"kbis", r"kbis"),
        (r"references", r"reference"),
    ]
    for pf in pieces_a_fournir:
        n = normaliser(pf.intitule)
        for motif_piece, motif_doc in correspondances:
            if not re.search(motif_piece, n):
                continue
            candidats = [d for d in biblio.documents if re.search(motif_doc, normaliser(d.fichier))]
            if not candidats:
                break
            # le plus récent d'abord (validité la plus lointaine)
            candidats.sort(key=lambda d: d.validite or "", reverse=True)
            d = candidats[0]
            pf.document_propose = f"{d.affaire}/{d.fichier}"
            if d.validite:
                pf.validite = date.fromisoformat(d.validite)
                if pf.validite < aujourdhui:
                    pf.statut = Statut.A_VERIFIER
                    journal.append(f"F6 : {pf.intitule} : document {pf.document_propose} périmé depuis le {pf.validite:%d/%m/%Y}.")
            else:
                pf.statut = Statut.A_VERIFIER
                journal.append(f"F6 : {pf.intitule} : validité de {pf.document_propose} non trouvée, à vérifier.")
            break


def contexte_affaire(objet: str | None, lots: list[Lot]) -> str:
    return " ".join([objet or ""] + [l.intitule for l in lots if l.retenu is not False])
