"""Modèle de données commun (v0) entre le groupe 1 et le groupe 2.

Règle de base : toute information extraite porte une source et un statut.
Une information introuvable n'est jamais inventée : elle est marquée
« à vérifier » avec une valeur vide.
"""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field


def nouvel_id(prefixe: str) -> str:
    return f"{prefixe}-{uuid4().hex[:8]}"


class Statut(str, Enum):
    EXTRAIT = "extrait"            # trouvé dans une pièce, avec source
    A_VERIFIER = "a_verifier"      # absent, incertain ou source non retrouvée
    CORRIGE = "corrige_par_sie"    # modifié par un utilisateur SIE
    VALIDE = "valide"              # validé par un utilisateur SIE


class TypePiece(str, Enum):
    RC = "RC"
    CCAP = "CCAP"
    CCTP = "CCTP"
    AE = "AE"
    DPGF = "DPGF"
    BPU = "BPU"
    PLAN = "PLAN"
    ANNEXE = "ANNEXE"
    AUTRE = "AUTRE"


class Source(BaseModel):
    piece_id: str
    fichier: str
    page: Optional[int] = None
    article: Optional[str] = None
    extrait: str = ""


class Champ(BaseModel):
    """Une valeur extraite, avec sa source et son statut."""
    valeur: Optional[str] = None
    source: Optional[Source] = None
    statut: Statut = Statut.A_VERIFIER
    methode: str = "regle"  # regle | llm | saisie

    @classmethod
    def absent(cls) -> "Champ":
        return cls(valeur=None, source=None, statut=Statut.A_VERIFIER)


class Page(BaseModel):
    numero: int
    texte: str
    ocr: bool = False


class Piece(BaseModel):
    id: str = Field(default_factory=lambda: nouvel_id("P"))
    fichier: str
    type: TypePiece = TypePiece.AUTRE
    lot: Optional[str] = None  # numéro de lot, None = pièce commune
    nb_pages: int = 0
    pages: list[Page] = Field(default_factory=list, exclude=True)
    lisible: bool = True  # False pour les plans ou les pièces sans texte
    remarque: Optional[str] = None


class Lot(BaseModel):
    id: str = Field(default_factory=lambda: nouvel_id("LOT"))
    numero: str
    intitule: str
    source: Optional[Source] = None
    retenu: Optional[bool] = None  # décision SIE, jamais automatique
    responsable: Optional[str] = None


class CategorieExigence(str, Enum):
    TECHNIQUE = "technique"
    ADMINISTRATIVE = "administrative"
    RSE = "RSE"
    ELIMINATOIRE = "eliminatoire"
    INTERFACE = "interface"
    SITE_OCCUPE = "site_occupe"
    PLANNING = "planning"


class Exigence(BaseModel):
    id: str = Field(default_factory=lambda: nouvel_id("EX"))
    lot: Optional[str] = None
    categorie: CategorieExigence
    sous_categorie: Optional[str] = None
    texte: str
    source: Source
    statut: Statut = Statut.EXTRAIT


class Critere(BaseModel):
    id: str = Field(default_factory=lambda: nouvel_id("CR"))
    intitule: str
    ponderation: Optional[float] = None  # en %
    sous_criteres: list["Critere"] = Field(default_factory=list)
    source: Optional[Source] = None
    statut: Statut = Statut.EXTRAIT


class PieceAFournir(BaseModel):
    id: str = Field(default_factory=lambda: nouvel_id("PF"))
    intitule: str
    etape: str = "offre"  # candidature | offre
    obligatoire: bool = True
    source: Source
    document_propose: Optional[str] = None  # document d'archive proposé
    validite: Optional[date] = None
    statut: Statut = Statut.EXTRAIT


class FicheRenseignement(BaseModel):
    reference: Champ = Field(default_factory=Champ.absent)
    intitule: Champ = Field(default_factory=Champ.absent)
    objet: Champ = Field(default_factory=Champ.absent)
    lieu: Champ = Field(default_factory=Champ.absent)
    maitre_ouvrage: Champ = Field(default_factory=Champ.absent)
    maitre_oeuvre: Champ = Field(default_factory=Champ.absent)
    procedure: Champ = Field(default_factory=Champ.absent)
    date_limite_remise: Champ = Field(default_factory=Champ.absent)
    date_limite_questions: Champ = Field(default_factory=Champ.absent)
    visite: Champ = Field(default_factory=Champ.absent)
    visite_obligatoire: Champ = Field(default_factory=Champ.absent)
    contacts: Champ = Field(default_factory=Champ.absent)
    duree_validite_offres: Champ = Field(default_factory=Champ.absent)
    alertes: list[str] = Field(default_factory=list)

    # Décision : saisie par la direction SIE uniquement.
    decision: Optional[str] = None  # GO | NO GO
    decideur: Optional[str] = None
    date_decision: Optional[datetime] = None

    def champs(self) -> dict[str, Champ]:
        return {k: v for k, v in self if isinstance(v, Champ)}


class StatutSection(str, Enum):
    A_COMPLETER = "a_completer"
    PROPOSE = "propose"
    VALIDE = "valide"


class Passage(BaseModel):
    texte: str
    source: Source
    score: float = 0.0
    affaire: Optional[str] = None


class SectionMemoire(BaseModel):
    id: str = Field(default_factory=lambda: nouvel_id("SM"))
    titre: str
    critere: Optional[str] = None
    ponderation: Optional[float] = None
    passages_proposes: list[Passage] = Field(default_factory=list)
    statut: StatutSection = StatutSection.A_COMPLETER
    note: Optional[str] = None


class Validation(BaseModel):
    objet_id: str
    etape: str
    valideur: str
    date: datetime = Field(default_factory=datetime.now)
    commentaire: Optional[str] = None


class Consultation(BaseModel):
    id: str = Field(default_factory=lambda: nouvel_id("AFF"))
    dossier: str
    date_analyse: datetime = Field(default_factory=datetime.now)
    pieces: list[Piece] = Field(default_factory=list)
    fiche: FicheRenseignement = Field(default_factory=FicheRenseignement)
    lots: list[Lot] = Field(default_factory=list)
    exigences: list[Exigence] = Field(default_factory=list)
    criteres: list[Critere] = Field(default_factory=list)
    pieces_a_fournir: list[PieceAFournir] = Field(default_factory=list)
    trame_memoire: list[SectionMemoire] = Field(default_factory=list)
    validations: list[Validation] = Field(default_factory=list)
    journal: list[str] = Field(default_factory=list)  # hypothèses, erreurs, limites

    def piece(self, piece_id: str) -> Optional[Piece]:
        return next((p for p in self.pieces if p.id == piece_id), None)
