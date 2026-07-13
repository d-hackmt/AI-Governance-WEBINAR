"""BYOK LLM provider wiring for Groq and Mistral.

No key is ever read from the environment or from disk — every call takes
the key explicitly, sourced from Streamlit's session state (see app.py's
sidebar). Model lists are fetched live from each provider's own /models
endpoint using the user's key, rather than hardcoded, because provider
lineups change every few months and a stale dropdown is worse than a
short network call.
"""

from __future__ import annotations

import requests

GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"
MISTRAL_MODELS_URL = "https://api.mistral.ai/v1/models"

# Used only if the live lookup fails (offline demo, expired key while
# just browsing models, etc.) — never used to silently mask a bad key
# at run time, since build_chat_model always makes its own real call.
FALLBACK_GROQ_MODEL = "llama-3.3-70b-versatile"
FALLBACK_MISTRAL_MODEL = "mistral-large-latest"


# Groq's /models list also mixes in speech (whisper), text-to-speech
# (orpheus/canopylabs — additionally requires separate terms acceptance),
# classifier models (prompt-guard, which "answers" chat completions with a
# bare confidence score instead of an actual response), Groq's own agentic
# router models (compound/compound-mini — reject tool calling outright with
# a 400), allam-2-7b (an Arabic-focused chat model that also rejects tool
# calling with the same 400), and gpt-oss-safeguard (a safety-classifier
# variant, not a general reasoning model — none of this app's agents need
# it). Every remaining model has been individually tested against Groq's
# real API and confirmed to support tool calling, which every agent here
# requires.
_GROQ_NON_CHAT_MARKERS = (
    "whisper", "orpheus", "canopylabs", "prompt-guard", "compound", "allam", "safeguard",
)


def fetch_groq_models(api_key: str) -> list[str]:
    """Return live chat-capable model IDs from Groq, newest/largest first."""
    if not api_key:
        return [FALLBACK_GROQ_MODEL]
    try:
        resp = requests.get(
            GROQ_MODELS_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        ids = sorted(
            {
                m["id"]
                for m in data
                if isinstance(m, dict)
                and not any(marker in m["id"].lower() for marker in _GROQ_NON_CHAT_MARKERS)
            }
        )
        return ids or [FALLBACK_GROQ_MODEL]
    except requests.RequestException:
        return [FALLBACK_GROQ_MODEL]


# Mistral's /v1/models list mixes chat models in with embedding, OCR,
# moderation, speech models, and access-restricted "labs" models
# (labs-leanstral-* — 403s for accounts without that entitlement) — none of
# which accept a normal chat/completions request.
_MISTRAL_NON_CHAT_MARKERS = ("embed", "ocr", "moderation", "transcribe", "voxtral", "tts", "labs-leanstral")

# Beyond that, Mistral's list is heavily redundant for this app's purposes:
# every general-reasoning family (mistral-large, mistral-medium, ministral,
# magistral, ...) is listed both as a "-latest" alias AND as several
# individually-dated snapshots (mistral-medium-2505, -2508, -2604, -3, ...)
# — all pointing at essentially the same model family. None of that history
# is useful in a model-picker dropdown, so only "-latest" (or a bare id with
# no "-latest" sibling, like open-mistral-nemo) is kept. Code/CLI-agent
# specific families (codestral, devstral, mistral-code-*, mistral-vibe-cli-*)
# are dropped too — none of this app's agents need code-generation or
# CLI-tool-use specialization, just general reasoning + tool calling.
_MISTRAL_SPECIALIZED_MARKERS = ("codestral", "devstral", "mistral-code", "vibe-cli")


def fetch_mistral_models(api_key: str) -> list[str]:
    """Return live, general-purpose, tool-calling-capable model IDs from Mistral."""
    if not api_key:
        return [FALLBACK_MISTRAL_MODEL]
    try:
        resp = requests.get(
            MISTRAL_MODELS_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        ids = sorted(
            {
                m["id"]
                for m in data
                if isinstance(m, dict)
                and not any(marker in m["id"].lower() for marker in _MISTRAL_NON_CHAT_MARKERS)
                and not any(marker in m["id"].lower() for marker in _MISTRAL_SPECIALIZED_MARKERS)
                and (m["id"].endswith("-latest") or m["id"] == "open-mistral-nemo")
            }
        )
        return ids or [FALLBACK_MISTRAL_MODEL]
    except requests.RequestException:
        return [FALLBACK_MISTRAL_MODEL]


def build_chat_model(provider: str, model: str, api_key: str, temperature: float = 0.1):
    """Construct a LangChain chat model for the given BYOK provider.

    Raises ValueError up front (rather than a confusing error mid-graph)
    if the key is blank, since every agent needs a working model to run.
    """
    if not api_key:
        raise ValueError(f"No API key provided for {provider}. Enter it in the sidebar first.")

    if provider == "groq":
        from langchain_groq import ChatGroq

        return ChatGroq(model_name=model, groq_api_key=api_key, temperature=temperature)
    if provider == "mistral":
        from langchain_mistralai import ChatMistralAI

        return ChatMistralAI(model=model, mistral_api_key=api_key, temperature=temperature)
    raise ValueError(f"Unknown provider: {provider!r}")
