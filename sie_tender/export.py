"""Exports : JSON (format d'échange avec le groupe 2), Excel, Markdown et Word."""
from __future__ import annotations

import json
from pathlib import Path

from .models import Consultation, Source, Statut

LIBELLES = {
    "reference": "Référence", "intitule": "Intitulé", "objet": "Objet", "lieu": "Lieu d'exécution",
    "maitre_ouvrage": "Maître d'ouvrage", "maitre_oeuvre": "Maître d'œuvre", "procedure": "Procédure",
    "date_limite_remise": "Date et heure limites de remise", "date_limite_questions": "Date limite des questions",
    "visite": "Visite", "visite_obligatoire": "Visite obligatoire", "contacts": "Contacts",
    "duree_validite_offres": "Délai de validité des offres",
}
STATUTS = {Statut.EXTRAIT: "extrait", Statut.A_VERIFIER: "À VÉRIFIER", Statut.CORRIGE: "corrigé par SIE", Statut.VALIDE: "validé"}


def ref(src: Source | None) -> str:
    if src is None:
        return ""
    morceaux = [src.fichier]
    if src.page:
        morceaux.append(f"p. {src.page}")
    if src.article:
        morceaux.append(f"art. {src.article}")
    return ", ".join(morceaux)


def exporter_json(c: Consultation, chemin: Path) -> None:
    chemin.write_text(c.model_dump_json(indent=2), encoding="utf-8")


def exporter_excel(c: Consultation, chemin: Path) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    jaune = PatternFill("solid", fgColor="FFF2CC")

    def feuille(titre, entetes, lignes, largeurs):
        ws = wb.create_sheet(titre)
        ws.append(entetes)
        for cell in ws[1]:
            cell.font = Font(bold=True)
        for l in lignes:
            ws.append(l)
            if any(isinstance(v, str) and v.startswith("À VÉRIFIER") for v in l):
                for cell in ws[ws.max_row]:
                    cell.fill = jaune
        for i, w in enumerate(largeurs):
            ws.column_dimensions[chr(65 + i)].width = w
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
        ws.freeze_panes = "A2"

    wb.remove(wb.active)
    f = c.fiche
    lignes = [[LIBELLES.get(k, k), ch.valeur or "", STATUTS[ch.statut], ref(ch.source), ch.source.extrait if ch.source else ""]
              for k, ch in f.champs().items()]
    lignes.append(["Lots", "\n".join(f"Lot {l.numero} : {l.intitule}" for l in c.lots), "extrait" if c.lots else "À VÉRIFIER", "", ""])
    lignes.append(["Alertes", "\n".join(f.alertes), "", "", ""])
    lignes.append(["Décision GO / NO GO", f.decision or "(à saisir par la direction)", "", "", ""])
    feuille("Fiche", ["Champ", "Valeur", "Statut", "Source", "Extrait"], lignes, [28, 50, 14, 30, 70])
    feuille("Lots", ["N°", "Intitulé", "Retenu", "Source"],
            [[l.numero, l.intitule, {True: "oui", False: "non", None: "(décision SIE)"}[l.retenu], ref(l.source)] for l in c.lots], [6, 50, 16, 40])
    feuille("Exigences", ["Lot", "Catégorie", "Précision", "Exigence", "Statut", "Source"],
            [[e.lot or "commun", e.categorie.value, e.sous_categorie or "", e.texte, STATUTS[e.statut], ref(e.source)] for e in c.exigences],
            [8, 14, 22, 90, 12, 34])
    crit = []
    for cr in c.criteres:
        crit.append([cr.intitule, cr.ponderation, "", STATUTS[cr.statut], ref(cr.source)])
        for sc in cr.sous_criteres:
            crit.append([f"   {sc.intitule}", "", sc.ponderation, STATUTS[sc.statut], ref(sc.source)])
    feuille("Critères", ["Critère", "Pondération (%)", "Sous-pondération", "Statut", "Source"], crit, [50, 16, 16, 12, 34])
    feuille("Pièces à fournir", ["Étape", "Pièce", "Document proposé (archives)", "Validité", "Statut", "Source"],
            [[p.etape, p.intitule, p.document_propose or "", p.validite.strftime("%d/%m/%Y") if p.validite else "", STATUTS[p.statut], ref(p.source)]
             for p in c.pieces_a_fournir], [12, 40, 40, 12, 12, 34])
    feuille("Pièces du DCE", ["Fichier", "Type", "Lot", "Pages", "Lue", "Remarque"],
            [[p.fichier, p.type.value, p.lot or "commune", p.nb_pages, "oui" if p.lisible else "non", p.remarque or ""] for p in c.pieces],
            [40, 10, 10, 8, 8, 60])
    feuille("Journal", ["Hypothèses, erreurs et limites observées"], [[j] for j in c.journal], [120])
    wb.save(chemin)


