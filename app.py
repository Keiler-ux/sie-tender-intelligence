"""Interface de l'outil (Streamlit) : une page par étape du parcours SIE.

    streamlit run app.py

Variables d'environnement utiles :
    SIE_ESPACE   dossier des affaires (défaut : affaires)
    SIE_INDEX    index des archives créé par « python -m sie_tender indexer » (défaut : archives_index.json)
    SIE_LLM      aucun | ollama | anthropic (voir sie_tender/llm.py)
"""
from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path

import pandas as pd
import streamlit as st

from sie_tender.affaire import EspaceTravail
from sie_tender.export import LIBELLES, STATUTS, ref
from sie_tender.f3_quantites import importer_quantites
from sie_tender.f4_cotations import exporter_demandes, generer_demandes, texte_courriel
from sie_tender.f5_dpgf import associer, construire_dpgf, exporter_dpgf, importer_devis
from sie_tender.f6_archives import Bibliotheque
from sie_tender.f7_controle import controler, exporter_dossier
from sie_tender.models import Champ, Statut, StatutSection
from sie_tender.pipeline import analyser_affaire

st.set_page_config(page_title="SIE · Aide aux appels d'offres", layout="wide")

ESPACE = EspaceTravail(os.getenv("SIE_ESPACE", "affaires"))
INDEX = Path(os.getenv("SIE_INDEX", "archives_index.json"))
ETAPES = ["Affaires", "1. Fiche GO / NO GO", "2. Analyse par lot", "3. Quantités", "4. Cotations",
          "5. Devis et DPGF", "6. Mémoire technique", "7. Contrôle et dossier", "Archives"]


@st.cache_resource
def bibliotheque(chemin: str, mtime: float) -> Bibliotheque | None:
    return Bibliotheque.charger(chemin) if Path(chemin).exists() else None


def biblio() -> Bibliotheque | None:
    return bibliotheque(str(INDEX), INDEX.stat().st_mtime if INDEX.exists() else 0)


def affaire_courante():
    aid = st.session_state.get("affaire")
    if not aid:
        st.info("Choisis ou crée une affaire dans la page « Affaires ».")
        st.stop()
    return ESPACE.ouvrir(aid)


def telecharger(label: str, chemin: Path, cle: str) -> None:
    st.download_button(label, chemin.read_bytes(), file_name=chemin.name, key=cle)


# --- Barre latérale ---------------------------------------------------------------
with st.sidebar:
    st.title("SIE · Appels d'offres")
    page = st.radio("Étape", ETAPES, label_visibility="collapsed")
    st.divider()
    utilisateur = st.text_input("Ton nom (pour les validations)", value=st.session_state.get("utilisateur", ""))
    st.session_state["utilisateur"] = utilisateur
    if st.session_state.get("affaire"):
        st.caption(f"Affaire ouverte : {st.session_state['affaire']}")
    st.caption(f"LLM : {os.getenv('SIE_LLM', 'aucun')} · archives : {'indexées' if INDEX.exists() else 'non indexées'}")


# --- Affaires -----------------------------------------------------------------------
if page == "Affaires":
    st.header("Affaires")
    affaires = ESPACE.lister()
    if affaires:
        choix = st.selectbox("Ouvrir une affaire", affaires, format_func=lambda x: f"{x[1]} ({x[0]})")
        if st.button("Ouvrir"):
            st.session_state["affaire"] = choix[0]
            st.success(f"Affaire {choix[1]} ouverte.")
    st.subheader("Nouvelle affaire : déposer le DCE")
    fichiers = st.file_uploader("Pièces du DCE (PDF, Word, Excel, plans) ou une archive ZIP", accept_multiple_files=True)
    if st.button("Créer et analyser", disabled=not fichiers):
        a = ESPACE.creer()
        for f in fichiers:
            if f.name.lower().endswith(".zip"):
                with zipfile.ZipFile(io.BytesIO(f.getvalue())) as z:
                    for info in z.infolist():
                        if not info.is_dir():
                            a.deposer("dce", Path(info.filename).name, z.read(info))
            else:
                a.deposer("dce", f.name, f.getvalue())
        with st.spinner("Lecture et analyse du DCE…"):
            analyser_affaire(a, biblio())
        st.session_state["affaire"] = a.id
        st.success(f"Affaire créée : {a.c.fiche.intitule.valeur or a.id}. Passe à l'étape 1.")

