"""Interface de l'outil (Streamlit) : une page par étape du parcours SIE.

    streamlit run app.py

Variables d'environnement utiles :
    SIE_ESPACE        dossier des affaires (défaut : affaires)
    SIE_INDEX         index des archives (défaut : archives_index.json)
    SIE_LLM           aucun | ollama | anthropic : moteur IA proposé par défaut (voir sie_tender/llm.py)
    SIE_MOT_DE_PASSE  protège l'accès (obligatoire en ligne)
"""
from __future__ import annotations

import io
import os
import tempfile
import zipfile
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from sie_tender import ia
from sie_tender.affaire import Affaire, EspaceTravail
from sie_tender.export import LIBELLES, exporter_excel, exporter_trame_docx, ref
from sie_tender.f3_quantites import importer_quantites
from sie_tender.f4_cotations import exporter_demandes, generer_demandes, lire_date, texte_courriel
from sie_tender.f5_dpgf import _prix_vente, associer, construire_dpgf, exporter_dpgf, importer_devis
from sie_tender.f6_archives import Bibliotheque
from sie_tender.f7_controle import controler, exporter_dossier
from sie_tender.ingestion import importer_dce
from sie_tender.llm import AnthropicLLM, client, moteurs_disponibles
from sie_tender.models import Champ, LigneQuantite, Statut, StatutSection
from sie_tender.pipeline import analyser_affaire

st.set_page_config(page_title="SIE · Appels d'offres", page_icon=":material/bolt:", layout="wide")

ESPACE = EspaceTravail(os.getenv("SIE_ESPACE", "affaires"))
INDEX = Path(os.getenv("SIE_INDEX", "archives_index.json"))
DONNEES = Path(os.getenv("SIE_DATA", "/data" if Path("/data").is_dir() else "donnees_demo"))

st.markdown("""
<style>
.block-container {padding-top: 2.2rem; max-width: 1400px;}
.sie-entete {border-left: 4px solid var(--primary-color, #1f5f99); padding: .2rem 0 .2rem .9rem; margin-bottom: 1rem;}
.sie-entete h2 {margin: 0; padding: 0; font-size: 1.45rem;}
.sie-entete .sous {opacity: .75; font-size: .92rem;}
.sie-pastille {display: inline-block; padding: .08rem .55rem; border-radius: 999px; font-size: .8rem; margin-right: .35rem;
               border: 1px solid rgba(128,128,128,.35);}
.sie-ok {background: rgba(46,160,67,.15);} .sie-alerte {background: rgba(219,109,40,.18);} .sie-bloque {background: rgba(218,54,51,.18);}
.sie-carte {border: 1px solid rgba(128,128,128,.25); border-radius: .6rem; padding: .7rem 1rem; min-height: 6.2rem;}
.sie-carte .titre {font-size: .8rem; opacity: .7; text-transform: uppercase; letter-spacing: .03em;}
.sie-carte .valeur {font-size: 1.02rem; font-weight: 600; margin-top: .15rem; line-height: 1.3;}
.sie-citation {border-left: 3px solid rgba(128,128,128,.4); padding-left: .7rem; margin: .3rem 0; font-size: .9rem; opacity: .9;}
</style>
""", unsafe_allow_html=True)


