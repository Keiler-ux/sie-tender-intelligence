"""Adaptateur IA : le reste du code ne connaît que `ClientLLM`.

Trois moteurs, choisis par variables d'environnement (ou dans l'interface) :

    SIE_LLM=aucun        (défaut) extraction par règles seulement
    SIE_LLM=ollama       modèle local, rien ne sort de la machine
    SIE_LLM=anthropic    API Claude : exige SIE_LLM_EXTERNE_AUTORISE=oui et
                         ANTHROPIC_API_KEY ; à n'utiliser que sur des DCE
                         fictifs ou publics tant que SIE n'a pas donné son accord.
    SIE_LLM_MODELE=...   modèle (défaut : claude-opus-5-5 pour Claude, mistral pour Ollama)
    SIE_LLM_EFFORT=...   low | medium | high (Claude, défaut medium)
    SIE_OLLAMA_URL=...   défaut http://localhost:11434
    ANTHROPIC_WORKSPACE_ID=wrkspc_...  si la clé n'est rattachée à aucun espace de travail

Chaque moteur sait faire trois choses :
- json()       : réponse JSON libre (complément de la fiche F1) ;
- texte()      : texte libre (brouillon de section du mémoire) ;
- texte_cite() : réponse appuyée sur des pages du DCE, avec les extraits cités.
Les extraits sont toujours revérifiés dans le texte des pièces par l'appelant.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

# Tarifs publics de l'API en dollars par million de jetons (entrée, sortie), pour l'estimation affichée.
TARIFS = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50)}


@dataclass
class PageDoc:
    """Une page de pièce transmise au modèle."""
    fichier: str
    page: int
    texte: str

    @property
    def titre(self) -> str:
        return f"{self.fichier} p.{self.page}"


@dataclass
class Citation:
    fichier: str
    page: int
    extrait: str


@dataclass
class Reponse:
    texte: str
    citations: list[Citation] = field(default_factory=list)
    cout_usd: float = 0.0
    erreur: str | None = None


class ClientLLM(Protocol):
    nom: str
    externe: bool

    def json(self, consigne: str, contenu: str) -> dict[str, Any] | None: ...

    def texte(self, consigne: str, contenu: str) -> Reponse: ...

    def texte_cite(self, consigne: str, question: str, pages: list[PageDoc]) -> Reponse: ...


class AucunLLM:
    nom = "aucun"
    externe = False
    contexte_max = 0

    def json(self, consigne: str, contenu: str) -> dict[str, Any] | None:
        return None

    def texte(self, consigne: str, contenu: str) -> Reponse:
        return Reponse("", erreur="Aucune IA configurée.")

    def texte_cite(self, consigne: str, question: str, pages: list[PageDoc]) -> Reponse:
        return Reponse("", erreur="Aucune IA configurée.")


def _extraire_json(texte: str) -> dict[str, Any] | None:
    m = re.search(r"\{.*\}", texte.strip(), re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


CONSIGNE_CITATIONS_JSON = """
Les pages sont numérotées [n]. Réponds uniquement par un objet JSON :
{"reponse": "texte de la réponse", "citations": [{"page": n, "extrait": "citation exacte, 8 à 30 mots"}]}
Chaque citation est recopiée mot pour mot depuis la page [n]."""


class OllamaLLM:
    externe = False
    contexte_max = 24_000  # caractères envoyés : les modèles locaux ont un contexte réduit

    def __init__(self, modele: str | None = None, url: str | None = None):
        self.modele = modele or os.getenv("SIE_LLM_MODELE", "mistral")
        self.url = (url or os.getenv("SIE_OLLAMA_URL", "http://localhost:11434")).rstrip("/")
        self.nom = f"ollama:{self.modele}"

    @staticmethod
    def disponible(url: str | None = None) -> bool:
        url = (url or os.getenv("SIE_OLLAMA_URL", "http://localhost:11434")).rstrip("/")
        try:
            with urllib.request.urlopen(f"{url}/api/tags", timeout=0.5):
                return True
        except Exception:
            return False

    def _chat(self, consigne: str, contenu: str, format_json: bool) -> str | None:
        corps = {"model": self.modele, "stream": False, "options": {"temperature": 0},
                 "messages": [{"role": "system", "content": consigne}, {"role": "user", "content": contenu}]}
        if format_json:
            corps["format"] = "json"
        req = urllib.request.Request(f"{self.url}/api/chat", data=json.dumps(corps).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                return json.loads(r.read()).get("message", {}).get("content", "")
        except Exception:
            return None

    def json(self, consigne: str, contenu: str) -> dict[str, Any] | None:
        rep = self._chat(consigne, contenu, True)
        return _extraire_json(rep) if rep else None

    def texte(self, consigne: str, contenu: str) -> Reponse:
        rep = self._chat(consigne, contenu, False)
        return Reponse(rep) if rep else Reponse("", erreur=f"Ollama ({self.url}) n'a pas répondu.")

    def texte_cite(self, consigne: str, question: str, pages: list[PageDoc]) -> Reponse:
        corps = "\n".join(f"\n[{i}] {p.titre}\n{p.texte}" for i, p in enumerate(pages, 1))
        rep = self._chat(consigne + CONSIGNE_CITATIONS_JSON, f"{corps}\n\nQuestion : {question}", True)
        obj = _extraire_json(rep or "")
        if not obj or not isinstance(obj.get("reponse"), str):
            return Reponse("", erreur="Réponse du modèle local inexploitable.")
        cites = []
        for c in obj.get("citations") or []:
            if isinstance(c, dict) and isinstance(c.get("page"), int) and 1 <= c["page"] <= len(pages):
                p = pages[c["page"] - 1]
                cites.append(Citation(p.fichier, p.page, str(c.get("extrait", ""))))
        return Reponse(obj["reponse"], cites)


class AnthropicLLM:
    externe = True
    contexte_max = 400_000  # caractères, environ 100 000 jetons

    def __init__(self, modele: str | None = None, client: Any = None):
        if not externe_autorise():
            raise PermissionError(
                "API externe bloquée : définir SIE_LLM_EXTERNE_AUTORISE=oui uniquement "
                "avec l'accord écrit de SIE ou pour des documents fictifs ou publics."
            )
        if client is None:
            import anthropic

            # Clé non rattachée à un espace de travail : l'API exige l'identifiant de l'espace en en-tête.
            espace = os.getenv("ANTHROPIC_WORKSPACE_ID")
            client = anthropic.Anthropic(default_headers={"anthropic-workspace-id": espace} if espace else None)
        self.client = client
        self.modele = modele or os.getenv("SIE_LLM_MODELE", "claude-opus-5-5")
        self.effort = os.getenv("SIE_LLM_EFFORT", "medium")
        self.nom = f"claude:{self.modele}"
        self._fallback = True

    def cout(self, usage: Any) -> float:
        entree, sortie = TARIFS.get(self.modele, (4.0, 20.0))
        u = lambda k: getattr(usage, k, 0) or 0  # noqa: E731
        jetons_entree = u("input_tokens") + 1.25 * u("cache_creation_input_tokens") + 0.1 * u("cache_read_input_tokens")
        return (jetons_entree * entree + u("output_tokens") * sortie) / 1_000_000

    def _appel(self, system: str, contenu: list[dict] | str, max_tokens: int = 16000) -> Any:
        args = dict(model=self.modele, max_tokens=max_tokens, system=system,
                    output_config={"effort": self.effort}, messages=[{"role": "user", "content": contenu}])
        if self._fallback:
            # En cas de refus du modèle, l'API relance la requête sur un autre modèle.
            try:
                return self.client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **args)
            except Exception as e:  # compte ou SDK sans cette option : on continue sans
                if "fallback" not in str(e).lower():
                    raise
                self._fallback = False
        return self.client.messages.create(**args)

    def _executer(self, system: str, contenu: list[dict] | str, max_tokens: int = 16000) -> tuple[Any, Reponse | None]:
        try:
            rep = self._appel(system, contenu, max_tokens)
        except Exception as e:
            nom = type(e).__name__
            msg = {"AuthenticationError": "clé API invalide ou absente (ANTHROPIC_API_KEY)",
                   "PermissionDeniedError": "la clé API n'a pas accès à ce modèle",
                   "RateLimitError": "trop de requêtes ou crédit API épuisé, réessayer plus tard",
                   "APIConnectionError": "connexion à l'API impossible",
                   "NotFoundError": f"modèle « {self.modele} » introuvable"}.get(nom, f"{nom} {str(e)[:300]}")
            if "workspace" in str(e).lower():
                msg = ("la clé API n'est rattachée à aucun espace de travail : ajouter le secret ANTHROPIC_WORKSPACE_ID "
                       "(identifiant wrkspc_… visible sur console.anthropic.com, Settings › Workspaces) "
                       "ou créer une clé depuis un espace de travail")
            return None, Reponse("", erreur=f"Appel à Claude impossible : {msg}.")
        if getattr(rep, "stop_reason", None) == "refusal":
            return rep, Reponse("", cout_usd=self.cout(rep.usage), erreur="Claude a refusé de répondre à cette demande.")
        return rep, None

    def json(self, consigne: str, contenu: str) -> dict[str, Any] | None:
        rep, err = self._executer(consigne + "\nRéponds uniquement par un objet JSON valide.", contenu, 4000)
        if err or rep is None:
            return None
        return _extraire_json("".join(b.text for b in rep.content if getattr(b, "type", "") == "text"))

    def texte(self, consigne: str, contenu: str) -> Reponse:
        rep, err = self._executer(consigne, contenu)
        if err:
            return err
        texte = "".join(b.text for b in rep.content if getattr(b, "type", "") == "text").strip()
        return Reponse(texte, cout_usd=self.cout(rep.usage))

    def texte_cite(self, consigne: str, question: str, pages: list[PageDoc]) -> Reponse:
        """Pages envoyées comme documents avec citations : l'API renvoie les passages exacts utilisés."""
        docs = [{"type": "document", "source": {"type": "text", "media_type": "text/plain", "data": p.texte},
                 "title": p.titre, "citations": {"enabled": True}} for p in pages if p.texte.strip()]
        if not docs:
            return Reponse("", erreur="Aucun texte lisible à transmettre.")
        docs[-1]["cache_control"] = {"type": "ephemeral"}  # questions suivantes sur le même DCE moins chères
        pages_lisibles = [p for p in pages if p.texte.strip()]
        rep, err = self._executer(consigne, docs + [{"type": "text", "text": question}])
        if err:
            return err
        morceaux, cites = [], []
        for b in rep.content:
            if getattr(b, "type", "") != "text":
                continue
            morceaux.append(b.text)
            for c in getattr(b, "citations", None) or []:
                i = getattr(c, "document_index", None)
                if isinstance(i, int) and 0 <= i < len(pages_lisibles):
                    p = pages_lisibles[i]
                    cites.append(Citation(p.fichier, p.page, getattr(c, "cited_text", "").strip()))
        return Reponse("".join(morceaux).strip(), cites, cout_usd=self.cout(rep.usage))


def externe_autorise() -> bool:
    return os.getenv("SIE_LLM_EXTERNE_AUTORISE", "").lower() in ("oui", "1", "true")


def moteurs_disponibles() -> list[str]:
    """Moteurs utilisables ici : « aucun » toujours, Claude si autorisé et clé présente, Ollama s'il répond."""
    out = ["aucun"]
    if externe_autorise() and (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")):
        out.append("anthropic")
    if OllamaLLM.disponible():
        out.append("ollama")
    return out


def client(choix: str | None = None) -> ClientLLM:
    choix = (choix or os.getenv("SIE_LLM", "aucun")).lower()
    if choix == "ollama":
        return OllamaLLM()
    if choix in ("anthropic", "claude"):
        return AnthropicLLM()
    return AucunLLM()


def client_depuis_env() -> ClientLLM:
    return client()