# --- 1. Fiche ---------------------------------------------------------------------------
elif page == ETAPES[1]:
    a = affaire_courante()
    c = a.c
    st.header("Fiche de renseignement")
    st.caption("Les champs en « À VÉRIFIER » n'ont pas été trouvés ou sont incertains : l'outil n'invente rien.")
    lignes = [{"cle": k, "Champ": LIBELLES.get(k, k), "Valeur": ch.valeur or "", "Statut": STATUTS[ch.statut],
               "Source": ref(ch.source), "Extrait": ch.source.extrait if ch.source else ""} for k, ch in c.fiche.champs().items()]
    df = st.data_editor(pd.DataFrame(lignes), disabled=["cle", "Champ", "Statut", "Source", "Extrait"], hide_index=True,
                        column_config={"cle": None}, width='stretch', key="fiche")
    if c.fiche.alertes:
        st.subheader("Alertes")
        for al in c.fiche.alertes:
            st.warning(al)
    st.subheader("Lots")
    lots_df = st.data_editor(pd.DataFrame([{"N°": l.numero, "Intitulé": l.intitule, "Retenu": bool(l.retenu),
                                            "Responsable": l.responsable or "", "Source": ref(l.source)} for l in c.lots]),
                             disabled=["N°", "Source"], hide_index=True, width='stretch', key="lots")
    st.subheader("Décision de la direction")
    col1, col2 = st.columns(2)
    decision = col1.radio("Décision", ["(non décidée)", "GO", "NO GO"],
                          index=["(non décidée)", "GO", "NO GO"].index(c.fiche.decision or "(non décidée)"), horizontal=True)
    responsable_visite = col2.text_input("Responsable de la visite", value=c.lots[0].responsable if c.lots and c.lots[0].responsable else "")
    if st.button("Enregistrer et valider la fiche", type="primary"):
        for _, r in df.iterrows():
            ch: Champ = getattr(c.fiche, r["cle"])
            if (r["Valeur"] or None) != ch.valeur:
                ch.valeur, ch.statut, ch.methode = r["Valeur"] or None, Statut.CORRIGE, "saisie"
        for l, (_, r) in zip(c.lots, lots_df.iterrows()):
            l.intitule, l.retenu, l.responsable = r["Intitulé"], bool(r["Retenu"]), r["Responsable"] or responsable_visite or None
        if decision != "(non décidée)":
            c.fiche.decision, c.fiche.decideur = decision, utilisateur or "?"
            c.fiche.date_decision = pd.Timestamp.now().to_pydatetime()
        a.valider("fiche", "fiche", utilisateur or "?")
        a.sauver()
        st.success("Fiche enregistrée.")
    from sie_tender.export import exporter_excel
    if st.button("Exporter la fiche et l'analyse (Excel)"):
        exporter_excel(c, a.dossier("exports") / "analyse_dce.xlsx")
    if (a.dossier("exports") / "analyse_dce.xlsx").exists():
        telecharger("Télécharger analyse_dce.xlsx", a.dossier("exports") / "analyse_dce.xlsx", "dl_fiche")