# --- Préparation ---------------------------------------------------------------------
@st.cache_resource
def preparer_demo() -> None:
    """Mode démo (SIE_DEMO=1) : données fictives et archives indexées au premier lancement."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent / "scripts"))
    if not (DONNEES / "exemples" / "dce_test").exists():
        from generer_dce_test import generer as generer_dce_test
        generer_dce_test(DONNEES / "exemples" / "dce_test")
    if (DONNEES / "exemples" / "archives_fictives").exists() and INDEX.exists():
        return
    from generer_donnees_fictives import generer
    generer(DONNEES / "exemples")
    Bibliotheque.indexer(DONNEES / "exemples" / "archives_fictives").sauver(INDEX)


_MDP = os.getenv("SIE_MOT_DE_PASSE")
if _MDP and not st.session_state.get("connecte"):
    import hmac

    _, centre, _ = st.columns([1, 1.2, 1])
    with centre:
        st.title(":material/bolt: SIE · Appels d'offres")
        with st.form("connexion"):
            saisie = st.text_input("Mot de passe", type="password")
            if st.form_submit_button("Entrer", type="primary", width="stretch"):
                if hmac.compare_digest(saisie.encode(), _MDP.encode()):
                    st.session_state["connecte"] = True
                    st.rerun()
                st.error("Mot de passe incorrect.")
    st.stop()
if os.getenv("SIE_DEMO") == "1" or os.getenv("SIE_EXEMPLES") == "1":
    preparer_demo()


# --- Données en cache (rechargées seulement quand le fichier change) ---------------------
def _mtime(p: Path) -> float:
    return p.stat().st_mtime if p.exists() else 0.0


@st.cache_resource
def _bibliotheque(chemin: str, mtime: float) -> Bibliotheque | None:
    return Bibliotheque.charger(chemin) if Path(chemin).exists() else None


def biblio() -> Bibliotheque | None:
    return _bibliotheque(str(INDEX), _mtime(INDEX))


@st.cache_resource(max_entries=20)
def _affaire(aid: str, mtime: float) -> Affaire:
    return ESPACE.ouvrir(aid)


def ouvrir(aid: str) -> Affaire:
    return _affaire(aid, _mtime(ESPACE.racine / aid / "consultation.json"))


@st.cache_data(max_entries=200, show_spinner=False)
def _resume(aid: str, mtime: float) -> dict:
    c = ESPACE.ouvrir(aid).c
    limite = lire_date(c.fiche.date_limite_remise.valeur)
    return {"id": aid, "titre": c.fiche.intitule.valeur or c.fiche.objet.valeur or "(DCE non analysé)",
            "reference": c.fiche.reference.valeur or "", "acheteur": c.fiche.maitre_ouvrage.valeur or "",
            "limite": limite, "decision": c.fiche.decision or "", "lots": len(c.lots),
            "etapes": sum(any(v.etape == e for v in c.validations) for e in ("fiche", "quantites", "dpgf", "memoire")),
            "cree": c.date_analyse}


def resumes() -> list[dict]:
    out = []
    for rep in sorted(ESPACE.racine.iterdir(), reverse=True):
        f = rep / "consultation.json"
        if f.exists():
            out.append(_resume(rep.name, _mtime(f)))
    return out


@st.cache_resource(max_entries=5, show_spinner="Lecture des pièces du DCE…")
def _pieces(aid: str, mtime: float):
    return importer_dce(ESPACE.racine / aid / "dce")


def pieces_texte(a: Affaire):
    """Texte des pièces (non stocké dans consultation.json) : relu une fois puis gardé en mémoire."""
    d = a.dossier("dce")
    return _pieces(a.id, max([_mtime(f) for f in d.iterdir()] + [0.0]))


@st.cache_data(max_entries=30, show_spinner=False)
def _export(_a: Affaire, nom: str, mtime: float) -> bytes:
    with tempfile.TemporaryDirectory() as t:
        chemin = Path(t) / nom
        {"analyse_dce.xlsx": lambda: exporter_excel(_a.c, chemin),
         "Memoire_technique.docx": lambda: exporter_trame_docx(_a.c, chemin),
         "demandes_cotation.xlsx": lambda: exporter_demandes(_a.c, chemin),
         "DPGF.xlsx": lambda: exporter_dpgf(_a.c, chemin, _a.dossier("exports") / "modele_dpgf.xlsx"
                                            if (_a.dossier("exports") / "modele_dpgf.xlsx").exists() else None)}[nom]()
        return chemin.read_bytes()


def bouton_export(a: Affaire, label: str, nom: str) -> None:
    donnees = _export(a, nom, _mtime(a.racine / "consultation.json"))
    st.download_button(label, donnees, file_name=nom, icon=":material/download:", key=f"dl_{nom}")


# --- Outils d'affichage -----------------------------------------------------------------
def utilisateur() -> str:
    return st.session_state.get("utilisateur") or "?"


def jours_restants(limite: date | None) -> str:
    if not limite:
        return "date à vérifier"
    n = (limite - date.today()).days
    return "dépassée" if n < 0 else ("aujourd'hui" if n == 0 else f"J-{n}")


def pastille(texte: str, genre: str = "") -> str:
    return f'<span class="sie-pastille {genre}">{texte}</span>'


def carte(col, titre: str, valeur: str, aide: str = "") -> None:
    col.markdown(f'<div class="sie-carte"><div class="titre">{titre}</div><div class="valeur">{valeur}</div>'
                 f'<div class="sous" style="font-size:.8rem;opacity:.7">{aide}</div></div>', unsafe_allow_html=True)


def entete(titre: str, sous_titre: str = "", a: Affaire | None = None) -> None:
    infos = ""
    if a is not None:
        c = a.c
        limite = lire_date(c.fiche.date_limite_remise.valeur)
        j = jours_restants(limite)
        genre = "sie-bloque" if j in ("dépassée", "aujourd'hui") or (j.startswith("J-") and int(j[2:]) <= 7) else ""
        infos = (pastille(c.fiche.reference.valeur or "réf. ?") + pastille(f"Remise : {j}", genre)
                 + pastille(f"Décision : {c.fiche.decision or 'non prise'}",
                            "sie-ok" if c.fiche.decision == "GO" else ("sie-bloque" if c.fiche.decision else "sie-alerte")))
        sous_titre = (c.fiche.intitule.valeur or c.fiche.objet.valeur or a.id) + (f" · {sous_titre}" if sous_titre else "")
    st.markdown(f'<div class="sie-entete"><h2>{titre}</h2><div class="sous">{sous_titre}</div><div style="margin-top:.35rem">{infos}</div></div>',
                unsafe_allow_html=True)


def affaire_courante() -> Affaire:
    aid = st.session_state.get("affaire")
    if not aid or not (ESPACE.racine / aid / "consultation.json").exists():
        st.info("Ouvre ou crée une affaire pour commencer.", icon=":material/folder_open:")
        st.page_link(PAGES["affaires"], label="Aller aux affaires", icon=":material/arrow_forward:")
        st.stop()
    return ouvrir(aid)


def suivant(cle: str, label: str) -> None:
    st.divider()
    if st.button(f"Étape suivante : {label}", icon=":material/arrow_forward:", key=f"suiv_{cle}"):
        st.switch_page(PAGES[cle])


CATEGORIES = {"RSE": "RSE", "eliminatoire": "Éliminatoire", "interface": "Interfaces", "planning": "Planning",
              "site_occupe": "Site occupé", "technique": "Technique", "administrative": "Administratif"}

STATUT_AFFICHE = {Statut.EXTRAIT: "✅ extrait", Statut.A_VERIFIER: "⚠️ à vérifier", Statut.CORRIGE: "✏️ corrigé",
                  Statut.VALIDE: "✔️ validé"}


def creer_affaire(fichiers: list[tuple[str, bytes]]) -> None:
    a = ESPACE.creer()
    for nom, contenu in fichiers:
        if nom.lower().endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(contenu)) as z:
                for info in z.infolist():
                    if not info.is_dir() and not Path(info.filename).name.startswith((".", "__")):
                        a.deposer("dce", Path(info.filename).name, z.read(info))
        else:
            a.deposer("dce", nom, contenu)
    with st.status("Analyse du DCE…", expanded=False) as s:
        s.write("Lecture des pièces, fiche, exigences, critères, recherche dans les archives")
        analyser_affaire(a, biblio())
        s.update(label="Analyse terminée", state="complete")
    st.session_state["affaire"] = a.id
    st.toast(f"Affaire créée : {a.c.fiche.intitule.valeur or a.id}", icon=":material/check_circle:")
    st.switch_page(PAGES["fiche"])


# --- IA -----------------------------------------------------------------------------------
NOMS_MOTEURS = {"aucun": "Sans IA (règles seules)", "anthropic": "Claude (API Anthropic)", "ollama": "Modèle local (Ollama)"}


@st.cache_data(ttl=60, show_spinner=False)
def _moteurs() -> list[str]:
    return moteurs_disponibles()


def moteur_choisi() -> str:
    dispo = _moteurs()
    choix = st.session_state.get("moteur") or os.getenv("SIE_LLM", "aucun").lower()
    return choix if choix in dispo else "aucun"


def moteur_pour(a: Affaire):
    """Moteur IA utilisable pour cette affaire, ou None (avec explication affichée)."""
    choix = moteur_choisi()
    if choix == "aucun":
        st.info("L'IA est désactivée. Les règles d'extraction fonctionnent sans elle. Pour l'activer, voir « Configurer l'IA » "
                "dans la barre latérale.", icon=":material/info:")
        return None
    if choix == "anthropic" and not a.c.ia_autorisation:
        st.warning("Claude est un service externe : les pages du DCE lui sont envoyées. L'engagement de confidentialité "
                   "interdit d'y envoyer des documents SIE sans l'accord de SIE.", icon=":material/shield:")
        ok = st.checkbox("Je confirme que ce DCE est fictif ou public, ou que SIE a donné son accord écrit.", key=f"cgu_{a.id}")
        if st.button("Autoriser l'IA externe pour cette affaire", disabled=not ok, type="primary"):
            a.c.ia_autorisation = f"{utilisateur()} le {datetime.now():%d/%m/%Y %H:%M}"
            a.c.journal.append(f"IA externe autorisée pour cette affaire par {a.c.ia_autorisation}.")
            a.sauver()
            st.rerun()
        return None
    try:
        return client(choix)
    except Exception as e:
        st.error(f"IA indisponible : {e}")
        return None


def appeler_ia(a: Affaire, fonction, *args):
    with st.spinner("L'IA travaille… (de quelques secondes à une minute)"):
        try:
            r = fonction(*args)
        except RuntimeError as e:
            st.error(str(e))
            return None
    a.c.reponses_ia.append(r)
    a.c.journal.append(f"IA ({r.moteur}) : {r.question[:80]} · coût estimé {r.cout_usd:.3f} $")
    st.session_state["cout_ia"] = st.session_state.get("cout_ia", 0.0) + r.cout_usd
    a.sauver()
    return r


def afficher_reponse(a: Affaire, r, supprimable: bool = True) -> None:
    st.markdown(r.texte)
    verifiees = [c for c in r.citations if c.verifiee]
    douteuses = [c for c in r.citations if not c.verifiee]
    if r.type != "memoire":
        if not r.citations:
            st.warning("Aucune citation : réponse à vérifier dans le DCE.", icon=":material/warning:")
        elif douteuses:
            st.warning(f"{len(douteuses)} citation(s) introuvable(s) mot pour mot dans le DCE : à vérifier.", icon=":material/warning:")
    with st.expander(f"Sources ({len(verifiees)} vérifiées{f', {len(douteuses)} à vérifier' if douteuses else ''})"):
        for c in r.citations:
            marque = "✅" if c.verifiee else "⚠️"
            st.markdown(f'{marque} **{c.fichier}{f" p.{c.page}" if c.page else ""}**<div class="sie-citation">{c.extrait}</div>',
                        unsafe_allow_html=True)
    pied = st.columns([4, 1])
    pied[0].caption(f"{r.moteur} · {r.date:%d/%m/%Y %H:%M} · coût estimé {r.cout_usd:.3f} $")
    if supprimable and pied[1].button("Supprimer", key=f"del_{r.id}", icon=":material/delete:"):
        a.c.reponses_ia = [x for x in a.c.reponses_ia if x.id != r.id]
        a.sauver()
        st.rerun()


# --- Pages ------------------------------------------------------------------------------------
def page_affaires() -> None:
    entete("Affaires", "Consultations en cours et nouvelles réponses")
    if os.getenv("SIE_DEMO") == "1":
        st.warning("Version de démonstration en ligne : n'y déposez aucun document SIE, uniquement des données fictives. "
                   "Les affaires peuvent disparaître au redémarrage du serveur.", icon=":material/science:")
    liste = resumes()
    onglets = st.tabs([f"En cours ({len(liste)})", "Nouvelle affaire", "Démonstration"])

    with onglets[0]:
        if not liste:
            st.info("Aucune affaire pour l'instant. Dépose un DCE dans l'onglet « Nouvelle affaire ».")
        else:
            filtre = st.text_input("Rechercher", placeholder="intitulé, référence, acheteur…", label_visibility="collapsed")
            vues = [x for x in liste if not filtre or filtre.lower() in f"{x['titre']} {x['reference']} {x['acheteur']}".lower()]
            df = pd.DataFrame([{"id": x["id"], "Intitulé": x["titre"], "Référence": x["reference"],
                                "Acheteur": x["acheteur"].split(",")[0],
                                "Remise": x["limite"], "Délai": jours_restants(x["limite"]), "Décision": x["decision"] or "—",
                                "Lots": x["lots"], "Avancement": x["etapes"] / 4} for x in vues])
            sel = st.dataframe(df, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                               column_config={"id": None, "Intitulé": st.column_config.TextColumn(width="large"),
                                              "Acheteur": st.column_config.TextColumn(width="small"),
                                              "Lots": st.column_config.NumberColumn(width="small"),
                                              "Remise": st.column_config.DateColumn(format="DD/MM/YYYY"),
                                              "Avancement": st.column_config.ProgressColumn(min_value=0, max_value=1, format="percent",
                                                                                            help="Fiche, métré, DPGF et mémoire validés")},
                               key="table_affaires")
            lignes = sel.selection.rows if sel else []
            if lignes:
                choisie = vues[lignes[0]]
                c1, c2, _ = st.columns([1, 1, 3])
                if c1.button("Ouvrir", type="primary", icon=":material/folder_open:", width="stretch"):
                    st.session_state["affaire"] = choisie["id"]
                    st.switch_page(PAGES["fiche"])
                with c2.popover("Supprimer", icon=":material/delete:", width="stretch"):
                    st.write(f"Supprimer définitivement « {choisie['titre']} » et ses fichiers ?")
                    if st.button("Confirmer la suppression", type="primary"):
                        ESPACE.supprimer(choisie["id"])
                        if st.session_state.get("affaire") == choisie["id"]:
                            st.session_state.pop("affaire")
                        st.rerun()
            else:
                st.caption("Clique sur une ligne pour l'ouvrir.")

    with onglets[1]:
        st.write("Dépose toutes les pièces du DCE (PDF, Word, Excel, plans) ou l'archive ZIP téléchargée sur la plateforme.")
        fichiers = st.file_uploader("Pièces du DCE", accept_multiple_files=True, label_visibility="collapsed")
        if st.button("Créer l'affaire et analyser le DCE", type="primary", disabled=not fichiers, icon=":material/play_arrow:"):
            creer_affaire([(f.name, f.getvalue()) for f in fichiers])

    with onglets[2]:
        exemples = DONNEES / "exemples"
        if not exemples.exists():
            st.info("Données fictives absentes : lancer avec SIE_DEMO=1 ou SIE_EXEMPLES=1.")
            return
        st.write("Données entièrement inventées pour essayer l'outil de bout en bout.")
        dce_demo = {"École de Valbourg, 4 lots (avec archives, métré et devis)": exemples / "dce_fictif_valbourg"}
        for z in sorted((exemples / "dce_test").glob("*.zip")):
            dce_demo[{"dce_college_lot_unique": "Collège, lot unique", "dce_hopital_3_lots": "Hôpital, 3 lots",
                      "dce_bailleur_incomplet": "Bailleur social, DCE incomplet"}.get(z.stem, z.stem)] = z
        choix = st.radio("DCE fictif", list(dce_demo), label_visibility="collapsed")
        if st.button("Créer cette affaire de démonstration", type="primary", icon=":material/science:"):
            src = dce_demo[choix]
            fichiers = [(src.name, src.read_bytes())] if src.is_file() else [(f.name, f.read_bytes()) for f in src.iterdir()]
            creer_affaire(fichiers)
        st.divider()
        c1, c2 = st.columns(2)
        tampon = io.BytesIO()
        with zipfile.ZipFile(tampon, "w") as z:
            for f in sorted((exemples / "chiffrage_fictif").rglob("*")):
                if f.is_file():
                    z.write(f, f.relative_to(exemples))
        c1.download_button("Métré et devis fictifs (étapes 3, 5, 7)", tampon.getvalue(), file_name="chiffrage_fictif.zip",
                           icon=":material/download:")
        if (exemples / "dce_test").exists():
            with c2.popover("Télécharger les DCE fictifs", icon=":material/download:"):
                for z in sorted((exemples / "dce_test").glob("*.zip")):
                    st.download_button(z.name, z.read_bytes(), file_name=z.name, key=f"dce_{z.name}")


def page_fiche() -> None:
    a = affaire_courante()
    c = a.c
    entete("1 · Fiche GO / NO GO", a=a)
    f = c.fiche
    cols = st.columns(4)
    limite = lire_date(f.date_limite_remise.valeur)
    carte(cols[0], "Remise des offres", f.date_limite_remise.valeur or "à vérifier", jours_restants(limite))
    carte(cols[1], "Visite", {"oui": "Obligatoire", "non": "Facultative"}.get(f.visite_obligatoire.valeur, "à vérifier"),
          f.visite.valeur if f.visite.valeur and len(f.visite.valeur) < 40 else "")
    carte(cols[2], "Procédure", (f.procedure.valeur or "à vérifier").capitalize(), f.duree_validite_offres.valeur or "")
    a_verif = sum(ch.statut == Statut.A_VERIFIER for ch in f.champs().values())
    carte(cols[3], "Champs à vérifier", str(a_verif), f"sur {len(f.champs())}")
    st.write("")

    gauche, droite = st.columns([3, 2], gap="large")
    with gauche:
        st.subheader("Renseignements")
        st.caption("Corrige directement dans le tableau. « À vérifier » : information absente ou incertaine, l'outil n'invente rien.")
        lignes = [{"cle": k, "Champ": LIBELLES.get(k, k), "Valeur": ch.valeur or "", "Statut": STATUT_AFFICHE[ch.statut],
                   "Source": ref(ch.source), "Extrait": ch.source.extrait if ch.source else ""} for k, ch in f.champs().items()]
        df = st.data_editor(pd.DataFrame(lignes), disabled=["cle", "Champ", "Statut", "Source", "Extrait"], hide_index=True,
                            column_config={"cle": None, "Valeur": st.column_config.TextColumn(width="large"),
                                           "Extrait": st.column_config.TextColumn(width="medium")},
                            width="stretch", height=35 * (len(lignes) + 1) + 3, key=f"fiche_{a.id}")
        moteur = None
        if a_verif and moteur_choisi() != "aucun":
            with st.expander(f"Compléter les {a_verif} champs à vérifier avec l'IA", icon=":material/auto_awesome:"):
                moteur = moteur_pour(a)
                if moteur and st.button("Lancer", key="ia_fiche"):
                    from sie_tender.f1_fiche import _completer_llm
                    with st.spinner("L'IA cherche les champs manquants…"):
                        _completer_llm(c.fiche, pieces_texte(a), moteur, c.journal)
                    a.sauver()
                    st.rerun()
    with droite:
        st.subheader("Lots")
        lots_df = st.data_editor(pd.DataFrame([{"N°": l.numero, "Intitulé": l.intitule, "Retenu": bool(l.retenu),
                                                "Responsable": l.responsable or ""} for l in c.lots]),
                                 disabled=["N°"], hide_index=True, width="stretch", key=f"lots_{a.id}",
                                 column_config={"N°": st.column_config.TextColumn(width="small"),
                                                "Retenu": st.column_config.CheckboxColumn(help="Lots auxquels SIE répond", width="small"),
                                                "Intitulé": st.column_config.TextColumn(width="medium"),
                                                "Responsable": st.column_config.TextColumn(width="small")})
        if not c.lots:
            st.caption("Aucun lot trouvé : à vérifier dans le RC.")
        if f.alertes:
            st.subheader("Points d'attention")
            for al in f.alertes:
                libelle, _, extrait = al.partition(" : « ")
                with st.expander(libelle, icon=":material/priority_high:"):
                    st.markdown(f'<div class="sie-citation">« {extrait}</div>', unsafe_allow_html=True)
    st.subheader("Décision de la direction")
    with st.form("decision", border=True):
        c1, c2 = st.columns([2, 3])
        options = ["(non décidée)", "GO", "NO GO"]
        decision = c1.segmented_control("Décision", options, default=c.fiche.decision or "(non décidée)")
        c2.caption(f"Décision actuelle : {c.fiche.decision or 'non prise'}"
                   + (f" par {c.fiche.decideur} le {c.fiche.date_decision:%d/%m/%Y}" if c.fiche.date_decision else ""))
        if st.form_submit_button("Enregistrer et valider la fiche", type="primary", icon=":material/save:"):
            for _, r in df.iterrows():
                ch: Champ = getattr(c.fiche, r["cle"])
                if (r["Valeur"] or None) != ch.valeur:
                    ch.valeur, ch.statut, ch.methode = r["Valeur"] or None, Statut.CORRIGE, "saisie"
            for l, (_, r) in zip(c.lots, lots_df.iterrows()):
                l.intitule, l.retenu, l.responsable = r["Intitulé"], bool(r["Retenu"]), r["Responsable"] or None
            if decision and decision != "(non décidée)":
                c.fiche.decision, c.fiche.decideur, c.fiche.date_decision = decision, utilisateur(), datetime.now()
            a.valider("fiche", "fiche", utilisateur())
            a.sauver()
            st.toast("Fiche enregistrée.", icon=":material/check_circle:")
            st.rerun()
    bouton_export(a, "Exporter la fiche et l'analyse (Excel)", "analyse_dce.xlsx")
    suivant("analyse", "analyse par lot")


def page_analyse() -> None:
    a = affaire_courante()
    c = a.c
    entete("2 · Analyse par lot", a=a)
    retenus = [l.numero for l in c.lots if l.retenu]
    tous_lots = sorted({e.lot or "commun" for e in c.exigences}, key=lambda x: (x == "commun", x.zfill(3)))
    cats = sorted({e.categorie.value for e in c.exigences})
    compte = pd.Series([e.categorie.value for e in c.exigences]).value_counts() if c.exigences else pd.Series(dtype=int)
    cols = st.columns(max(len(cats), 1))
    for col, cat in zip(cols, cats):
        carte(col, CATEGORIES.get(cat, cat), str(compte.get(cat, 0)))
    st.write("")
    onglets = st.tabs([f"Exigences ({len(c.exigences)})", f"Critères ({len(c.criteres)})",
                       f"Pièces à fournir ({len(c.pieces_a_fournir)})", "Journal"])
    with onglets[0]:
        f1, f2, f3 = st.columns([2, 3, 3])
        lots = f1.multiselect("Lots", tous_lots, default=[x for x in tous_lots if not retenus or x in retenus + ["commun"]])
        choix_cats = f2.multiselect("Catégories", cats, default=cats, format_func=lambda x: CATEGORIES.get(x, x))
        texte = f3.text_input("Contient", placeholder="mot-clé…")
        ex = [e for e in c.exigences if (e.lot or "commun") in lots and e.categorie.value in choix_cats
              and (not texte or texte.lower() in e.texte.lower())]
        st.dataframe(pd.DataFrame([{"Lot": e.lot or "commun", "Catégorie": CATEGORIES.get(e.categorie.value, e.categorie.value), "Précision": e.sous_categorie or "",
                                    "Exigence": e.texte, "Source": ref(e.source)} for e in ex]),
                     width="stretch", hide_index=True, height=min(38 + 35 * max(len(ex), 1), 560),
                     column_config={"Exigence": st.column_config.TextColumn(width="large")})
        if not retenus:
            st.caption("Aucun lot retenu pour l'instant (étape 1) : tous les lots sont affichés.")
    with onglets[1]:
        if not c.criteres:
            st.warning("Critères non trouvés dans le RC : à saisir.")
        total = sum(cr.ponderation or 0 for cr in c.criteres)
        if c.criteres and abs(total - 100) > 0.01:
            st.warning(f"La somme des pondérations fait {total:g} % : à vérifier dans le RC.")
        for cr in c.criteres:
            st.markdown(f"**{cr.intitule}** · {cr.ponderation:g} %")
            st.progress(min((cr.ponderation or 0) / 100, 1.0))
            for sc in cr.sous_criteres:
                st.markdown(f"&nbsp;&nbsp;&nbsp;– {sc.intitule} : {sc.ponderation:g} %")
            st.caption(ref(cr.source))
    with onglets[2]:
        st.dataframe(pd.DataFrame([{"Étape": p.etape, "Pièce": p.intitule, "Document d'archive proposé": p.document_propose or "",
                                    "Validité": p.validite, "Statut": STATUT_AFFICHE[p.statut], "Source": ref(p.source)}
                                   for p in c.pieces_a_fournir]), hide_index=True, width="stretch",
                     column_config={"Validité": st.column_config.DateColumn(format="DD/MM/YYYY")})
    with onglets[3]:
        st.caption("Hypothèses, erreurs et limites relevées pendant l'analyse.")
        for j in c.journal:
            st.markdown("- " + j)
    suivant("assistant", "assistant IA")


def page_assistant() -> None:
    a = affaire_courante()
    c = a.c
    entete("Assistant IA", "Synthèse et questions sur le DCE, réponses sourcées", a=a)
    moteur = moteur_pour(a)
    if moteur is not None:
        st.caption(f"Moteur : {NOMS_MOTEURS.get(moteur_choisi())} ({moteur.nom}). Chaque réponse cite les pages utilisées ; "
                   "les citations sont revérifiées mot pour mot dans les pièces.")
        c1, c2 = st.columns([1, 3])
        if c1.button("Synthèse pour la décision", type="primary", icon=":material/summarize:", width="stretch"):
            r = appeler_ia(a, ia.synthese_dce, c, pieces_texte(a), moteur)
            if r:
                st.rerun()
        with c2.form("question", clear_on_submit=True, border=False):
            q1, q2 = st.columns([5, 1])
            question = q1.text_input("Question", placeholder="Ex. : quelles pénalités en cas de retard ? Qui fournit les luminaires ?",
                                     label_visibility="collapsed")
            if q2.form_submit_button("Demander", width="stretch") and question.strip():
                if appeler_ia(a, ia.question_dce, pieces_texte(a), question.strip(), moteur):
                    st.rerun()
    reponses = [r for r in c.reponses_ia if r.type in ("synthese", "question")]
    if not reponses:
        if moteur is not None:
            st.info("Aucune réponse pour l'instant.")
        return
    total = sum(r.cout_usd for r in c.reponses_ia)
    st.caption(f"{len(reponses)} réponse(s) · coût IA cumulé sur cette affaire : {total:.2f} $")
    for r in reversed(reponses):
        with st.container(border=True):
            st.markdown(f"**{r.question}**")
            afficher_reponse(a, r)


def page_quantites() -> None:
    a = affaire_courante()
    c = a.c
    entete("3 · Quantités", "Métré du bureau d'études (aucune quantité n'est calculée depuis les plans)", a=a)
    with st.expander("Importer un fichier de métré", expanded=not c.quantites, icon=":material/upload_file:"):
        f = st.file_uploader("Fichier Excel de métré", type=["xlsx", "xlsm"])
        lot_defaut = st.text_input("Lot par défaut (si la feuille ne l'indique pas)", value=next((l.numero for l in c.lots if l.retenu), ""))
        if st.button("Importer", disabled=not f, type="primary"):
            chemin = a.deposer("quantites", f.name, f.getvalue())
            lignes, journal = importer_quantites(chemin, lot_defaut or None)
            c.quantites += lignes
            c.journal += journal
            a.sauver()
            st.toast(journal[-1], icon=":material/check_circle:")
            st.rerun()
    if not c.quantites:
        return
    n_verif = sum(q.statut == Statut.A_VERIFIER for q in c.quantites)
    cols = st.columns(3)
    carte(cols[0], "Lignes", str(len(c.quantites)))
    carte(cols[1], "À vérifier", str(n_verif))
    carte(cols[2], "Familles", str(len({q.famille for q in c.quantites if q.famille})))
    st.write("")
    df = pd.DataFrame([{"id": q.id, "Lot": q.lot, "N°": q.code, "Famille": q.famille, "Désignation": q.designation,
                        "Unité": q.unite, "Quantité": q.quantite, "Remarque": q.remarque or ""} for q in c.quantites])
    df2 = st.data_editor(df, column_config={"id": None, "Quantité": st.column_config.NumberColumn(min_value=0, format="%.2f"),
                                            "Désignation": st.column_config.TextColumn(width="large")},
                         disabled=["Remarque"], hide_index=True, width="stretch", num_rows="dynamic", key=f"q_{a.id}")
    if st.button("Enregistrer et valider le métré", type="primary", icon=":material/save:"):
        par_id = {q.id: q for q in c.quantites}
        nouvelles = []
        for _, r in df2.iterrows():
            q = par_id.get(r["id"]) if isinstance(r["id"], str) else None
            if q is None:
                q = LigneQuantite(designation=str(r["Désignation"] or ""))
            q.lot, q.code, q.famille, q.designation, q.unite = r["Lot"], r["N°"], r["Famille"], r["Désignation"], r["Unité"]
            q.quantite = None if pd.isna(r["Quantité"]) else float(r["Quantité"])
            ok = q.quantite is not None and q.quantite > 0 and q.unite
            q.statut, q.remarque = (Statut.VALIDE, None) if ok else (Statut.A_VERIFIER, q.remarque or "quantité ou unité manquante")
            nouvelles.append(q)
        c.quantites = nouvelles
        a.valider("quantites", "quantites", utilisateur())
        a.sauver()
        st.toast("Métré enregistré.", icon=":material/check_circle:")
        st.rerun()
    suivant("cotations", "cotations")


def page_cotations() -> None:
    a = affaire_courante()
    c = a.c
    entete("4 · Cotations", "Brouillons de demandes de prix : le choix des fournisseurs et l'envoi restent faits par SIE", a=a)
    if not c.quantites:
        st.info("Importe d'abord le métré (étape 3).")
        return
    c1, c2 = st.columns([1, 3])
    delai = c1.number_input("Retour des devis, jours avant la remise", 3, 30, 10)
    c2.write("")
    if c2.button("Générer les demandes", type="primary", icon=":material/mail:"):
        c.demandes_cotation = generer_demandes(c, delai)
        a.sauver()
        st.rerun()
    for d in c.demandes_cotation:
        with st.expander(f"Lot {d.lot or '?'} · {d.famille} · {len(d.lignes)} lignes · retour avant {d.echeance_retour or 'à fixer'}"):
            st.dataframe(pd.DataFrame([{"Désignation": l.designation, "Unité": l.unite, "Quantité": l.quantite,
                                        "Caractéristiques (CCTP)": "\n".join(l.caracteristiques)} for l in d.lignes]),
                         hide_index=True, width="stretch")
            st.text_area("Brouillon de courriel", texte_courriel(c, d), height=240, key=f"mail_{d.id}")
    if c.demandes_cotation:
        bouton_export(a, "Télécharger les demandes (Excel)", "demandes_cotation.xlsx")
        suivant("dpgf", "devis et DPGF")


def page_dpgf() -> None:
    a = affaire_courante()
    c = a.c
    entete("5 · Devis et DPGF", a=a)
    with st.expander("Importer un devis fournisseur", expanded=not c.devis, icon=":material/upload_file:"):
        c1, c2, c3 = st.columns([2, 2, 1])
        f = c1.file_uploader("Devis (Excel, CSV ou PDF)", type=["xlsx", "csv", "pdf"])
        fournisseur = c2.text_input("Fournisseur")
        lot = c3.text_input("Lot", value=next((l.numero for l in c.lots if l.retenu), ""))
        if st.button("Importer le devis", disabled=not f, type="primary"):
            chemin = a.deposer("devis", f.name, f.getvalue())
            d = importer_devis(chemin, fournisseur or None, lot or None)
            associer(c, d)
            c.devis.append(d)
            a.sauver()
            st.toast(f"{len(d.lignes)} lignes lues, {sum(1 for l in d.lignes if l.ligne_quantite_id)} associées.", icon=":material/check_circle:")
            st.rerun()
    postes = {q.id: f"{q.code or ''} {q.designation}".strip() for q in c.quantites}
    options = ["(aucun)"] + list(postes.values())
    inverse = {v: k for k, v in postes.items()}
    for d in c.devis:
        n_assoc = sum(1 for l in d.lignes if l.ligne_quantite_id)
        with st.expander(f"{d.fournisseur} · {d.fichier} · {n_assoc}/{len(d.lignes)} lignes associées"):
            df = pd.DataFrame([{"Désignation devis": l.designation, "Unité": l.unite, "Qté": l.quantite, "PU HT": l.prix_unitaire,
                                "Poste associé": postes.get(l.ligne_quantite_id, "(aucun)"), "Confiance": l.score_association,
                                "Validé": l.association_validee} for l in d.lignes])
            df2 = st.data_editor(df, column_config={"Poste associé": st.column_config.SelectboxColumn(options=options, width="large"),
                                                    "Confiance": st.column_config.ProgressColumn(min_value=0, max_value=1),
                                                    "PU HT": st.column_config.NumberColumn(format="%.2f €")},
                                 disabled=["Désignation devis", "Confiance"], hide_index=True, width="stretch", key=f"dv_{d.id}")
            if st.button("Enregistrer les associations", key=f"save_{d.id}"):
                for l, (_, r) in zip(d.lignes, df2.iterrows()):
                    l.ligne_quantite_id = inverse.get(r["Poste associé"])
                    l.prix_unitaire = None if pd.isna(r["PU HT"]) else float(r["PU HT"])
                    l.association_validee = bool(r["Validé"])
                a.sauver()
                st.toast("Associations enregistrées.", icon=":material/check_circle:")
    st.subheader("Coefficients (saisis par SIE)")
    st.caption("L'outil ne choisit ni prix ni marge : sans ces valeurs, le prix de vente reste vide.")
    k = c.coefficients
    with st.form("coefficients", border=False):
        cols = st.columns(4)
        vals = [cols[i].number_input(lbl, value=v, min_value=0.0, step=0.5, format="%.2f", key=f"k{i}")
                for i, (lbl, v) in enumerate([("Taux horaire €/h", k.taux_horaire), ("Frais généraux %", k.frais_generaux),
                                              ("Aléas %", k.aleas), ("Marge %", k.marge)])]
        modele = st.file_uploader("Modèle de DPGF SIE ou de l'acheteur (facultatif)", type=["xlsx"])
        if st.form_submit_button("Construire la DPGF", type="primary", disabled=not c.quantites, icon=":material/table:"):
            k.taux_horaire, k.frais_generaux, k.aleas, k.marge = vals
            if modele:
                a.deposer("exports", "modele_dpgf.xlsx", modele.getvalue())
            c.dpgf = construire_dpgf(c)
            a.sauver()
            st.rerun()
    if not c.dpgf:
        return
    total = sum(p.total or 0 for p in c.dpgf)
    cols = st.columns(3)
    carte(cols[0], "Total HT", f"{total:,.2f} €".replace(",", " "))
    carte(cols[1], "Postes sans prix de vente", str(sum(p.prix_unitaire_vente is None for p in c.dpgf)))
    carte(cols[2], "Postes avec alerte", str(sum(bool(p.alertes) for p in c.dpgf)))
    st.write("")
    df = pd.DataFrame([{"id": p.ligne_quantite_id, "Lot": p.lot, "N°": p.code, "Désignation": p.designation, "Unité": p.unite,
                        "Qté": p.quantite, "Prix achat": p.prix_materiel, "Fournisseur": p.fournisseur,
                        "Temps pose h/u": p.temps_pose_h, "PU vente": p.prix_unitaire_vente, "Total": p.total,
                        "Alertes": " ; ".join(p.alertes)} for p in c.dpgf])
    df2 = st.data_editor(df, column_config={"id": None, "Prix achat": st.column_config.NumberColumn(format="%.2f €"),
                                            "PU vente": st.column_config.NumberColumn(format="%.2f €"),
                                            "Total": st.column_config.NumberColumn(format="%.2f €")},
                         hide_index=True, width="stretch", key=f"dpgf_{a.id}",
                         disabled=["Lot", "N°", "Désignation", "Unité", "Qté", "Fournisseur", "PU vente", "Total", "Alertes"])
    if st.button("Recalculer avec les temps de pose et valider", icon=":material/calculate:"):
        for p, (_, r) in zip(c.dpgf, df2.iterrows()):
            p.temps_pose_h = None if pd.isna(r["Temps pose h/u"]) else float(r["Temps pose h/u"])
        saisis = {r["id"]: float(r["Prix achat"]) for _, r in df2.iterrows() if not pd.isna(r["Prix achat"])}
        temps = {p.ligne_quantite_id: p.temps_pose_h for p in c.dpgf}
        c.dpgf = construire_dpgf(c)
        for p in c.dpgf:
            p.temps_pose_h = temps.get(p.ligne_quantite_id, p.temps_pose_h)
            if p.prix_materiel is None and p.ligne_quantite_id in saisis:
                p.prix_materiel = saisis[p.ligne_quantite_id]
                p.alertes = [x for x in p.alertes if x != "Poste sans prix fournisseur"] + ["Prix saisi à la main"]
            p.prix_unitaire_vente = _prix_vente(p.prix_materiel, p.temps_pose_h, c)
            p.total = round(p.prix_unitaire_vente * p.quantite, 2) if p.prix_unitaire_vente is not None and p.quantite else None
        a.valider("dpgf", "dpgf", utilisateur())
        a.sauver()
        st.rerun()
    bouton_export(a, "Télécharger la DPGF (Excel)", "DPGF.xlsx")
    suivant("memoire", "mémoire technique")


def page_memoire() -> None:
    a = affaire_courante()
    c = a.c
    entete("6 · Mémoire technique", a=a)
    if not c.trame_memoire:
        st.info("Pas de trame : critères non trouvés dans le RC.")
        return
    n_val = sum(s.statut == StatutSection.VALIDE for s in c.trame_memoire)
    st.progress(n_val / len(c.trame_memoire), text=f"{n_val} section(s) validée(s) sur {len(c.trame_memoire)}")
    st.caption("Plan issu des critères du RC. Pour chaque section : reprendre un passage d'archive ou demander un brouillon "
               "à l'IA, l'adapter, puis valider. Les [À COMPLÉTER] signalent ce que l'IA n'a pas pu tirer des archives.")
    ia_active = moteur_choisi() != "aucun"
    for s in c.trame_memoire:
        pond = f" · {s.ponderation:g} %" if s.ponderation else ""
        icone = {StatutSection.VALIDE: ":material/check_circle:", StatutSection.PROPOSE: ":material/edit_note:"}.get(s.statut, ":material/radio_button_unchecked:")
        with st.expander(f"{s.titre}{pond}", icon=icone):
            if s.note:
                st.info(s.note)
            gauche, droite = st.columns([1, 1], gap="large")
            with gauche:
                texte = st.text_area("Texte de la section", s.contenu or "", height=320, key=f"txt_{s.id}")
                b1, b2 = st.columns(2)
                if b1.button("Enregistrer", key=f"sv_{s.id}", icon=":material/save:", width="stretch"):
                    s.contenu = texte or None
                    if texte and s.statut == StatutSection.A_COMPLETER:
                        s.statut = StatutSection.PROPOSE
                    a.sauver()
                    st.toast("Section enregistrée.")
                if b2.button("Valider la section", key=f"va_{s.id}", disabled=not texte or "[À COMPLÉTER" in texte,
                             type="primary", icon=":material/task_alt:", width="stretch",
                             help="Impossible tant qu'il reste des [À COMPLÉTER]."):
                    s.contenu, s.statut = texte, StatutSection.VALIDE
                    a.sauver()
                    st.rerun()
            with droite:
                onglets = st.tabs(["Archives", "Brouillon IA"])
                with onglets[0]:
                    if not s.passages_proposes:
                        st.caption("Aucun passage d'archive proposé.")
                    for i, pa in enumerate(s.passages_proposes[:5]):
                        st.markdown(f"**{pa.affaire}** · {Path(pa.source.fichier).name}, p. {pa.source.page}")
                        st.markdown(f'<div class="sie-citation">{pa.texte}</div>', unsafe_allow_html=True)
                        if st.button("Reprendre ce passage", key=f"pr_{s.id}_{i}", icon=":material/add:"):
                            s.contenu = (s.contenu + "\n\n" if s.contenu else "") + pa.texte
                            s.statut = StatutSection.PROPOSE if s.statut == StatutSection.A_COMPLETER else s.statut
                            a.sauver()
                            st.session_state.pop(f"txt_{s.id}", None)
                            st.rerun()
                with onglets[1]:
                    brouillons = [r for r in c.reponses_ia if r.type == "memoire" and r.question == f"Brouillon : {s.titre}"]
                    if not ia_active:
                        st.caption("IA désactivée (voir « Configurer l'IA » dans la barre latérale).")
                    elif st.button("Rédiger un brouillon", key=f"ia_{s.id}", icon=":material/auto_awesome:"):
                        moteur = moteur_pour(a)
                        if moteur and appeler_ia(a, ia.brouillon_section, c, s, moteur):
                            st.rerun()
                    if brouillons:
                        r = brouillons[-1]
                        st.markdown(f'<div class="sie-citation">{r.texte}</div>', unsafe_allow_html=True)
                        st.caption(f"{r.moteur} · à partir de {len(r.citations)} passage(s) d'archives · {r.cout_usd:.3f} $")
                        if st.button("Reprendre ce brouillon", key=f"rb_{s.id}", icon=":material/add:"):
                            s.contenu = (s.contenu + "\n\n" if s.contenu else "") + r.texte
                            s.statut = StatutSection.PROPOSE if s.statut == StatutSection.A_COMPLETER else s.statut
                            a.sauver()
                            st.session_state.pop(f"txt_{s.id}", None)
                            st.rerun()
    if n_val == len(c.trame_memoire) and not a.est_valide("memoire"):
        a.valider("memoire", "memoire", utilisateur())
        a.sauver()
    bouton_export(a, "Télécharger le mémoire (Word)", "Memoire_technique.docx")
    suivant("controle", "contrôle et dossier")


def page_controle() -> None:
    a = affaire_courante()
    c = a.c
    entete("7 · Contrôle et dossier", "Le dépôt sur la plateforme de l'acheteur reste fait par SIE", a=a)
    with st.expander("Déposer des pièces de réponse", icon=":material/upload_file:"):
        fichiers = st.file_uploader("Attestations, AE signé, planning…", accept_multiple_files=True)
        if st.button("Déposer", disabled=not fichiers, type="primary"):
            for f in fichiers:
                a.deposer("pieces_reponse", f.name, f.getvalue())
            st.rerun()
    deposes = sorted(p.name for p in a.dossier("pieces_reponse").iterdir())
    constats = controler(a)
    nb = {g: sum(k.gravite == g for k in constats) for g in ("bloquant", "a_verifier")}
    cols = st.columns(3)
    carte(cols[0], "Points bloquants", str(nb["bloquant"]))
    carte(cols[1], "Points à vérifier", str(nb["a_verifier"]))
    carte(cols[2], "Pièces déposées", str(len(deposes)), ", ".join(deposes)[:80])
    st.write("")
    if not constats:
        st.success("Aucun manque détecté.", icon=":material/check_circle:")
    st.dataframe(pd.DataFrame([{"Gravité": {"bloquant": "🔴 bloquant", "a_verifier": "🟠 à vérifier"}.get(k.gravite, "🔵 info"),
                                "Objet": k.objet, "Constat": k.message} for k in
                               sorted(constats, key=lambda k: {"bloquant": 0, "a_verifier": 1}.get(k.gravite, 2))]),
                 hide_index=True, width="stretch", column_config={"Constat": st.column_config.TextColumn(width="large")})
    if st.button("Préparer le dossier (ZIP)", type="primary", icon=":material/folder_zip:"):
        with st.spinner("Préparation du dossier…"):
            chemin = exporter_dossier(a)
            a.sauver()
        st.session_state["zip"] = str(chemin)
    if st.session_state.get("zip") and Path(st.session_state["zip"]).exists() and a.id in st.session_state["zip"]:
        z = Path(st.session_state["zip"])
        st.download_button("Télécharger le dossier", z.read_bytes(), file_name=z.name, icon=":material/download:")
        st.caption("Dossier destiné à la relecture et au dépôt manuel par SIE. Le dossier 04 est interne : ne pas le déposer.")


def page_archives() -> None:
    entete("Archives SIE", "Anciens mémoires et documents administratifs")
    b = biblio()
    if b:
        cols = st.columns(3)
        carte(cols[0], "Affaires", str(len({d.affaire for d in b.documents})))
        carte(cols[1], "Documents", str(len(b.documents)))
        carte(cols[2], "Passages indexés", str(len(b.morceaux)))
        st.write("")
    onglets = st.tabs(["Rechercher", "Documents administratifs", "Indexer"])
    with onglets[0]:
        if not b:
            st.info("Aucune archive indexée.")
        else:
            q = st.text_input("Rechercher", placeholder="Ex. : intervention en site occupé école")
            if q:
                st.caption("Affaires les plus proches : " + ", ".join(f"{x} ({s})" for x, s in b.affaires_similaires(q)))
                for p in b.rechercher(q):
                    st.markdown(f"**{p.affaire}** · {ref(p.source)} · score {p.score}")
                    st.markdown(f'<div class="sie-citation">{p.texte}</div>', unsafe_allow_html=True)
    with onglets[1]:
        if b:
            st.dataframe(pd.DataFrame([{"Affaire": d.affaire, "Fichier": d.fichier, "Type": d.type, "Validité": d.validite}
                                       for d in b.documents]), hide_index=True, width="stretch",
                         column_config={"Validité": st.column_config.DateColumn(format="DD/MM/YYYY")})
    with onglets[2]:
        dossier = st.text_input("Dossier d'archives à indexer (un sous-dossier par affaire)")
        if st.button("Indexer", disabled=not dossier, type="primary"):
            with st.spinner("Indexation…"):
                nb = Bibliotheque.indexer(dossier)
                nb.sauver(INDEX)
            st.success(f"{len(nb.documents)} documents, {len(nb.morceaux)} passages indexés.")


# --- Navigation et barre latérale -------------------------------------------------------------
def _etat(etape: str) -> str:
    aid = st.session_state.get("affaire")
    if not aid or not (ESPACE.racine / aid / "consultation.json").exists():
        return ""
    return " ✓" if any(v.etape == etape for v in ouvrir(aid).c.validations) else ""


PAGES = {
    "affaires": st.Page(page_affaires, title="Affaires", icon=":material/folder_open:", url_path="affaires", default=True),
    "fiche": st.Page(page_fiche, title="1 · Fiche GO / NO GO" + _etat("fiche"), icon=":material/fact_check:", url_path="fiche"),
    "analyse": st.Page(page_analyse, title="2 · Analyse par lot", icon=":material/list_alt:", url_path="analyse"),
    "assistant": st.Page(page_assistant, title="Assistant IA", icon=":material/auto_awesome:", url_path="assistant"),
    "quantites": st.Page(page_quantites, title="3 · Quantités" + _etat("quantites"), icon=":material/straighten:", url_path="quantites"),
    "cotations": st.Page(page_cotations, title="4 · Cotations", icon=":material/mail:", url_path="cotations"),
    "dpgf": st.Page(page_dpgf, title="5 · Devis et DPGF" + _etat("dpgf"), icon=":material/request_quote:", url_path="dpgf"),
    "memoire": st.Page(page_memoire, title="6 · Mémoire technique" + _etat("memoire"), icon=":material/description:", url_path="memoire"),
    "controle": st.Page(page_controle, title="7 · Contrôle et dossier", icon=":material/verified:", url_path="controle"),
    "archives": st.Page(page_archives, title="Archives", icon=":material/inventory_2:", url_path="archives"),
}
nav = st.navigation({
    "": [PAGES["affaires"]],
    "Analyse du DCE": [PAGES["fiche"], PAGES["analyse"], PAGES["assistant"]],
    "Chiffrage": [PAGES["quantites"], PAGES["cotations"], PAGES["dpgf"]],
    "Réponse": [PAGES["memoire"], PAGES["controle"]],
    "Ressources": [PAGES["archives"]],
})

with st.sidebar:
    aid = st.session_state.get("affaire")
    if aid and (ESPACE.racine / aid / "consultation.json").exists():
        r = _resume(aid, _mtime(ESPACE.racine / aid / "consultation.json"))
        titre = r["titre"] if len(r["titre"]) <= 70 else r["titre"][:70].rsplit(" ", 1)[0] + "…"
        st.markdown(f"**{titre}**")
        st.caption(f"{r['reference'] or aid} · remise {jours_restants(r['limite'])} · {r['etapes']}/4 étapes validées")
    st.text_input("Ton nom (pour les validations)", key="utilisateur", placeholder="Prénom Nom")
    with st.expander("Configurer l'IA", icon=":material/auto_awesome:"):
        dispo = _moteurs()
        st.radio("Moteur", dispo, format_func=lambda m: NOMS_MOTEURS.get(m, m), key="moteur",
                 index=dispo.index(moteur_choisi()))
        if "anthropic" not in dispo:
            st.caption("Claude : renseigner ANTHROPIC_API_KEY et SIE_LLM_EXTERNE_AUTORISE=oui dans les secrets "
                       "(Streamlit Cloud : Settings › Secrets).")
        if "ollama" not in dispo:
            st.caption("Modèle local : installer Ollama sur ce poste (version téléchargeable uniquement).")
        if st.session_state.get("cout_ia"):
            st.caption(f"Coût IA de cette session : {st.session_state['cout_ia']:.3f} $")
    st.caption(f"Archives : {'indexées' if INDEX.exists() else 'non indexées'}")

if st.session_state.get("_page_test"):  # tests automatiques : page appelée directement
    {"affaires": page_affaires, "fiche": page_fiche, "analyse": page_analyse, "assistant": page_assistant,
     "quantites": page_quantites, "cotations": page_cotations, "dpgf": page_dpgf, "memoire": page_memoire,
     "controle": page_controle, "archives": page_archives}[st.session_state["_page_test"]]()
else:
    nav.run()
