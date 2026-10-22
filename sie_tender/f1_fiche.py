"""F1 : fiche de renseignement pour la décision GO / NO GO.

Deux passes :
1. des règles (expressions régulières) sur le RC, l'AE et le CCAP, qui ne
   peuvent rien inventer ;
2. si un LLM est configuré, il complète les champs restés vides. Chacune de
   ses réponses doit citer un extrait, et l'extrait est recherché dans la
   pièce : s'il n'y est pas, le champ reste « à vérifier ».
La décision GO / NO GO n'est jamais remplie par l'outil.
"""
from __future__ import annotations

import re

from .llm import AucunLLM, ClientLLM
from .models import Champ, FicheRenseignement, Lot, Piece, Source, Statut, TypePiece
from .recherche_texte import extrait_present, iter_matches, pieces_par_priorite, premier
from .texte import normaliser

I = re.IGNORECASE
_MOIS = r"(?:janvier|f[ée]vrier|mars|avril|mai|juin|juillet|ao[uû]t|septembre|octobre|novembre|d[ée]cembre)"
_DATE = rf"(?:(?:lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)\s+)?(?:\d{{1,2}}(?:er)?\s+{_MOIS}\s+\d{{4}}|\d{{1,2}}/\d{{1,2}}/\d{{2,4}})"
_HEURE = r"(?:\s*(?:à|a|,)?\s*\d{1,2}\s*[hH:]\s*\d{0,2}(?:\s*min)?)?"
_DATE_HEURE = rf"(?P<v>{_DATE}{_HEURE})"
_LIGNE = r"(?P<v>[^\n]{3,200})"

MOTIFS: dict[str, list[re.Pattern]] = {
    "reference": [
        re.compile(rf"(?:r[ée]f[ée]rence|n[°o]\s*de\s*(?:la\s*)?consultation|n[°o]\s*(?:du\s*)?march[ée]|march[ée]\s*n[°o])\s*(?:du marché|de la consultation)?\s*:?\s*(?P<v>[A-Z0-9][A-Z0-9_./\-]{{2,40}})", I),
    ],
    "intitule": [
        re.compile(rf"^\s*(?:intitul[ée]\s*(?:de la consultation|du march[ée])?)\s*:\s*{_LIGNE}", I | re.M),
    ],
    "objet": [
        re.compile(rf"objet\s*(?:de la consultation|du march[ée])\s*:\s*{_LIGNE}", I),
        re.compile(r"(?:la (?:pr[ée]sente )?consultation|le (?:pr[ée]sent )?march[ée])\s+(?:porte sur|a pour objet)\s+(?P<v>[^\n.]{5,250})", I),
    ],
    "lieu": [
        re.compile(rf"(?:lieu\s*d'ex[ée]cution|adresse du chantier|lieu des travaux|site d'ex[ée]cution)\s*(?:des travaux|des prestations)?\s*:\s*{_LIGNE}", I),
    ],
    "maitre_ouvrage": [
        re.compile(rf"(?:ma[iî]tre\s*d'ouvrage|ma[iî]trise\s*d'ouvrage|pouvoir adjudicateur|acheteur public|nom de l'acheteur)\s*:\s*{_LIGNE}", I),
    ],
    "maitre_oeuvre": [
        re.compile(rf"(?:ma[iî]tre\s*d'(?:œ|oe)uvre|ma[iî]trise\s*d'(?:œ|oe)uvre)\s*(?:\(MOE\))?\s*:\s*{_LIGNE}", I),
    ],
    "procedure": [
        re.compile(r"(?P<v>proc[ée]dure adapt[ée]e(?: ouverte)?|appel d'offres (?:ouvert|restreint)|proc[ée]dure (?:avec|sans) n[ée]gociation|march[ée] [àa] proc[ée]dure adapt[ée]e|\bMAPA\b)", I),
    ],
    "date_limite_remise": [
        re.compile(rf"(?:date\s*(?:et heure)?\s*limites?\s*(?:de\s*)?(?:r[ée]ception|remise|d[ée]p[oô]t)\s*des\s*(?:offres|plis|candidatures))[^\n:]{{0,40}}:?\s*(?:le\s*)?{_DATE_HEURE}", I),
        re.compile(rf"(?:offres|plis)\s*(?:devront|doivent)\s*(?:[eê]tre\s*)?(?:remis|d[ée]pos[ée]s|parvenir|transmis)[^.\n]{{0,80}}?(?:avant\s*le|au plus tard\s*le|le)\s*{_DATE_HEURE}", I),
    ],
    "date_limite_questions": [
        re.compile(rf"(?:questions|demandes? de renseignements?(?: compl[ée]mentaires)?)[^.\n]{{0,120}}?(?:au plus tard\s*(?:le)?|avant\s*le|jusqu'au)\s*{_DATE_HEURE}", I),
        re.compile(r"(?:questions|demandes? de renseignements?(?: compl[ée]mentaires)?)[^.\n]{0,120}?(?P<v>au plus tard\s+\d{1,2}\s+jours?[^.\n]{0,60})", I),
    ],
    "visite": [
        re.compile(rf"visite[\s\S]{{0,160}}?(?:aura lieu|organis[ée]e|pr[ée]vue|se d[ée]roulera|fix[ée]e)\s*(?:le\s*)?{_DATE_HEURE}", I),
        re.compile(r"(?P<v>visite (?:des lieux|de site|du site)?[^.\n]{0,40}(?:obligatoire|facultative|conseill[ée]e|recommand[ée]e)[^.\n]{0,120})", I),
    ],
    "contacts": [
        re.compile(r"(?P<v>[\w.+-]+@[\w-]+\.[\w.-]+)"),
    ],
    "duree_validite_offres": [
        re.compile(r"d[ée]lai de validit[ée] des offres[^.\n]{0,40}?(?P<v>\d{2,3}\s*jours)", I),
    ],
}

