"""Adaptateur LLM : le reste du code ne connaît que `ClientLLM`.

Le choix se fait par variables d'environnement, sans toucher au code :

    SIE_LLM=aucun        (défaut) extraction par règles seulement
    SIE_LLM=ollama       modèle local, rien ne sort de la machine
    SIE_LLM=anthropic    API externe : exige SIE_LLM_EXTERNE_AUTORISE=oui,
                         à n'activer qu'avec l'accord écrit de SIE ou sur
                         des documents publics.
    SIE_LLM_MODELE=...   nom du modèle (ex. mistral, llama3.1, claude-sonnet-5-5)
    SIE_OLLAMA_URL=...   défaut http://localhost:11434
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Any, Protocol


class ClientLLM(Protocol):
    nom: str
    externe: bool

    def json(self, consigne: str, contenu: str) -> dict[str, Any] | None:
        """Renvoie un objet JSON, ou None si le modèle n'a pas répondu proprement."""


class AucunLLM:
    nom = "aucun"
    externe = False

    def json(self, consigne: str, contenu: str) -> dict[str, Any] | None:
        return None


def _extraire_json(texte: str) -> dict[str, Any] | None:
    texte = texte.strip()
    m = re.search(r"\{.*\}", texte, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


class OllamaLLM:
    externe = False

    def __init__(self, modele: str | None = None, url: str | None = None):
        self.modele = modele or os.getenv("SIE_LLM_MODELE", "mistral")
        self.url = (url or os.getenv("SIE_OLLAMA_URL", "http://localhost:11434")).rstrip("/")
        self.nom = f"ollama:{self.modele}"

    def json(self, consigne: str, contenu: str) -> dict[str, Any] | None:
        corps = {
            "model": self.modele,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0},
            "messages": [
                {"role": "system", "content": consigne},
                {"role": "user", "content": contenu},
            ],
        }
        req = urllib.request.Request(
            f"{self.url}/api/chat", data=json.dumps(corps).encode(), headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                rep = json.loads(r.read())
        except Exception:
            return None
        return _extraire_json(rep.get("message", {}).get("content", ""))


class AnthropicLLM:
    externe = True

    def __init__(self, modele: str | None = None):
        if os.getenv("SIE_LLM_EXTERNE_AUTORISE", "").lower() not in ("oui", "1", "true"):
            raise PermissionError(
                "API externe bloquée : définir SIE_LLM_EXTERNE_AUTORISE=oui uniquement "
                "avec l'accord écrit de SIE ou pour des documents publics."
            )
        import anthropic

        self.client = anthropic.Anthropic()
        self.modele = modele or os.getenv("SIE_LLM_MODELE", "claude-sonnet-5-5")
        self.nom = f"anthropic:{self.modele}"

    def json(self, consigne: str, contenu: str) -> dict[str, Any] | None:
        try:
            rep = self.client.messages.create(
                model=self.modele,
                max_tokens=4000,
                temperature=0,
                system=consigne + "\nRéponds uniquement par un objet JSON valide.",
                messages=[{"role": "user", "content": contenu}],
            )
        except Exception:
            return None
        texte = "".join(b.text for b in rep.content if getattr(b, "type", "") == "text")
        return _extraire_json(texte)


def client_depuis_env() -> ClientLLM:
    choix = os.getenv("SIE_LLM", "aucun").lower()
    if choix == "ollama":
        return OllamaLLM()
    if choix == "anthropic":
        return AnthropicLLM()
    return AucunLLM()
