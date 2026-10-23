"""F2 : exigences, critères de notation, RSE, interfaces et pièces à fournir.

Chaque élément est une phrase réellement présente dans une pièce, avec sa
source. Rien n'est reformulé ni complété : la synthèse rédigée par lot se
fait à partir de ces éléments (à la main, ou par un LLM s'il est autorisé).
"""
from __future__ import annotations

import re
from collections import defaultdict

from .models import (CategorieExigence, Critere, Exigence, Piece, PieceAFournir, Source, Statut,
                     TypePiece)
from .recherche_texte import source
from .texte import normaliser, phrases_positions

I = re.IGNORECASE

# --- RSE -------------------------------------------------------------------
RSE: dict[str, list[str]] = {
    "insertion": [r"insertion (?:professionnelle|sociale)", r"parcours d'insertion", r"clause sociale", r"heures? d'insertion", r"personnes? eloignees? de l'emploi"],
    "dechets": [r"dechets?", r"\btri\b", r"\bsoged\b", r"bordereaux? de suivi", r"valorisation", r"recycl"],
    "nuisances": [r"nuisances?", r"\bbruit", r"poussieres?", r"chantier (?:a faibles nuisances|propre|vert)"],
    "consommations": [r"consommations? (?:d'energie|d'eau|energetiques?)", r"economies? d'energie", r"sobriete"],
    "deplacements": [r"deplacements?", r"covoiturage", r"transports? en commun", r"vehicules? (?:propres|electriques)"],
    "environnement": [r"clause environnementale", r"developpement durable", r"empreinte carbone", r"bilan carbone", r"biosource", r"reemploi", r"\brse\b", r"responsabilite societale"],
}

INTERFACE = [r"a la charge du lot", r"fourni(?:e|s)? (?:et pose(?:e|s)? )?par le lot", r"en coordination avec (?:le|les) lots?",
             r"limites? de prestations?", r"reservations?", r"asservissements?",
             r"alimentations? electriques? (?:des|du|de la)", r"\balimente\b", r"commandee? par le (?:ssi|lot)", r"interfaces? avec",
             r"lot (?:cvc|plomberie|electricite|gros oeuvre|platrerie|faux plafonds)", r"tous corps d'etat", r"compte prorata",
             r"entreprises? (?:des|titulaires? des) autres lots"]
SITE_OCCUPE = [r"site occupe", r"milieu occupe", r"maintien (?:en activite|de l'activite|en exploitation)", r"travaux de nuit",
               r"\bphasage\b", r"horaires? (?:d'intervention|de travail)", r"etablissement (?:ouvert|en activite)"]
PLANNING = [r"delai d'execution", r"periode de preparation", r"duree (?:des travaux|du marche|globale)", r"date previsionnelle de (?:debut|demarrage)"]
ELIMINATOIRE = [r"sous peine", r"irrecevable", r"sera rejete", r"seront rejetes", r"sera elimine", r"obligatoirement", r"exige(?:e|es|s)? sous"]
PENALITE = [r"penalites?", r"retenues? (?:de|pour) retard"]
TECHNIQUE = [r"nf c ?\d{2}[- ]?\d{3}", r"\bdtu\b", r"conforme(?:ment)? (?:a|aux) (?:la )?normes?", r"\bnf en\b", r"apsad", r"reglementation (?:erp|incendie|thermique)"]

