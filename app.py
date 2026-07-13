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

import os

# Must be set before numpy/faiss ever get imported anywhere in this process
# (they read these at native-library init time, not lazily) — faiss-cpu is a
# well-documented source of segfaults in constrained cloud containers when
# its OpenMP thread pool conflicts with the container's CPU/thread limits.
# This app's FAISS index is 2 documents; single-threaded costs nothing.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import json
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))

import data_access  # noqa: E402
import generate_dummy_data  # noqa: E402
from graph import build_graph  # noqa: E402
from governance_wiring import GovernanceSession  # noqa: E402
from langgraph.types import Command  # noqa: E402
from llm_providers import (  # noqa: E402
    FALLBACK_GROQ_MODEL,
    FALLBACK_MISTRAL_MODEL,
    build_chat_model,
    fetch_groq_models,
    fetch_mistral_models,
)

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
    fallback_model = FALLBACK_GROQ_MODEL if provider == "groq" else FALLBACK_MISTRAL_MODEL
    models = (
        (_cached_groq_models(key) if provider == "groq" else _cached_mistral_models(key))
        if key
        else ["(enter API key first)"]
    )
    # Default to a known-good, tool-calling-verified model rather than
    # whichever one happens to sort first alphabetically — that's exactly
    # how a model with no tool-calling support (e.g. Groq's allam-2-7b)
    # became a silent default the moment a key was entered, breaking every
    # agent (all of them need tool calling) without the user ever having
    # picked it themselves.
    default_index = models.index(fallback_model) if fallback_model in models else 0
    model = col2.selectbox(f"{role_label} model", models, index=default_index, key=f"model_{role_label}")
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


# ---------------------------------------------------------------------------
# Company data files — data/*.xlsx and data/*.csv are gitignored, so a fresh
# clone of this repo has nowhere to read from yet. Offer both paths: generate
# the bundled fictional dataset, or upload your own, one clearly-named field
# per required file so it's unambiguous which upload goes where.
# ---------------------------------------------------------------------------

missing_files = data_access.missing_data_files()
data_section_label = (
    "📁 Company data files — none found, set these up first"
    if missing_files
    else "📁 Company data files (loaded — expand to replace any of them)"
)

def _friendly_file_error(exc: Exception, filename: str) -> str:
    """PermissionError on Windows almost always means the file is open
    elsewhere (Excel keeps a lock file like '~$name.xlsx' while it's open) —
    give that concrete, actionable hint instead of a raw traceback."""
    if isinstance(exc, PermissionError):
        return (
            f"Couldn't write {filename} — it looks like it's open in Excel "
            "or another program right now. Close it there and try again."
        )
    return f"Couldn't write {filename}: {exc}"


with st.expander(data_section_label, expanded=bool(missing_files)):
    if missing_files:
        st.warning(
            f"Missing: {', '.join(missing_files)}. Generate the bundled sample "
            "dataset below, or upload your own files."
        )

    if st.button("🧪 Use bundled sample data (resets all 4 files)"):
        try:
            generate_dummy_data.main()
        except Exception as exc:  # noqa: BLE001 — surfaced to the UI, not swallowed
            st.error(_friendly_file_error(exc, "one of the sample data files"))
        else:
            _cached_applicant_ids.clear()
            _cached_applicant_preview.clear()
            st.rerun()

    st.divider()
    st.caption("Or upload your own — each field expects exactly this filename's data:")

    uploaded_by_filename: dict[str, object] = {}
    for spec in data_access.REQUIRED_DATA_FILES:
        have_it = spec["filename"] not in missing_files
        label = f"{spec['filename']}  ·  tier: {spec['tier']}" + ("  ✅ present" if have_it else "")
        uploaded = st.file_uploader(label, type=spec["extensions"], help=spec["hint"], key=f"upload_{spec['filename']}")
        if uploaded is not None:
            uploaded_by_filename[spec["filename"]] = uploaded

    if uploaded_by_filename and st.button("💾 Save uploaded file(s)"):
        any_failed = False
        for filename, uploaded_file in uploaded_by_filename.items():
            try:
                warning = data_access.save_uploaded_file(filename, uploaded_file.getvalue())
            except Exception as exc:  # noqa: BLE001 — surfaced to the UI, not swallowed
                st.error(_friendly_file_error(exc, filename))
                any_failed = True
            else:
                if warning:
                    st.warning(f"{filename}: {warning}")
                else:
                    st.success(f"{filename}: saved.")
        _cached_applicant_ids.clear()
        _cached_applicant_preview.clear()
        if not any_failed:
            st.rerun()

if missing_files:
    st.stop()

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

def _run_graph_safely(compiled, graph_input, config, spinner_text: str):
    """Runs compiled.invoke(...), returning (result, None) on success or
    (None, exception) on failure — so a caller can show a clean error
    instead of leaving st.session_state["pipeline"] half-updated after an
    unhandled exception (which is what made earlier crashes look like "the
    app just stopped working" rather than a readable error)."""
    try:
        with st.spinner(spinner_text):
            return compiled.invoke(graph_input, config=config), None
    except Exception as exc:  # noqa: BLE001 — surfaced to the UI, not swallowed
        return None, exc


def _show_run_error(exc: Exception) -> None:
    message = str(exc)
    if "tool calling" in message.lower() or "tool_calling" in message.lower():
        st.error(
            "One of the agents' selected models doesn't support tool calling "
            "(every agent here needs it, to read files / call tools). "
            "Pick a different model in the sidebar for the Analyst, Decision, "
            "and Auditor and try again — known-good picks: "
            "`llama-3.3-70b-versatile` or `llama-3.1-8b-instant` on Groq, "
            "`mistral-large-latest` or `mistral-small-latest` on Mistral."
        )
    else:
        st.error(f"The pipeline hit an error and stopped: {exc}")
    with st.expander("Full error details"):
        st.exception(exc)


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

        result, error = _run_graph_safely(
            compiled,
            {"application_id": application_id},
            config,
            "Running Analyst -> Decision (pausing for your approval next)...",
        )
        if error is not None:
            _show_run_error(error)
            if not session.is_closed:
                session.close()
        else:
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
        st.dataframe(rows, width="stretch")
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
            final_state, error = _run_graph_safely(
                pipeline["compiled"],
                Command(resume={"approved": True, "feedback": feedback}),
                pipeline["config"],
                "Running Auditor Agent...",
            )
            if error is not None:
                _show_run_error(error)
            else:
                pipeline["stage"] = "done"
                pipeline["final_state"] = final_state
                st.rerun()
        if c2.button("❌ Deny / override"):
            final_state, error = _run_graph_safely(
                pipeline["compiled"],
                Command(resume={"approved": False, "feedback": feedback}),
                pipeline["config"],
                "Running Auditor Agent...",
            )
            if error is not None:
                _show_run_error(error)
            else:
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