def exporter_markdown(c: Consultation, chemin: Path) -> None:
    f = c.fiche
    L = [f"# Analyse du DCE : {f.intitule.valeur or f.objet.valeur or c.dossier}", ""]
    L += ["## Fiche de renseignement (F1)", "", "| Champ | Valeur | Statut | Source |", "| --- | --- | --- | --- |"]
    for k, ch in f.champs().items():
        L.append(f"| {LIBELLES.get(k, k)} | {(ch.valeur or '').replace('|', '/')} | {STATUTS[ch.statut]} | {ref(ch.source)} |")
    L += ["", "**Lots** : " + ("; ".join(f"lot {l.numero} {l.intitule}" for l in c.lots) or "à vérifier"), ""]
    if f.alertes:
        L += ["**Alertes**", ""] + [f"- {a}" for a in f.alertes] + [""]
    L += ["**Décision GO / NO GO** : à saisir par la direction SIE.", ""]
    L += ["## Critères de notation (F2)", ""]
    for cr in c.criteres:
        L.append(f"- {cr.intitule} : {cr.ponderation} % ({ref(cr.source)})")
        L += [f"    - {sc.intitule} : {sc.ponderation} %" for sc in cr.sous_criteres]
    L += ["", "## Exigences par catégorie (F2)", ""]
    for cat in sorted({e.categorie.value for e in c.exigences}):
        L += [f"### {cat}", ""]
        for e in [e for e in c.exigences if e.categorie.value == cat]:
            L.append(f"- [lot {e.lot or 'commun'}] {e.texte} *({ref(e.source)})*")
        L.append("")
    L += ["## Pièces à fournir", ""]
    for p in c.pieces_a_fournir:
        doc = f" → {p.document_propose}" if p.document_propose else ""
        L.append(f"- [{p.etape}] {p.intitule}{doc} ({STATUTS[p.statut]}, {ref(p.source)})")
    L += ["", "## Trame du mémoire technique (F6)", ""]
    for s in c.trame_memoire:
        pond = f" ({s.ponderation} %)" if s.ponderation else ""
        L.append(f"### {s.titre}{pond} : {s.statut.value}")
        if s.note:
            L.append(f"_{s.note}_")
        for pa in s.passages_proposes[:3]:
            L.append(f"- {pa.texte[:300]}… *({ref(pa.source)}, score {pa.score})*")
        L.append("")
    L += ["## Journal", ""] + [f"- {j}" for j in c.journal]
    chemin.write_text("\n".join(L), encoding="utf-8")


def exporter_trame_docx(c: Consultation, chemin: Path) -> None:
    import docx
    from docx.shared import Pt, RGBColor

    d = docx.Document()
    d.add_heading(f"Mémoire technique : {c.fiche.objet.valeur or c.fiche.intitule.valeur or 'consultation'}", 0)
    p = d.add_paragraph("Projet généré par l'outil d'aide à la réponse. Chaque passage est une proposition à relire, "
                        "adapter et valider ; les sources sont indiquées entre crochets.")
    p.runs[0].italic = True
    for s in c.trame_memoire:
        titre = s.titre + (f" ({s.ponderation} %)" if s.ponderation else "")
        d.add_heading(titre, 1)
        if s.contenu:
            for para in s.contenu.split("\n\n"):
                d.add_paragraph(para.strip())
            continue
        if s.note or s.statut.value == "a_completer":
            r = d.add_paragraph().add_run(f"[À COMPLÉTER] {s.note or ''}".strip())
            r.bold = True
            r.font.color.rgb = RGBColor(0xC0, 0x50, 0x00)
        for pa in s.passages_proposes[:3]:
            d.add_paragraph(pa.texte)
            r = d.add_paragraph().add_run(f"[Source : {ref(pa.source)}]")
            r.font.size = Pt(8)
            r.italic = True
    d.save(chemin)


def exporter_tout(c: Consultation, dossier: str | Path) -> dict[str, Path]:
    dossier = Path(dossier)
    dossier.mkdir(parents=True, exist_ok=True)
    chemins = {
        "json": dossier / "consultation.json",
        "excel": dossier / "analyse_dce.xlsx",
        "markdown": dossier / "rapport.md",
        "memoire": dossier / "trame_memoire.docx",
    }
    exporter_json(c, chemins["json"])
    exporter_excel(c, chemins["excel"])
    exporter_markdown(c, chemins["markdown"])
    exporter_trame_docx(c, chemins["memoire"])
    return chemins