_ORDRE = [TypePiece.RC, TypePiece.AE, TypePiece.CCAP, TypePiece.ANNEXE, TypePiece.CCTP, TypePiece.AUTRE]

_LOT_LIGNE = re.compile(
    r"^\s*(?:[-•*]\s*)?lot\s*(?:n\s*[°o]\s*)?(?P<num>\d{1,2})\s*[:\-–.)]?\s*(?P<intitule>[A-ZÀ-Ýa-zà-ý][^\n]{2,100})$",
    I | re.M,
)

_ALERTES = [
    (re.compile(r"visite[^.\n]{0,60}obligatoire", I), "Visite obligatoire"),
    (re.compile(r"sous peine (?:d'irrecevabilit[ée]|de rejet|d'[ée]limination|de nullit[ée])", I), "Cause d'irrecevabilité mentionnée"),
    (re.compile(r"(?:irrecevable|sera rejet[ée]e|seront rejet[ée]es|sera [ée]limin[ée]e)", I), "Cas de rejet de l'offre"),
    (re.compile(r"(?:qualification|certification)\s+(?:qualifelec|qualibat|apsad|mase)[^.\n]{0,60}(?:exig[ée]e|obligatoire|requise)", I), "Qualification exigée"),
    (re.compile(r"(?:dat[ée]e?s? et sign[ée]e?s?|sign[ée]e?s? (?:par|[ée]lectroniquement))", I), "Pièce à signer"),
    (re.compile(r"attestation de visite", I), "Attestation de visite à joindre"),
]


def _champ(t) -> Champ:
    if t is None:
        return Champ.absent()
    return Champ(valeur=t.valeur, source=t.source, statut=Statut.EXTRAIT, methode="regle")


def extraire_lots(pieces: list[Piece]) -> list[Lot]:
    lots: dict[str, Lot] = {}
    for t in iter_matches(pieces_par_priorite(pieces, _ORDRE), _LOT_LIGNE):
        num = str(int(t.match.group("num")))
        intitule = re.sub(r"\s+", " ", t.match.group("intitule")).strip(" :;,.-–")
        # « Lot 3 sera attribué... » n'est pas une ligne de liste.
        if re.match(r"(?:sera|est|doit|pour|du|de la|au|les|des)\b", intitule, I):
            continue
        if num not in lots:
            lots[num] = Lot(numero=num, intitule=intitule, source=t.source)
    # Pièces techniques par lot sans ligne de liste : on garde le lot, intitulé à vérifier.
    for p in pieces:
        if p.lot and p.lot not in lots:
            lots[p.lot] = Lot(numero=p.lot, intitule="(intitulé à vérifier)",
                              source=Source(piece_id=p.id, fichier=p.fichier, extrait="numéro de lot lu dans le nom du fichier"))
    return sorted(lots.values(), key=lambda l: int(l.numero))


def _alertes(pieces: list[Piece]) -> list[str]:
    vues: list[str] = []
    for motif, libelle in _ALERTES:
        t = premier(pieces_par_priorite(pieces, _ORDRE), [motif])
        if t:
            ou = f"{t.source.fichier} p.{t.source.page}"
            vues.append(f"{libelle} ({ou}) : « {t.source.extrait} »")
    return vues