# --- 2. Analyse ---------------------------------------------------------------------------
elif page == ETAPES[2]:
    a = affaire_courante()
    c = a.c
    st.header("Analyse des exigences par lot")
    retenus = [l.numero for l in c.lots if l.retenu]
    if not retenus:
        st.info("Aucun lot retenu pour l'instant : toutes les exigences sont affichées.")
    lots = st.multiselect("Lots", sorted({e.lot or "commun" for e in c.exigences}),
                          default=[x for x in sorted({e.lot or "commun" for e in c.exigences}) if not retenus or x in retenus + ["commun"]])
    cats = st.multiselect("Catégories", sorted({e.categorie.value for e in c.exigences}), default=sorted({e.categorie.value for e in c.exigences}))
    ex = [e for e in c.exigences if (e.lot or "commun") in lots and e.categorie.value in cats]
    st.dataframe(pd.DataFrame([{"Lot": e.lot or "commun", "Catégorie": e.categorie.value, "Précision": e.sous_categorie or "",
                                "Exigence": e.texte, "Source": ref(e.source)} for e in ex]), width='stretch', hide_index=True)
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Critères de notation")
        for cr in c.criteres:
            st.markdown(f"**{cr.intitule}** : {cr.ponderation:g} %  \n<small>{ref(cr.source)}</small>", unsafe_allow_html=True)
            for sc in cr.sous_criteres:
                st.markdown(f"- {sc.intitule} : {sc.ponderation:g} %")
        if not c.criteres:
            st.warning("Critères non trouvés dans le RC : à saisir.")
    with col2:
        st.subheader("Pièces à fournir")
        st.dataframe(pd.DataFrame([{"Étape": p.etape, "Pièce": p.intitule, "Document proposé": p.document_propose or "",
                                    "Validité": p.validite.strftime("%d/%m/%Y") if p.validite else "", "Statut": STATUTS[p.statut]}
                                   for p in c.pieces_a_fournir]), hide_index=True, width='stretch')
    with st.expander("Journal (hypothèses, erreurs, limites)"):
        for j in c.journal:
            st.write("- " + j)

# --- 3. Quantités ---------------------------------------------------------------------------
elif page == ETAPES[3]:
    a = affaire_courante()
    c = a.c
    st.header("Quantités du bureau d'études")
    st.caption("Import du fichier Excel de métré. Aucune quantité n'est calculée à partir des plans.")
    f = st.file_uploader("Fichier Excel de métré", type=["xlsx", "xlsm"])
    lot_defaut = st.text_input("Lot par défaut (si la feuille ne l'indique pas)", value=next((l.numero for l in c.lots if l.retenu), ""))
    if f and st.button("Importer"):
        chemin = a.deposer("quantites", f.name, f.getvalue())
        lignes, journal = importer_quantites(chemin, lot_defaut or None)
        c.quantites += lignes
        c.journal += journal
        a.sauver()
        st.success(journal[-1])
    if c.quantites:
        df = pd.DataFrame([{"id": q.id, "Lot": q.lot, "N°": q.code, "Famille": q.famille, "Désignation": q.designation,
                            "Unité": q.unite, "Quantité": q.quantite, "Remarque": q.remarque or ""} for q in c.quantites])
        df2 = st.data_editor(df, column_config={"id": None}, disabled=["Remarque"], hide_index=True, width='stretch', num_rows="dynamic")
        if st.button("Enregistrer et valider le métré", type="primary"):
            par_id = {q.id: q for q in c.quantites}
            nouvelles = []
            for _, r in df2.iterrows():
                q = par_id.get(r["id"]) if isinstance(r["id"], str) else None
                if q is None:
                    from sie_tender.models import LigneQuantite
                    q = LigneQuantite(designation=str(r["Désignation"]))
                q.lot, q.code, q.famille, q.designation, q.unite = r["Lot"], r["N°"], r["Famille"], r["Désignation"], r["Unité"]
                q.quantite = None if pd.isna(r["Quantité"]) else float(r["Quantité"])
                ok = q.quantite is not None and q.quantite > 0 and q.unite
                q.statut, q.remarque = (Statut.VALIDE, None) if ok else (Statut.A_VERIFIER, q.remarque or "quantité ou unité manquante")
                nouvelles.append(q)
            c.quantites = nouvelles
            a.valider("quantites", "quantites", utilisateur or "?")
            a.sauver()
            st.success("Métré enregistré.")

