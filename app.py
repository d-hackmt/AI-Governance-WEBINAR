"""AI Governance in Action — a loan-approval demo built on sentience-governor.

Everything here is fictional (Northfield Community Lending, its applicants,
its data) but the governance mechanics are real: every data access is scoped,
classified, and logged through Sentience Governor's actual event primitives;
one specific file is genuinely unreadable no matter what any agent tries;
every decision pauses for a real human click before an audit report is
produced. See governance/*.md for the full narrative this is built from.

BYOK only: every API key below lives in st.session_state for this browser
tab only. Nothing is read from or written to .env or disk.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import data_access  # noqa: E402
from graph import build_graph  # noqa: E402
from governance_wiring import GovernanceSession  # noqa: E402
from langgraph.types import Command  # noqa: E402
from llm_providers import build_chat_model, fetch_groq_models, fetch_mistral_models  # noqa: E402

GOVERNANCE_DIR = Path(__file__).resolve().parent / "governance"

st.set_page_config(page_title="AI Governance in Action", layout="wide")


# ---------------------------------------------------------------------------
# Sidebar — BYOK + per-agent provider/model selection
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300, show_spinner=False)
def _cached_groq_models(key: str) -> list[str]:
    return fetch_groq_models(key)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_mistral_models(key: str) -> list[str]:
    return fetch_mistral_models(key)


@st.cache_resource(ttl=1800, show_spinner=False)
def _cached_knowledge_index(jina_key: str):
    """Cache the FAISS index + Jina embeddings client across runs, keyed on
    the key itself. The two source documents (public financials, credit
    policy) don't change between runs, so re-embedding them from scratch on
    every single click would waste a real network call and add latency to
    exactly the step a live demo can least afford it. st.cache_resource
    (not cache_data) because a retriever isn't serializable/copyable data —
    it's a live object that should be reused as-is."""
    return data_access.build_company_knowledge_index(jina_key)


def _model_picker(role_label: str, default_provider: str, groq_key: str, mistral_key: str):
    col1, col2 = st.sidebar.columns([1, 2])
    provider = col1.selectbox(
        f"{role_label} provider", ["groq", "mistral"],
        index=0 if default_provider == "groq" else 1,
        key=f"provider_{role_label}",
    )
    key = groq_key if provider == "groq" else mistral_key
    models = (
        (_cached_groq_models(key) if provider == "groq" else _cached_mistral_models(key))
        if key
        else ["(enter API key first)"]
    )
    model = col2.selectbox(f"{role_label} model", models, key=f"model_{role_label}")
    return provider, model


st.sidebar.header("🔑 Bring your own keys")
st.sidebar.caption("Kept in this browser session only. Never written to disk.")
groq_key = st.sidebar.text_input("Groq API key", type="password")
mistral_key = st.sidebar.text_input("Mistral API key", type="password")
jina_key = st.sidebar.text_input("Jina API key", type="password")

st.sidebar.divider()
st.sidebar.header("🧠 Agent models")
analyst_provider, analyst_model = _model_picker("Analyst", "groq", groq_key, mistral_key)
decision_provider, decision_model = _model_picker("Decision", "groq", groq_key, mistral_key)
auditor_provider, auditor_model = _model_picker("Auditor", "mistral", groq_key, mistral_key)

st.sidebar.divider()
if st.sidebar.button("🔄 Reset demo"):
    st.session_state.clear()
    st.rerun()


# ---------------------------------------------------------------------------
# Main area
# ---------------------------------------------------------------------------

st.title("AI Governance in Action")
st.caption(
    "A fictional loan-review pipeline for Northfield Community Lending — three "
    "LangGraph agents, real Sentience Governor instrumentation, one file that "
    "is genuinely unreadable no matter what's tried."
)

@st.cache_data(ttl=60, show_spinner=False)
def _cached_applicant_ids() -> list[str]:
    return data_access.list_applicant_ids()


@st.cache_data(ttl=60, show_spinner=False)
def _cached_applicant_preview(application_id: str) -> dict:
    return data_access.preview_applicant(application_id)


