"""Outils texte : normalisation, découpage en phrases et en articles."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


def sans_accents(texte: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texte) if unicodedata.category(c) != "Mn")


def normaliser(texte: str) -> str:
    """Minuscules, sans accents, espaces simples : sert aux comparaisons."""
    texte = sans_accents(texte).lower()
    texte = texte.replace("’", "'").replace(" ", " ")
    return re.sub(r"\s+", " ", texte).strip()


def nettoyer(texte: str) -> str:
    texte = texte.replace(" ", " ").replace("’", "'")
    texte = re.sub(r"-\n(\w)", r"\1", texte)  # césures en fin de ligne
    return re.sub(r"[ \t]+", " ", texte)


# --- Articles -----------------------------------------------------------------
# Titre explicite : « Article 4.2 - Titre », « ARTICLE 4 : Titre », « Art. 4 Titre »
_ARTICLE_EXPLICITE = re.compile(
    r"^\s*(?:article|art\.)\s*(\d{1,2}(?:\.\d{1,2}){0,3})\s*[-–:.)]?\s*([^\n]{0,120})$", re.IGNORECASE | re.MULTILINE)
# Titre numéroté : « 4.2 Titre » ou « 4. Titre », court et sans point final
_ARTICLE_NUMERO = re.compile(
    r"^\s*(\d{1,2}(?:\.\d{1,2}){0,3})\s*[-–.)]?\s+([A-ZÀ-Ý][^\n]{2,90}?)\s*$", re.MULTILINE)


def _titres(texte: str, explicite: bool) -> list[re.Match]:
    if explicite:
        return list(_ARTICLE_EXPLICITE.finditer(texte))
    out = []
    for m in _ARTICLE_NUMERO.finditer(texte):
        titre = m.group(2)
        if titre.endswith((".", ";", ",")) or len(titre.split()) > 12 or re.search(r"\d\s*%|\bpoints?\b", titre):
            continue
        out.append(m)
    return out


@dataclass
class Article:
    numero: str
    titre: str
    texte: str
    page: int


def articles(pages: list[tuple[int, str]]) -> list[Article]:
    """Découpe une pièce en articles, en gardant la page de début.

    Si la pièce contient des titres « Article N », seuls ceux-là comptent ; sinon on
    se rabat sur les titres numérotés (« 2. Gestion des déchets »).
    """
    explicite = any(_ARTICLE_EXPLICITE.search(t) for _, t in pages)
    resultat: list[Article] = []
    courant: Article | None = None
    for numero_page, texte in pages:
        pos = 0
        for m in _titres(texte, explicite):
            if courant is not None:
                courant.texte += texte[pos:m.start()]
            courant = Article(m.group(1), m.group(2).strip(" -–:"), "", numero_page)
            resultat.append(courant)
            pos = m.end()
        if courant is not None:
            courant.texte += texte[pos:]
    return resultat


def article_de(page_texte: str, position: int) -> str | None:
    """Numéro du dernier article ouvert avant une position dans la page."""
    avant = page_texte[:position]
    titres = _titres(avant, explicite=True) or (_titres(avant, explicite=False) if not _ARTICLE_EXPLICITE.search(page_texte) else [])
    return titres[-1].group(1) if titres else None


# --- Phrases --------------------------------------------------------------------
# Une puce, un titre d'article ou une ligne « Lot N : » commencent une nouvelle unité.
# Un numéro seul en début de ligne (« 2. Les clapets ») n'en commence une que si la
# ligne précédente finissait une phrase : sinon c'est souvent « ... du lot\n2 ... ».
_DEBUT_UNITE = re.compile(r"^\s*(?:article\s*\d|art\.\s*\d|[-•*–]\s|lot\s*(?:n\s*[°o]\s*)?\d{1,2}\s*[:\-–])", re.IGNORECASE)
_TITRE_ARTICLE = re.compile(r"^\s*(?:article|art\.)\s*\d[^\n]{0,100}$", re.IGNORECASE)
_TITRE_MAJUSCULES = re.compile(r"^\s*[A-ZÀ-Ý0-9 ()'’\-–:]{6,}\s*$")
_COUPE = re.compile(r"(?<=[.;!?])\s+(?=[A-ZÀ-Ý«])")


def phrases_positions(texte: str) -> list[tuple[str, int, int]]:
    """Phrases d'une page avec leurs positions (début, fin) dans le texte brut.

    Les retours à la ligne de mise en page sont recollés ; un titre d'article, une
    puce ou une ligne « Lot N » commencent toujours une nouvelle unité.
    """
    lignes = []
    pos = 0
    for l in texte.split("\n"):
        lignes.append((l, pos))
        pos += len(l) + 1
    blocs: list[tuple[int, int]] = []
    debut = fin = None
    for l, p in lignes:
        if not l.strip():
            if debut is not None:
                blocs.append((debut, fin))
                debut = None
            continue
        if _TITRE_ARTICLE.match(l) or _TITRE_MAJUSCULES.match(l):
            if debut is not None:
                blocs.append((debut, fin))
                debut = None
            continue
        if debut is not None and _DEBUT_UNITE.match(l):
            blocs.append((debut, fin))
            debut = None
        if debut is None:
            debut = p
        fin = p + len(l)
        if l.rstrip().endswith((".", ";", "!", "?", ":")) and not re.search(r"\b(?:n°|art|p)\.$", l.rstrip(), re.IGNORECASE):
            blocs.append((debut, fin))
            debut = None
    if debut is not None:
        blocs.append((debut, fin))

    out = []
    for a, b in blocs:
        bloc = texte[a:b]
        curseur = 0
        for m in list(_COUPE.finditer(bloc)) + [None]:
            fin_ph = m.start() if m else len(bloc)
            brut = bloc[curseur:fin_ph]
            ph = re.sub(r"\s+", " ", brut).strip(" -•*–\t")
            # titre numéroté collé au texte : « 8.2 Gestion des déchets. »
            if len(ph.split()) >= 5:
                decal = len(brut) - len(brut.lstrip(" -•*–\t\n"))
                out.append((ph, a + curseur + decal, a + fin_ph))
            curseur = m.end() if m else len(bloc)
    return out


def phrases(texte: str) -> list[str]:
    return [p for p, _, _ in phrases_positions(texte)]


def extrait_autour(texte: str, debut: int, fin: int, marge: int = 80) -> str:
    a = max(0, debut - marge)
    b = min(len(texte), fin + marge)
    return re.sub(r"\s+", " ", texte[a:b]).strip()