# --- 4. Cotations ---------------------------------------------------------------------------
elif page == ETAPES[4]:
    a = affaire_courante()
    c = a.c
    st.header("Demandes de cotation fournisseurs")
    st.caption("Brouillons à relire. Le choix des fournisseurs et l'envoi restent faits par SIE.")
    delai = st.number_input("Retour des devis : jours avant la date limite de remise", 3, 30, 10)
    if st.button("Générer les demandes", disabled=not c.quantites):
        c.demandes_cotation = generer_demandes(c, delai)
        a.sauver()
    for d in c.demandes_cotation:
        with st.expander(f"Lot {d.lot or '?'} · {d.famille} · {len(d.lignes)} lignes · retour avant {d.echeance_retour or 'à fixer'}"):
            st.dataframe(pd.DataFrame([{"Désignation": l.designation, "Unité": l.unite, "Quantité": l.quantite,
                                        "Caractéristiques (CCTP)": "\n".join(l.caracteristiques)} for l in d.lignes]),
                         hide_index=True, width='stretch')
            st.text_area("Brouillon de courriel", texte_courriel(c, d), height=260, key=f"mail_{d.id}")
    if c.demandes_cotation:
        chemin = exporter_demandes(c, a.dossier("exports") / "demandes_cotation.xlsx")
        telecharger("Télécharger les demandes (Excel)", chemin, "dl_cot")

