"""Tests des fonctions IA sans appel réseau : faux client Claude et faux moteur local."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "scripts"))

from generer_donnees_fictives import generer  # noqa: E402

from sie_tender import ia  # noqa: E402
from sie_tender.ingestion import importer_dce  # noqa: E402
from sie_tender.llm import AnthropicLLM, AucunLLM, Citation, Reponse  # noqa: E402
from sie_tender.pipeline import analyser_dce  # noqa: E402


@pytest.fixture(scope="module")
def dce(tmp_path_factory):
    d = tmp_path_factory.mktemp("ia")
    generer(d)
    return d / "dce_fictif_valbourg"


class FauxMessages:
    """Imite client.beta.messages / client.messages : renvoie une citation exacte et une inventée."""

    def __init__(self):
        self.appels = []

    def create(self, **kw):
        self.appels.append(kw)
        docs = [b for b in kw["messages"][0]["content"] if isinstance(b, dict) and b.get("type") == "document"]
        i = next(i for i, d in enumerate(docs) if "27 novembre 2026" in d["source"]["data"])
        vraie = NS(type="char_location", document_index=i, cited_text="Date limite de réception des offres : vendredi 27 novembre 2026 à 12h00.")
        fausse = NS(type="char_location", document_index=i, cited_text="Le marché est attribué au moins-disant sans négociation.")
        return NS(stop_reason="end_turn",
                  usage=NS(input_tokens=1000, output_tokens=200, cache_creation_input_tokens=0, cache_read_input_tokens=0),
                  content=[NS(type="thinking", thinking=""),
                           NS(type="text", text="La remise est fixée au 27 novembre 2026 à 12h00.", citations=[vraie, fausse])])


def test_claude_bloque_sans_autorisation(monkeypatch):
    monkeypatch.delenv("SIE_LLM_EXTERNE_AUTORISE", raising=False)
    with pytest.raises(PermissionError):
        AnthropicLLM(client=NS())


def test_question_avec_citations_verifiees(dce, monkeypatch):
    monkeypatch.setenv("SIE_LLM_EXTERNE_AUTORISE", "oui")
    faux = NS(beta=NS(messages=FauxMessages()), messages=FauxMessages())
    moteur = AnthropicLLM(modele="claude-opus-5-5", client=faux)
    pieces = importer_dce(dce)
    r = ia.question_dce(pieces, "Quelle est la date limite de remise ?", moteur)
    assert "27 novembre" in r.texte
    assert [c.verifiee for c in r.citations] == [True, False]  # la citation inventée est signalée
    appel = faux.beta.messages.appels[0]
    assert appel["model"] == "claude-opus-5-5" and "temperature" not in appel
    assert appel["fallbacks"] == "default"
    assert r.cout_usd == pytest.approx((1000 * 4 + 200 * 20) / 1e6, abs=1e-4)


def test_pages_limitees_et_pertinentes(dce):
    pieces = importer_dce(dce)
    pages = ia.pages_pertinentes(pieces, "insertion professionnelle heures", 1500)
    assert sum(len(p.texte) for p in pages) <= 1500
    assert any("insertion" in p.texte for p in pages)


class FauxLocal(AucunLLM):
    nom = "faux"
    contexte_max = 50_000

    def texte(self, consigne, contenu):
        assert "[À COMPLÉTER" in consigne and "site occupé" in contenu
        return Reponse("Nos équipes interviennent en site occupé. [À COMPLÉTER : effectif]")

    def texte_cite(self, consigne, question, pages):
        return Reponse("ok", [Citation(pages[0].fichier, pages[0].page, pages[0].texte[:60])])


def test_brouillon_et_synthese(dce, tmp_path):
    from sie_tender.f6_archives import Bibliotheque
    c = analyser_dce(dce, Bibliotheque.indexer(dce.parent / "archives_fictives"))
    section = next(s for s in c.trame_memoire if "site occupé" in s.titre)
    r = ia.brouillon_section(c, section, FauxLocal())
    assert r.type == "memoire" and "[À COMPLÉTER" in r.texte
    s = ia.synthese_dce(c, importer_dce(dce), FauxLocal())
    assert s.citations and s.citations[0].verifiee
    with pytest.raises(RuntimeError):
        ia.synthese_dce(c, [], AucunLLM())


def test_espace_de_travail(monkeypatch):
    monkeypatch.setenv("SIE_LLM_EXTERNE_AUTORISE", "oui")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-faux")
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "wrkspc_test")
    m = AnthropicLLM()
    assert m.client.default_headers.get("anthropic-workspace-id") == "wrkspc_test"

    class Refus:
        def create(self, **kw):
            raise RuntimeError("This API key is not scoped to a workspace, so this request must include the anthropic-workspace-id header")
    m.client = NS(beta=NS(messages=Refus()), messages=Refus())
    r = m.texte("consigne", "contenu")
    assert "ANTHROPIC_WORKSPACE_ID" in r.erreur