CONSIGNE_LLM = """Tu aides un bureau d'études à remplir une fiche de renseignement d'appel d'offres.
On te donne des extraits de pièces numérotées. Pour chaque champ demandé, réponds par
{"champs": {"<nom>": {"valeur": "...", "fichier": "...", "page": N, "extrait": "citation exacte"}}}.
L'extrait doit être recopié mot pour mot depuis le texte fourni (10 à 30 mots).
Si l'information n'apparaît pas, mets "valeur": null. N'invente rien, ne déduis rien."""


def _completer_llm(fiche: FicheRenseignement, pieces: list[Piece], llm: ClientLLM, journal: list[str]) -> None:
    manquants = [k for k, c in fiche.champs().items() if c.statut == Statut.A_VERIFIER]
    if not manquants or isinstance(llm, AucunLLM):
        return
    pertinentes = pieces_par_priorite(pieces, [TypePiece.RC, TypePiece.AE, TypePiece.CCAP])
    morceaux, taille = [], 0
    for p in pertinentes:
        for page in p.pages:
            bloc = f"\n=== {p.fichier} | page {page.numero} ===\n{page.texte}"
            if taille + len(bloc) > 60000:
                break
            morceaux.append(bloc)
            taille += len(bloc)
    if not morceaux:
        return
    rep = llm.json(CONSIGNE_LLM, f"Champs à trouver : {', '.join(manquants)}\n" + "".join(morceaux))
    if not rep or not isinstance(rep.get("champs"), dict):
        journal.append(f"F1 : le LLM ({llm.nom}) n'a pas renvoyé de réponse exploitable.")
        return
    for nom, r in rep["champs"].items():
        if nom not in manquants or not isinstance(r, dict) or not r.get("valeur"):
            continue
        piece = next((p for p in pertinentes if p.fichier == r.get("fichier")), None)
        if piece is None:
            journal.append(f"F1 : champ « {nom} » proposé par le LLM sans fichier valide, ignoré.")
            continue
        page = r.get("page") if isinstance(r.get("page"), int) else None
        src = Source(piece_id=piece.id, fichier=piece.fichier, page=page, extrait=str(r.get("extrait", "")))
        if extrait_present(src, pieces):
            setattr(fiche, nom, Champ(valeur=str(r["valeur"]), source=src, statut=Statut.EXTRAIT, methode="llm"))
        else:
            setattr(fiche, nom, Champ(valeur=str(r["valeur"]), source=src, statut=Statut.A_VERIFIER, methode="llm"))
            journal.append(f"F1 : citation du LLM introuvable pour « {nom} », champ laissé à vérifier.")


def construire_fiche(pieces: list[Piece], llm: ClientLLM | None = None, journal: list[str] | None = None) -> tuple[FicheRenseignement, list[Lot]]:
    journal = journal if journal is not None else []
    llm = llm or AucunLLM()
    ordonnees = pieces_par_priorite(pieces, _ORDRE)
    fiche = FicheRenseignement()
    for nom, motifs in MOTIFS.items():
        setattr(fiche, nom, _champ(premier(ordonnees, motifs)))

    # Contacts : tous les courriels distincts, pas seulement le premier.
    courriels = []
    for t in iter_matches(ordonnees, MOTIFS["contacts"][0]):
        if t.valeur.lower() not in [c.lower() for c in courriels]:
            courriels.append(t.valeur)
    if courriels:
        fiche.contacts.valeur = ", ".join(courriels[:5])

    # Visite obligatoire : oui / non / à vérifier, avec la source.
    t_obl = premier(ordonnees, [re.compile(r"visite[^.\n]{0,60}(?P<v>obligatoire|facultative|conseill[ée]e|recommand[ée]e)", I)])
    if t_obl:
        fiche.visite_obligatoire = Champ(valeur="oui" if normaliser(t_obl.valeur) == "obligatoire" else "non",
                                         source=t_obl.source, statut=Statut.EXTRAIT)

    if fiche.intitule.statut == Statut.A_VERIFIER and fiche.objet.statut == Statut.EXTRAIT:
        fiche.intitule = fiche.objet.model_copy()

    _completer_llm(fiche, pieces, llm, journal)
    fiche.alertes = _alertes(pieces)
    lots = extraire_lots(pieces)
    if not lots:
        journal.append("F1 : aucune liste de lots trouvée (marché à lot unique ?), à vérifier.")
    if not any(p.type == TypePiece.RC for p in pieces):
        journal.append("F1 : aucun règlement de consultation reconnu dans le DCE.")
    return fiche, lots