# --- 5. Devis et DPGF ---------------------------------------------------------------------------
elif page == ETAPES[5]:
    a = affaire_courante()
    c = a.c
    st.header("Devis fournisseurs et DPGF provisoire")
    col1, col2, col3 = st.columns(3)
    f = col1.file_uploader("Devis (Excel, CSV ou PDF)", type=["xlsx", "csv", "pdf"])
    fournisseur = col2.text_input("Fournisseur")
    lot = col3.text_input("Lot", value=next((l.numero for l in c.lots if l.retenu), ""))
    if f and st.button("Importer le devis"):
        chemin = a.deposer("devis", f.name, f.getvalue())
        d = importer_devis(chemin, fournisseur or None, lot or None)
        associer(c, d)
        c.devis.append(d)
        a.sauver()
        st.success(f"{len(d.lignes)} lignes lues, {sum(1 for l in d.lignes if l.ligne_quantite_id)} associées automatiquement.")
    postes = {q.id: f"{q.code or ''} {q.designation}".strip() for q in c.quantites}
    options = ["(aucun)"] + list(postes.values())
    inverse = {v: k for k, v in postes.items()}
    for d in c.devis:
        with st.expander(f"{d.fournisseur} · {d.fichier} · {len(d.lignes)} lignes"):
            df = pd.DataFrame([{"Désignation devis": l.designation, "Unité": l.unite, "Qté": l.quantite, "PU HT": l.prix_unitaire,
                                "Poste associé": postes.get(l.ligne_quantite_id, "(aucun)"), "Score": l.score_association,
                                "Validé": l.association_validee} for l in d.lignes])
            df2 = st.data_editor(df, column_config={"Poste associé": st.column_config.SelectboxColumn(options=options)},
                                 disabled=["Désignation devis", "Score"], hide_index=True, width='stretch', key=f"dv_{d.id}")
            if st.button("Enregistrer les associations", key=f"save_{d.id}"):
                for l, (_, r) in zip(d.lignes, df2.iterrows()):
                    l.ligne_quantite_id = inverse.get(r["Poste associé"])
                    l.prix_unitaire = None if pd.isna(r["PU HT"]) else float(r["PU HT"])
                    l.association_validee = bool(r["Validé"])
                a.sauver()
                st.success("Associations enregistrées.")
    st.subheader("Coefficients (saisis par SIE)")
    st.caption("L'outil ne choisit ni prix ni marge : sans ces valeurs, le prix de vente reste vide.")
    k = c.coefficients
    cols = st.columns(4)
    vals = [cols[i].number_input(lbl, value=v, min_value=0.0, step=0.5, format="%.2f", key=f"k{i}")
            for i, (lbl, v) in enumerate([("Taux horaire €/h", k.taux_horaire), ("Frais généraux %", k.frais_generaux),
                                          ("Aléas %", k.aleas), ("Marge %", k.marge)])]
    modele = st.file_uploader("Modèle de DPGF SIE ou de l'acheteur (facultatif)", type=["xlsx"])
    if st.button("Construire la DPGF", type="primary", disabled=not c.quantites):
        k.taux_horaire, k.frais_generaux, k.aleas, k.marge = vals
        c.dpgf = construire_dpgf(c)
        a.sauver()
    if c.dpgf:
        df = pd.DataFrame([{"id": p.ligne_quantite_id, "Lot": p.lot, "N°": p.code, "Désignation": p.designation, "Unité": p.unite,
                            "Qté": p.quantite, "Prix achat": p.prix_materiel, "Fournisseur": p.fournisseur,
                            "Temps pose h/u": p.temps_pose_h, "PU vente": p.prix_unitaire_vente, "Total": p.total,
                            "Alertes": " ; ".join(p.alertes)} for p in c.dpgf])
        df2 = st.data_editor(df, column_config={"id": None}, hide_index=True, width='stretch',
                             disabled=["Lot", "N°", "Désignation", "Unité", "Qté", "Fournisseur", "PU vente", "Total", "Alertes"])
        st.metric("Total HT", f"{sum(p.total or 0 for p in c.dpgf):,.2f} €".replace(",", " "))
        if st.button("Recalculer avec les temps de pose et valider"):
            for p, (_, r) in zip(c.dpgf, df2.iterrows()):
                p.temps_pose_h = None if pd.isna(r["Temps pose h/u"]) else float(r["Temps pose h/u"])
            # un prix d'achat saisi à la main remplace celui des devis
            saisis = {r["id"]: float(r["Prix achat"]) for _, r in df2.iterrows() if not pd.isna(r["Prix achat"])}
            c.dpgf = construire_dpgf(c)
            for p in c.dpgf:
                if p.prix_materiel is None and p.ligne_quantite_id in saisis:
                    p.prix_materiel = saisis[p.ligne_quantite_id]
                    p.alertes = [x for x in p.alertes if x != "Poste sans prix fournisseur"] + ["Prix saisi à la main"]
            from sie_tender.f5_dpgf import _prix_vente
            for p in c.dpgf:
                p.prix_unitaire_vente = _prix_vente(p.prix_materiel, p.temps_pose_h, c)
                p.total = round(p.prix_unitaire_vente * p.quantite, 2) if p.prix_unitaire_vente is not None and p.quantite else None
            a.valider("dpgf", "dpgf", utilisateur or "?")
            a.sauver()
            st.rerun()
        chemin_modele = a.deposer("exports", "modele_dpgf.xlsx", modele.getvalue()) if modele else None
        chemin = exporter_dpgf(c, a.dossier("exports") / "DPGF.xlsx", chemin_modele)
        telecharger("Télécharger la DPGF (Excel)", chemin, "dl_dpgf")

