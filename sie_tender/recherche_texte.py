"""Recherche de motifs dans les pièces, avec construction de la source."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Iterator

from .models import Piece, Source, TypePiece
from .texte import article_de, extrait_autour, normaliser


@dataclass
class Trouvaille:
    valeur: str
    source: Source
    match: re.Match


def pieces_par_priorite(pieces: Iterable[Piece], ordre: Iterable[TypePiece]) -> list[Piece]:
    pieces = [p for p in pieces if p.lisible]
    ordre = list(ordre)
    rang = {t: i for i, t in enumerate(ordre)}
    retenues = [p for p in pieces if p.type in rang]
    return sorted(retenues, key=lambda p: (rang[p.type], p.fichier))


def source(piece: Piece, page_num: int, texte_page: str, debut: int, fin: int) -> Source:
    return Source(
        piece_id=piece.id,
        fichier=piece.fichier,
        page=page_num,
        article=article_de(texte_page, debut),
        extrait=extrait_autour(texte_page, debut, fin),
    )


# Ligne coupée par la mise en page du PDF : la suite commence par une minuscule.
_SUITE = re.compile(r"\n(?P<suite>[a-zà-ÿ(][^\n.]{0,150})")


def _avec_suite(texte: str, m: re.Match, valeur: str) -> str:
    fin = m.end("v")
    if valeur and valeur[-1] not in ".;:!?" and (s := _SUITE.match(texte, fin)):
        return valeur + " " + s.group("suite")
    return valeur


def iter_matches(pieces: list[Piece], motif: re.Pattern) -> Iterator[Trouvaille]:
    for piece in pieces:
        for page in piece.pages:
            for m in motif.finditer(page.texte):
                valeur = (m.group("v") if "v" in motif.groupindex else m.group(0)) or ""
                if "v" in motif.groupindex and m.group("v"):
                    valeur = _avec_suite(page.texte, m, valeur)
                valeur = re.sub(r"\s+", " ", valeur).strip(" :;,.-–")
                yield Trouvaille(valeur, source(piece, page.numero, page.texte, m.start(), m.end()), m)


def premier(pieces: list[Piece], motifs: list[re.Pattern]) -> Trouvaille | None:
    """Première trouvaille non vide, en essayant les motifs dans l'ordre."""
    for motif in motifs:
        for t in iter_matches(pieces, motif):
            if t.valeur:
                return t
    return None


def extrait_present(src: Source, pieces: list[Piece]) -> bool:
    """Vérifie qu'un extrait cité (par exemple par un LLM) figure vraiment dans la pièce."""
    if not src.extrait:
        return False
    cible = normaliser(src.extrait)
    if len(cible) < 8:
        return False
    for p in pieces:
        if p.id != src.piece_id and p.fichier != src.fichier:
            continue
        for page in p.pages:
            if src.page and page.numero != src.page:
                continue
            if cible in normaliser(page.texte):
                return True
    return False
