"""Fonctions IA de l'outil : synthèse du DCE, questions au DCE, brouillon de section du mémoire.

Règles communes :
- l'IA ne travaille que sur le texte des pièces (ou des archives) qu'on lui transmet ;
- chaque citation renvoyée est recherchée mot pour mot dans la pièce : sinon elle est
  marquée « non vérifiée » et l'interface le signale ;
- rien n'est validé automatiquement : un brouillon reste « proposé » tant que SIE ne l'a pas validé.
"""
from __future__ import annotations

from rank_bm25 import BM25Okapi

from .f6_archives import jetons
from .llm import ClientLLM, PageDoc, Reponse
from .models import CitationIA, Consultation, Piece, ReponseIA, SectionMemoire, TypePiece
from .texte import normaliser

_PRIORITE = {TypePiece.RC: 0, TypePiece.AE: 1, TypePiece.CCAP: 2, TypePiece.CCTP: 3, TypePiece.ANNEXE: 4}

REGLES = """Tu assistes le bureau d'études d'une entreprise d'électricité (CFO/CFA) qui répond à des appels d'offres publics.
Règles impératives :
- Appuie-toi uniquement sur les documents fournis. N'invente aucune date, aucun montant, aucun nom, aucune référence.
- Si l'information n'y figure pas, écris « non trouvé dans le DCE ».
- La décision GO / NO GO, les prix et les marges appartiennent à SIE : ne les propose pas.
- Réponds en français, de façon sobre et factuelle, sans superlatifs."""


def pages_pertinentes(pieces: list[Piece], requete: str, max_car: int, prioritaires: bool = False) -> list[PageDoc]:
    """Pages à transmettre, dans la limite de max_car caractères.

    prioritaires=True : d'abord le RC, l'AE, le CCAP (synthèse) ; sinon classement BM25 sur la requête."""
    pages = [(p, pg) for p in pieces if p.lisible for pg in p.pages if pg.texte.strip()]
    if not pages:
        return []
    if sum(len(pg.texte) for _, pg in pages) <= max_car:
        retenues = pages
    else:
        if prioritaires:
            ordre = sorted(range(len(pages)), key=lambda i: (_PRIORITE.get(pages[i][0].type, 9), i))
        else:
            bm = BM25Okapi([jetons(pg.texte) or ["_"] for _, pg in pages])
            scores = bm.get_scores(jetons(requete) or ["_"])
            ordre = sorted(range(len(pages)), key=lambda i: (-scores[i], _PRIORITE.get(pages[i][0].type, 9)))
        retenues, total = [], 0
        for i in ordre:
            n = len(pages[i][1].texte)
            if total + n > max_car:
                continue
            retenues.append(pages[i])
            total += n
        retenues.sort(key=lambda x: pages.index(x))
    return [PageDoc(p.fichier, pg.numero, pg.texte) for p, pg in retenues]


def verifier(citations, pieces: list[Piece]) -> list[CitationIA]:
    out = []
    textes = {(p.fichier, pg.numero): normaliser(pg.texte) for p in pieces for pg in p.pages}
    for c in citations:
        cible = normaliser(c.extrait)
        ok = len(cible) >= 8 and cible in textes.get((c.fichier, c.page), "")
        if not any(x.fichier == c.fichier and x.page == c.page and x.extrait == c.extrait for x in out):
            out.append(CitationIA(fichier=c.fichier, page=c.page, extrait=c.extrait, verifiee=ok))
    return out


def _reponse(type_: str, question: str, rep: Reponse, moteur: ClientLLM, pieces: list[Piece]) -> ReponseIA:
    if rep.erreur:
        raise RuntimeError(rep.erreur)
    return ReponseIA(type=type_, question=question, texte=rep.texte, citations=verifier(rep.citations, pieces),
                     moteur=moteur.nom, cout_usd=round(rep.cout_usd, 4))


def _exiger(moteur: ClientLLM) -> None:
    if moteur is None or getattr(moteur, "nom", "aucun") == "aucun":
        raise RuntimeError("Aucune IA n'est configurée.")


