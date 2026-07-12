"""Company data + the access-tier registry every governance check hangs off of.

This module is deliberately governance-agnostic: it knows *what* each file
is and *how sensitive* it is (``DATA_REGISTRY``), and it can read/write the
files. It does NOT talk to Sentience Governor directly — that's
``governance_wiring.py``'s job, which wraps every function here into a
governed tool using the same registry as its source of truth. One registry,
two consumers, no drift between "what's allowed" and "what's logged."

Tier vocabulary matches Sentience Governor's own sensitivity ladder exactly
(see sentience_governor.cache.cache.SENSITIVITY_TIERS): public < internal <
confidential < pii < restricted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

Access = Literal["allow", "deny"]

# Single source of truth for every data surface in the demo. Keyed by the
# same string used as both the LangChain tool name AND the Sentience
# target_system/tool_id (see governance_wiring.make_governed_tool) — keeping
# these identical is what lets SCOPE_INTENT_MISMATCH / POL-001 match cleanly
# against session_scope_hint without any custom parsing.
DATA_REGISTRY: dict[str, dict] = {
    "read_company_financials_public": {
        "file": "company_financials_public.xlsx",
        "tier": "public",
        "access": "allow",
    },
    "read_internal_credit_policy": {
        "file": "internal_credit_policy.csv",
        "tier": "internal",
        "access": "allow",
    },
    "read_loan_applicants": {
        "file": "loan_applicants.xlsx",
        "tier": "pii",
        "access": "allow",  # allowed, but only with an authorization_claim — enforced by the caller
    },
    "write_decision_log": {
        "file": "company_financials_public.xlsx",
        "tier": "public",
        "access": "allow",
    },
    "read_employee_salaries_confidential": {
        "file": "employee_salaries_confidential.xlsx",
        "tier": "restricted",
        "access": "deny",
    },
    "search_company_knowledge": {
        "file": None,  # derived FAISS index over public+internal docs, not a single file
        "tier": "internal",
        "access": "allow",
    },
}


class AccessDenied(PermissionError):
    """Raised when a tool tries to touch a 'deny' tier file.

    This is the actual enforcement — Sentience Governor's open tier only
    *observes and flags* (its own `on_match` schema has exactly one active
    value, "flag"; block/deny/prompt are reserved for a paid tier). This
    exception is what really stops the read, here in application code.
    """


def check_access(name: str) -> None:
    """Defense-in-depth guard: raise before ever touching disk for a denied tier.

    governance_wiring's tool wrapper calls this too, before this function
    even runs — so this check firing means the wrapper was bypassed. Kept
    here anyway so calling these functions directly (e.g. from a test) is
    just as safe as calling them through a governed tool.
    """
    entry = DATA_REGISTRY.get(name)
    if entry is None:
        raise KeyError(f"Unknown data surface: {name!r}")
    if entry["access"] == "deny":
        raise AccessDenied(
            f"'{name}' is a restricted data surface. This read was blocked "
            f"before {entry['file']} was opened."
        )


# ---------------------------------------------------------------------------
# Raw read/write functions — one per DATA_REGISTRY entry
# ---------------------------------------------------------------------------

def read_company_financials_public() -> str:
    check_access("read_company_financials_public")
    path = DATA_DIR / DATA_REGISTRY["read_company_financials_public"]["file"]
    df = pd.read_excel(path, sheet_name="quarterly_summary")
    return "Company public quarterly financials:\n" + df.to_markdown(index=False)


def read_internal_credit_policy() -> str:
    check_access("read_internal_credit_policy")
    path = DATA_DIR / DATA_REGISTRY["read_internal_credit_policy"]["file"]
    df = pd.read_csv(path)
    return "Internal credit policy thresholds:\n" + df.to_markdown(index=False)


def read_loan_applicants(application_id: str) -> str:
    check_access("read_loan_applicants")
    path = DATA_DIR / DATA_REGISTRY["read_loan_applicants"]["file"]
    df = pd.read_excel(path)
    row = df[df["application_id"] == application_id]
    if row.empty:
        return f"No applicant found with application_id={application_id!r}."
    return f"Applicant record for {application_id}:\n" + row.to_markdown(index=False)


def list_applicant_ids() -> list[str]:
    """UI helper (not a governed tool) — populates the Streamlit applicant picker."""
    path = DATA_DIR / DATA_REGISTRY["read_loan_applicants"]["file"]
    df = pd.read_excel(path)
    return df["application_id"].tolist()


def preview_applicant(application_id: str) -> dict:
    """UI helper (not a governed tool): the human reviewer looking at their own
    queue before deciding whether to run the AI pipeline. Not an agent action,
    so it deliberately does not go through GovernanceSession.access_data."""
    path = DATA_DIR / DATA_REGISTRY["read_loan_applicants"]["file"]
    df = pd.read_excel(path)
    row = df[df["application_id"] == application_id]
    return row.iloc[0].to_dict() if not row.empty else {}


def append_decision_log(application_id: str, decision: str, agent_id: str, session_id: str) -> str:
    check_access("write_decision_log")
    path = DATA_DIR / DATA_REGISTRY["write_decision_log"]["file"]
    existing = pd.read_excel(path, sheet_name="decision_log")
    new_row = pd.DataFrame(
        [
            {
                "timestamp_utc": pd.Timestamp.utcnow().isoformat(),
                "application_id": application_id,
                "decision": decision,
                "agent_id": agent_id,
                "session_id": session_id,
            }
        ]
    )
    updated = pd.concat([existing, new_row], ignore_index=True)
    quarterly = pd.read_excel(path, sheet_name="quarterly_summary")
    with pd.ExcelWriter(path, engine="openpyxl", mode="w") as writer:
        quarterly.to_excel(writer, sheet_name="quarterly_summary", index=False)
        updated.to_excel(writer, sheet_name="decision_log", index=False)
    return f"Decision '{decision}' for {application_id} appended to the audit log."


def read_employee_salaries_confidential() -> str:
    """Never legitimately called. Exists only so the Decision agent has a real
    tool an injection attempt can try to invoke — the block below is what
    the demo is there to show."""
    check_access("read_employee_salaries_confidential")
    path = DATA_DIR / DATA_REGISTRY["read_employee_salaries_confidential"]["file"]
    df = pd.read_excel(path)  # unreachable under normal operation
    return df.to_markdown(index=False)


# ---------------------------------------------------------------------------
# RAG index — Jina embeddings + FAISS, built ONLY from public+internal tier
# text. Scope enforcement here isn't just a runtime check on the read
# functions above; the index itself structurally cannot surface PII or
# restricted data, because it's never given that text to begin with.
#
# Calls Jina's current v1/embeddings API (model: jina-embeddings-v4) directly
# via `requests` rather than through langchain_community.embeddings.JinaEmbeddings
# — that wrapper posts the older v2/v3 request shape and langchain_community
# itself is being sunset in favor of standalone integration packages. FAISS
# stays on langchain_community.vectorstores.FAISS: unlike the embeddings
# wrapper, that's still the current, actively-documented integration path
# (no maintained standalone langchain-faiss replacement exists).
# ---------------------------------------------------------------------------

from langchain_core.embeddings import Embeddings  # noqa: E402

JINA_EMBEDDINGS_URL = "https://api.jina.ai/v1/embeddings"
JINA_MODEL = "jina-embeddings-v4"


class JinaEmbeddings(Embeddings):
    """langchain_core.embeddings.Embeddings implementation for Jina.

    Talks to Jina's REST API directly so this demo doesn't depend on
    langchain_community's embeddings wrapper (deprecated request shape,
    and the package itself is being sunset). Must actually subclass
    Embeddings, not just duck-type its methods — FAISS's similarity_search
    does `isinstance(self.embedding_function, Embeddings)` to decide whether
    to call `.embed_query()` or treat it as a raw callable; a duck-typed
    class with the right methods but no inheritance fails that check and
    FAISS then tries to call the object directly, which is not callable.
    """

    def __init__(self, api_key: str, model: str = JINA_MODEL) -> None:
        self._api_key = api_key
        self._model = model

    def _embed(self, texts: list[str]) -> list[list[float]]:
        import requests

        resp = requests.post(
            JINA_EMBEDDINGS_URL,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={"model": self._model, "input": texts},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        # Jina returns results in input order but tags each with an "index";
        # sort defensively rather than assuming order is preserved.
        ordered = sorted(data, key=lambda d: d["index"])
        return [d["embedding"] for d in ordered]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text])[0]


def build_company_knowledge_index(jina_api_key: str):
    """Build an in-memory FAISS index over public+internal company documents.

    Returns a LangChain retriever. Raises ValueError if jina_api_key is blank.
    """
    if not jina_api_key:
        raise ValueError("No Jina API key provided. Enter it in the sidebar first.")

    from langchain_community.vectorstores import FAISS
    from langchain_core.documents import Document

    documents = [
        Document(
            page_content=read_company_financials_public(),
            metadata={"source": "company_financials_public", "tier": "public"},
        ),
        Document(
            page_content=read_internal_credit_policy(),
            metadata={"source": "internal_credit_policy", "tier": "internal"},
        ),
    ]
    embeddings = JinaEmbeddings(api_key=jina_api_key)
    store = FAISS.from_documents(documents, embeddings)
    return store.as_retriever(search_kwargs={"k": 2})