# --- 6. Mémoire ---------------------------------------------------------------------------
elif page == ETAPES[6]:
    a = affaire_courante()
    c = a.c
    st.header("Mémoire technique")
    st.caption("Plan issu des critères du RC. Pour chaque section : choisir un passage d'archive, l'adapter, puis valider.")
    for s in c.trame_memoire:
        pond = f" ({s.ponderation:g} %)" if s.ponderation else ""
        with st.expander(f"{s.titre}{pond} · {s.statut.value}"):
            if s.note:
                st.info(s.note)
            for i, pa in enumerate(s.passages_proposes[:5]):
                st.markdown(f"**Passage {i + 1}** · {pa.affaire} · {ref(pa.source)} · score {pa.score}")
                st.write(pa.texte)
                if st.button("Reprendre ce passage", key=f"pr_{s.id}_{i}"):
                    s.contenu = (s.contenu + "\n\n" if s.contenu else "") + pa.texte
                    a.sauver()
                    st.rerun()
            texte = st.text_area("Texte de la section", s.contenu or "", height=220, key=f"txt_{s.id}")
            c1, c2 = st.columns(2)
            if c1.button("Enregistrer", key=f"sv_{s.id}"):
                s.contenu = texte or None
                if texte and s.statut == StatutSection.A_COMPLETER:
                    s.statut = StatutSection.PROPOSE
                a.sauver()
            if c2.button("Valider la section", key=f"va_{s.id}", disabled=not texte):
                s.contenu, s.statut = texte, StatutSection.VALIDE
                a.sauver()
                st.rerun()
    if c.trame_memoire and all(s.statut == StatutSection.VALIDE for s in c.trame_memoire) and not a.est_valide("memoire"):
        a.valider("memoire", "memoire", utilisateur or "?")
        a.sauver()
    from sie_tender.export import exporter_trame_docx
    chemin = a.dossier("exports") / "Memoire_technique.docx"
    exporter_trame_docx(c, chemin)
    telecharger("Télécharger le mémoire (Word)", chemin, "dl_mem")

# --- 7. Contrôle ---------------------------------------------------------------------------
elif page == ETAPES[7]:
    a = affaire_courante()
    c = a.c
    st.header("Contrôle final et dossier")
    fichiers = st.file_uploader("Pièces de réponse (attestations, AE signé, planning…)", accept_multiple_files=True)
    if fichiers and st.button("Déposer"):
        for f in fichiers:
            a.deposer("pieces_reponse", f.name, f.getvalue())
    deposes = sorted(p.name for p in a.dossier("pieces_reponse").iterdir())
    st.caption("Pièces déposées : " + (", ".join(deposes) or "aucune"))
    constats = controler(a)
    a.sauver()
    nb_bloq = sum(k.gravite == "bloquant" for k in constats)
    st.metric("Points bloquants", nb_bloq)
    st.dataframe(pd.DataFrame([{"Gravité": k.gravite, "Objet": k.objet, "Constat": k.message} for k in
                               sorted(constats, key=lambda k: {"bloquant": 0, "a_verifier": 1}.get(k.gravite, 2))]),
                 hide_index=True, width='stretch')
    if st.button("Exporter le dossier (ZIP)", type="primary"):
        chemin = exporter_dossier(a)
        a.sauver()
        st.session_state["zip"] = str(chemin)
    if st.session_state.get("zip") and Path(st.session_state["zip"]).exists():
        telecharger("Télécharger le dossier", Path(st.session_state["zip"]), "dl_zip")
        st.caption("Le dossier est destiné à la relecture et au dépôt manuel par SIE.")

# --- Archives ---------------------------------------------------------------------------
elif page == "Archives":
    st.header("Archives SIE")
    dossier = st.text_input("Dossier d'archives à indexer (un sous-dossier par affaire)", value="")
    if st.button("Indexer", disabled=not dossier):
        with st.spinner("Indexation…"):
            b = Bibliotheque.indexer(dossier)
            b.sauver(INDEX)
        st.success(f"{len(b.documents)} documents, {len(b.morceaux)} passages indexés.")
    b = biblio()
    if b:
        q = st.text_input("Rechercher (ex. : intervention en site occupé école)")
        if q:
            st.write("Affaires les plus proches : " + ", ".join(f"{x} ({s})" for x, s in b.affaires_similaires(q)))
            for p in b.rechercher(q):
                st.markdown(f"**{p.affaire}** · {ref(p.source)} · score {p.score}")
                st.write(p.texte)
        st.subheader("Documents administratifs")
        st.dataframe(pd.DataFrame([{"Affaire": d.affaire, "Fichier": d.fichier, "Type": d.type, "Validité": d.validite or ""}
                                   for d in b.documents]), hide_index=True, width='stretch')