applicant_ids = _cached_applicant_ids()
application_id = st.selectbox("Choose an application to review", applicant_ids)

preview = _cached_applicant_preview(application_id)
with st.expander("Applicant record (as the loan officer would see it)", expanded=True):
    st.json(preview)
    notes = str(preview.get("notes", ""))
    if "SYSTEM" in notes or "ignore" in notes.lower():
        st.warning(
            "This applicant's notes field contains what looks like an embedded "
            "instruction aimed at the AI, not the loan officer. The pipeline "
            "doesn't rely on the model 'noticing' this — scope enforcement "
            "and the Auditor's control test hold regardless of whether it does."
        )

pii_authorized = st.checkbox(
    "I am an authorized loan officer and approve PII access for this specific application"
)
pii_authorization_claim = f"loan-officer-authorized:{application_id}" if pii_authorized else None
if not pii_authorized:
    st.caption(
        "Leave unchecked to see what an unauthorized sensitivity escalation "
        "(POL-005) looks like in the governance panel below."
    )

run_clicked = st.button("▶️ Run governance-monitored review", type="primary")

if run_clicked:
    missing = []
    if analyst_provider == "groq" and not groq_key or decision_provider == "groq" and not groq_key:
        missing.append("Groq")
    if analyst_provider == "mistral" and not mistral_key or decision_provider == "mistral" and not mistral_key:
        missing.append("Mistral")
    if auditor_provider == "mistral" and not mistral_key:
        missing.append("Mistral")
    if auditor_provider == "groq" and not groq_key:
        missing.append("Groq")

    if missing:
        st.error(f"Enter your API key(s) first: {', '.join(sorted(set(missing)))}. Nothing runs on a fallback key.")
    else:
        retriever = None
        if jina_key:
            with st.spinner("Building the company knowledge index (Jina + FAISS)..."):
                retriever = _cached_knowledge_index(jina_key)
        else:
            st.info(
                "No Jina key entered — the Analyst will skip semantic search and use "
                "direct reads only. The governance mechanics below are unaffected.",
                icon="ℹ️",
            )

        session = GovernanceSession()
        allowed_scope = [
            "read_company_financials_public",
            "read_internal_credit_policy",
            "read_loan_applicants",
            "write_decision_log",
        ]
        if retriever is not None:
            allowed_scope.append("search_company_knowledge")
        # deliberately excludes read_employee_salaries_confidential
        session.register(declared_capabilities=allowed_scope)
        session.declare_intent(
            f"Review loan application {application_id} against Northfield's credit "
            f"policy and applicable client/regulatory requirements.",
            session_scope_hint=allowed_scope,
        )

        llms = {
            "analyst": build_chat_model(analyst_provider, analyst_model, groq_key if analyst_provider == "groq" else mistral_key),
            "decision": build_chat_model(decision_provider, decision_model, groq_key if decision_provider == "groq" else mistral_key),
            "auditor": build_chat_model(auditor_provider, auditor_model, groq_key if auditor_provider == "groq" else mistral_key),
        }

        compiled = build_graph(session, retriever, llms, pii_authorization_claim)
        config = {"configurable": {"thread_id": session.session_id}}

        with st.spinner("Running Analyst -> Decision (pausing for your approval next)..."):
            result = compiled.invoke({"application_id": application_id}, config=config)

        st.session_state["pipeline"] = {
            "session": session,
            "compiled": compiled,
            "config": config,
            "application_id": application_id,
            "stage": "awaiting_approval",
            "interrupt_payload": result["__interrupt__"][0].value,
        }
        st.rerun()


# ---------------------------------------------------------------------------
# Governance panel — renders whatever the current pipeline run has produced
# ---------------------------------------------------------------------------