QUESTION_SYNTHESE = """Rédige une synthèse du DCE pour aider la direction à décider GO / NO GO, en 6 rubriques courtes :
1. Objet et périmètre (lots, nature des travaux)
2. Calendrier (remise, questions, visite, délai d'exécution)
3. Exigences qui sortent de l'ordinaire (qualifications, pénalités, clauses éliminatoires)
4. Contraintes de chantier (site occupé, horaires, interfaces avec d'autres lots)
5. Exigences RSE et sociales
6. Points à clarifier avec l'acheteur (informations absentes ou contradictoires)
Une à quatre puces par rubrique, chacune appuyée sur le document."""


def synthese_dce(c: Consultation, pieces: list[Piece], moteur: ClientLLM) -> ReponseIA:
    _exiger(moteur)
    pages = pages_pertinentes(pieces, QUESTION_SYNTHESE, moteur.contexte_max, prioritaires=True)
    rep = moteur.texte_cite(REGLES, QUESTION_SYNTHESE, pages)
    return _reponse("synthese", "Synthèse du DCE pour la décision GO / NO GO", rep, moteur, pieces)


def question_dce(pieces: list[Piece], question: str, moteur: ClientLLM) -> ReponseIA:
    _exiger(moteur)
    pages = pages_pertinentes(pieces, question, moteur.contexte_max)
    rep = moteur.texte_cite(REGLES + "\nRéponds de façon brève et précise.", question, pages)
    return _reponse("question", question, rep, moteur, pieces)


CONSIGNE_MEMOIRE = REGLES + """
Tu rédiges le brouillon d'une section du mémoire technique. Matériaux autorisés :
les extraits d'anciens mémoires de l'entreprise et les exigences du DCE fournis ci-dessous.
- Adapte les extraits à la consultation, sans ajouter de moyens, de chiffres, de noms ou de références qui n'y sont pas.
- Pour chaque information manquante (effectifs, dates, matériels précis...), écris [À COMPLÉTER : ce qu'il faut préciser].
- Réponds aux exigences du DCE listées quand les extraits le permettent ; sinon, signale-le par un [À COMPLÉTER].
- Rends uniquement le texte de la section, en paragraphes, sans titre."""


def brouillon_section(c: Consultation, section: SectionMemoire, moteur: ClientLLM) -> ReponseIA:
    _exiger(moteur)
    critere = section.titre + (f" ({section.ponderation:g} % de la note)" if section.ponderation else "")
    mots = set(jetons(section.titre))
    exigences = [e for e in c.exigences if mots & set(jetons(e.texte))][:15]
    passages = section.passages_proposes[:5]
    contenu = [f"Consultation : {c.fiche.intitule.valeur or c.fiche.objet.valeur or 'non renseignée'}",
               f"Section à rédiger : {critere}", "", "Exigences du DCE liées à cette section :"]
    contenu += [f"- {e.texte} ({e.source.fichier} p.{e.source.page})" for e in exigences] or ["- aucune repérée"]
    contenu += ["", "Extraits d'anciens mémoires de l'entreprise :"]
    contenu += [f"[{i}] ({pa.affaire}) {pa.texte}" for i, pa in enumerate(passages, 1)] or ["- aucun"]
    if section.contenu:
        contenu += ["", "Texte déjà rédigé par SIE (à conserver et compléter) :", section.contenu]
    rep = moteur.texte(CONSIGNE_MEMOIRE, "\n".join(contenu))
    if rep.erreur:
        raise RuntimeError(rep.erreur)
    return ReponseIA(type="memoire", question=f"Brouillon : {section.titre}", texte=rep.texte, moteur=moteur.nom,
                     cout_usd=round(rep.cout_usd, 4),
                     citations=[CitationIA(fichier=f"archives/{pa.affaire}", page=pa.source.page, extrait=pa.texte[:200], verifiee=True)
                                for pa in passages])