PIECES_CONNUES: list[tuple[str, str, str]] = [
    # (regex sur texte normalisé, intitulé, étape)
    (r"\bdc ?1\b|lettre de candidature", "Lettre de candidature (DC1)", "candidature"),
    (r"\bdc ?2\b|declaration du candidat", "Déclaration du candidat (DC2)", "candidature"),
    (r"\bdume\b", "DUME", "candidature"),
    (r"\bk ?bis\b", "Extrait Kbis", "candidature"),
    (r"attestations? d'assurances?|assurance (?:responsabilite civile|decennale)", "Attestations d'assurance (RC, décennale)", "candidature"),
    (r"\burssaf\b|attestation de vigilance", "Attestation de vigilance URSSAF", "candidature"),
    (r"attestation (?:de regularite )?fiscale", "Attestation de régularité fiscale", "candidature"),
    (r"qualifelec|qualibat|\bapsad\b|certificats? de qualification", "Certificats de qualification", "candidature"),
    (r"references? (?:de travaux|similaires|de chantiers?)|liste des (?:principaux )?travaux", "Références de travaux similaires", "candidature"),
    (r"moyens humains et (?:techniques|materiels)|effectifs? (?:moyens )?annuels?", "Moyens humains et matériels", "candidature"),
    (r"acte d'engagement|\bae\b", "Acte d'engagement", "offre"),
    (r"\bdpgf\b|decomposition du prix global", "DPGF", "offre"),
    (r"\bbpu\b|bordereau des prix", "BPU", "offre"),
    (r"\bdqe\b|detail quantitatif", "DQE", "offre"),
    (r"memoire (?:technique|justificatif)", "Mémoire technique", "offre"),
    (r"attestation de visite|certificat de visite", "Attestation de visite", "offre"),
    (r"planning (?:previsionnel|d'execution|detaille)", "Planning prévisionnel", "offre"),
    (r"fiches? techniques?|fiches? produits?", "Fiches techniques des matériels", "offre"),
    (r"\brib\b|releve d'identite bancaire", "RIB", "offre"),
]

_CTX_PIECES = re.compile(r"(?:pieces?|documents?|elements?)\s+(?:a\s+(?:fournir|produire|remettre|joindre)|constitutifs?|demandes|exiges)|contenu (?:du|des) (?:dossier|plis?|offres?|candidatures?)|pieces? (?:de|de la|de l'|au titre de la) ?(?:candidature|offre)|au titre de (?:la candidature|l'offre)|(?:le|les) candidats? (?:doit|doivent|devront) (?:fournir|produire|remettre|joindre)|(?:dossier|pli|offre) (?:comprendra|contiendra|comporte)", I)


def _cherche(liste: list[str], texte_norm: str) -> bool:
    return any(re.search(m, texte_norm) for m in liste)


_LIGNE_POURCENTAGE = re.compile(r"\d\s*(?:%|points?|pts)\s*$")


def _phrases_page(piece: Piece, page):
    """Phrases d'une page avec leur source ; les lignes de barème (« ... : 25 % ») sont écartées."""
    for ph, debut, fin in phrases_positions(page.texte):
        if _LIGNE_POURCENTAGE.search(ph):
            continue
        yield ph, normaliser(ph), source(piece, page.numero, page.texte, debut, fin)


def _sources_phrases(piece: Piece):
    for page in piece.pages:
        yield from _phrases_page(piece, page)


def _lots_cites(texte_norm: str) -> list[str]:
    return sorted({str(int(n)) for n in re.findall(r"\blots?\s*(?:n\s*[°o]?\s*)?(\d{1,2})\b", texte_norm)}, key=int)


def extraire_exigences(pieces: list[Piece]) -> list[Exigence]:
    vues: set[str] = set()
    out: list[Exigence] = []

    def ajouter(cat, texte, src, lot, sous=None):
        cle = f"{cat}|{normaliser(texte)[:150]}"
        if cle in vues:
            return
        vues.add(cle)
        out.append(Exigence(lot=lot, categorie=cat, sous_categorie=sous, texte=texte, source=Source(**{**src.model_dump(), "extrait": texte[:400]})))

    for p in pieces:
        if not p.lisible or p.type in (TypePiece.DPGF, TypePiece.BPU, TypePiece.PLAN):
            continue
        for ph, n, src in _sources_phrases(p):
            if len(ph) > 700:
                ph = ph[:700] + "…"
            for sous, motifs in RSE.items():
                if _cherche(motifs, n):
                    ajouter(CategorieExigence.RSE, ph, src, p.lot, sous)
                    break
            # Interfaces : CCTP de tous les lots (même non retenus) et pièces communes (CCAP).
            if p.type in (TypePiece.CCTP, TypePiece.CCAP):
                cites = [l for l in _lots_cites(n) if l != p.lot]
                if _cherche(INTERFACE, n) or (p.type == TypePiece.CCTP and cites):
                    sous = f"lot {p.lot or 'commun'} -> lot {', '.join(cites)}" if cites else "interface entre corps d'état"
                    ajouter(CategorieExigence.INTERFACE, ph, src, p.lot, sous)
            if p.type in (TypePiece.CCAP, TypePiece.CCTP, TypePiece.AE) and _cherche(PENALITE, n):
                ajouter(CategorieExigence.PENALITE, ph, src, p.lot)
            if _cherche(SITE_OCCUPE, n):
                ajouter(CategorieExigence.SITE_OCCUPE, ph, src, p.lot)
            if _cherche(PLANNING, n):
                ajouter(CategorieExigence.PLANNING, ph, src, p.lot)
            if _cherche(ELIMINATOIRE, n):
                ajouter(CategorieExigence.ELIMINATOIRE, ph, src, p.lot)
            if p.type == TypePiece.CCTP and _cherche(TECHNIQUE, n):
                ajouter(CategorieExigence.TECHNIQUE, ph, src, p.lot)
    return out