def _render_governance_panel(session) -> None:
    st.subheader("📋 Governance trace")
    analysis = session.run_analyzers()

    col1, col2, col3 = st.columns(3)
    col1.metric("Events emitted", len(session.events))
    col2.metric("Advisory flags", sum(analysis["advisory_flag_counts"].values()))
    col3.metric("Policy violations", sum(analysis["policy_violation_counts"].values()))

    if analysis["policy_violation_counts"]:
        st.error(f"Policy violations fired: {analysis['policy_violation_counts']}")
    else:
        st.success("No policy violations recorded (yet) in this session.")

    if analysis["advisory_flag_counts"]:
        st.info(f"Advisory flags: {analysis['advisory_flag_counts']}")

    if session.blocked_attempts:
        st.error(f"🚫 Blocked access attempts: {session.blocked_attempts}")

    with st.expander("Full event trace (raw Sentience Governor events)"):
        rows = [
            {
                "event_type": e["event_type"],
                "primitive": e["primitive"],
                "tool_id": e.get("payload", {}).get("tool_id", ""),
                "advisory_flags": ", ".join(e.get("advisory_flags", [])),
                "policy_violations": ", ".join(e.get("policy_violations", [])),
                "simulated_consequence": e.get("simulated_consequence") or "",
            }
            for e in session.events
        ]
        st.dataframe(rows, use_container_width=True)
        st.download_button(
            "Download full trace (JSON)",
            data=json.dumps(session.events, indent=2),
            file_name=f"sentience_trace_{session.session_id}.json",
            mime="application/json",
        )

    with st.expander("Compliance mapping — the governance documents behind this run"):
        tabs = st.tabs(["Client requirements", "Internal policy", "Regulatory mapping", "Industry standards"])
        for tab, filename in zip(
            tabs,
            ["client_requirements.md", "policy_constraints.md", "regulatory_mapping.md", "industry_standards.md"],
        ):
            with tab:
                st.markdown((GOVERNANCE_DIR / filename).read_text(encoding="utf-8"))


pipeline = st.session_state.get("pipeline")

if pipeline and pipeline["application_id"] == application_id:
    session = pipeline["session"]

    if pipeline["stage"] == "awaiting_approval":
        payload = pipeline["interrupt_payload"]
        analysis_so_far = session.run_analyzers()
        has_violation = bool(analysis_so_far["policy_violation_counts"])

        if has_violation:
            st.error(
                f"⚠️ Human review required — policy violations already fired this "
                f"session: {analysis_so_far['policy_violation_counts']}"
            )
        else:
            st.success("✅ Human review checkpoint — no policy violations so far.")

        st.subheader("Recommended decision")
        st.markdown(f"**{payload['decision'].upper()}** — application `{payload['application_id']}`")
        with st.expander("Full rationale", expanded=True):
            st.markdown(payload["rationale"])

        _render_governance_panel(session)

        st.subheader("👤 Your decision")
        feedback = st.text_area("Reviewer notes (optional)")
        c1, c2 = st.columns(2)
        if c1.button("✅ Approve", type="primary"):
            with st.spinner("Running Auditor Agent..."):
                final_state = pipeline["compiled"].invoke(
                    Command(resume={"approved": True, "feedback": feedback}),
                    config=pipeline["config"],
                )
            pipeline["stage"] = "done"
            pipeline["final_state"] = final_state
            st.rerun()
        if c2.button("❌ Deny / override"):
            with st.spinner("Running Auditor Agent..."):
                final_state = pipeline["compiled"].invoke(
                    Command(resume={"approved": False, "feedback": feedback}),
                    config=pipeline["config"],
                )
            pipeline["stage"] = "done"
            pipeline["final_state"] = final_state
            st.rerun()

    elif pipeline["stage"] == "done":
        final_state = pipeline["final_state"]
        st.subheader("Outcome")
        approved_label = "✅ Approved by reviewer" if final_state["human_approved"] else "❌ Overridden by reviewer"
        st.markdown(f"**Recommended:** {final_state['decision'].upper()} — {approved_label}")

        st.subheader("🧑‍⚖️ Auditor compliance report")
        st.markdown(final_state["auditor_report"])

        _render_governance_panel(session)

        if session and not session.is_closed:
            session.close()