# --- Critères de notation ------------------------------------------------------
_LIGNE_CRITERE = re.compile(
    r"^\s*(?P<puce>(?:\d{1,2}(?:\.\d)?\s*[.)\-–]|[a-z]\)|[-•*–]|sous-crit[eè]re\s*\d*\s*:?)\s*)?"
    r"(?P<intitule>[A-ZÀ-Ýa-zà-ý][^\n:]{2,120}?)\s*[:\-–(]?\s*(?:pond[ée]ration\s*:?\s*)?"
    r"(?P<pond>\d{1,3}(?:[,.]\d{1,2})?)\s*(?:%|points?|pts)\s*\)?\s*$",
    re.M,
)


def extraire_criteres(pieces: list[Piece]) -> list[Critere]:
    criteres: list[Critere] = []
    for p in pieces:
        if p.type != TypePiece.RC or not p.lisible:
            continue
        for page in p.pages:
            n = normaliser(page.texte)
            if not re.search(r"criteres?|jugement des offres|attribution|notation", n):
                continue
            for m in _LIGNE_CRITERE.finditer(page.texte):
                intitule = m.group("intitule").strip(" .:-–")
                if len(intitule.split()) > 16 or re.search(r"\b(?:lot|article|tva|jours?)\b", intitule, I):
                    continue
                pond = float(m.group("pond").replace(",", "."))
                if pond > 100:
                    continue
                src = source(p, page.numero, page.texte, m.start(), m.end())
                puce = (m.group("puce") or "").strip()
                sous = bool(re.match(r"[-•*–]|[a-z]\)|sous-crit|\d\.\d", puce, I)) or normaliser(intitule).startswith("sous-critere")
                c = Critere(intitule=intitule, ponderation=pond, source=src)
                if sous and criteres:
                    criteres[-1].sous_criteres.append(c)
                else:
                    criteres.append(c)
        if criteres:
            break
    total = sum(c.ponderation or 0 for c in criteres)
    if criteres and abs(total - 100) > 0.5:
        for c in criteres:
            c.statut = Statut.A_VERIFIER
    return criteres


# --- Pièces à fournir ----------------------------------------------------------
def extraire_pieces_a_fournir(pieces: list[Piece]) -> list[PieceAFournir]:
    out: dict[str, PieceAFournir] = {}
    for p in pieces:
        if p.type not in (TypePiece.RC, TypePiece.CCAP) or not p.lisible:
            continue
        for page in p.pages:
            dans_contexte = bool(_CTX_PIECES.search(normaliser(page.texte)))
            if not dans_contexte and p.type != TypePiece.RC:
                continue
            for ph, n, src in _phrases_page(p, page):
                for motif, intitule, etape in PIECES_CONNUES:
                    if intitule in out or not re.search(motif, n):
                        continue
                    out[intitule] = PieceAFournir(intitule=intitule, etape=etape, source=src,
                                                  statut=Statut.EXTRAIT if dans_contexte else Statut.A_VERIFIER)
    return sorted(out.values(), key=lambda x: (x.etape != "candidature", x.intitule))


def resume_par_lot(exigences: list[Exigence]) -> dict[str, dict[str, int]]:
    res: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for e in exigences:
        res[e.lot or "commun"][e.categorie.value] += 1
    return {k: dict(v) for k, v in res.items()}
