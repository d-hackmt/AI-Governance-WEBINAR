"""Wires the loan-review pipeline to Sentience Governor at the primitive layer.

Why not the LangChain callback handler? sentience_governor.wrapper.langchain_adapter
.SentienceCallbackHandler is a generic shim: every CONTEXT_SNAPSHOT it emits is
hardcoded classification_source=unclassified, so POL-003 fires on literally every
tool call and POL-005 (sensitivity escalation) can never fire (escalation needs
real, varying classifications to compare against). That's fine for a
zero-config demo of *that* package feature, but it can't tell the difference
between reading public financials and reading an applicant's PII — which is
the entire point of this demo.

So instead this module uses the same primitives the adapter is built on
(SessionManager, InProcessCache, EventBuilder, GovernanceProfile) directly,
and tags every read/write with its *real* sensitivity tier. That's what lets
POL-005 fire honestly when the Decision agent moves from public data to PII,
and what lets POL-003 correctly NOT fire on properly classified reads.

One GovernanceSession spans the WHOLE pipeline run (Analyst -> Decision ->
Auditor), not one per agent — sensitivity-escalation and task-boundary
detection are session-scoped, so splitting sessions per agent would hide
exactly the cross-agent risk signal this demo exists to show.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import data_access  # noqa: E402

from langchain_core.tools import tool  # noqa: E402
from sentience_governor.analyze.policy_violation_burn_rate import (  # noqa: E402
    compute_policy_violation_burn_rate,
)
from sentience_governor.analyze.undeclared_intent import (  # noqa: E402
    compute_undeclared_intent_spend,
)
from sentience_governor.cache.cache import InProcessCache  # noqa: E402
from sentience_governor.event_builder.builder import EventBuilder  # noqa: E402
from sentience_governor.profile.loader import GovernanceProfile  # noqa: E402
from sentience_governor.schema.events import (  # noqa: E402
    ClassificationSource,
    DetectionMechanism,
    IntentConfidence,
    IntentSource,
    OperationType,
    WriteType,
)
from sentience_governor.session_manager.manager import SessionManager  # noqa: E402
from sentience_governor.sink.writer import SinkBase, SinkWriter  # noqa: E402

PROFILE_PATH = Path(__file__).resolve().parent.parent / "governance" / "profile.yaml"
AGENT_ID = "northfield-loan-review-pipeline"
AGENT_VERSION = "0.1.0"

# Module-level singletons — shared across every GovernanceSession the same
# way a real deployment would share one session manager / cache across runs.
# (SessionManager spins up a background reaper thread; only ever want one.)
_SESSION_MANAGER = SessionManager()
_CACHE = InProcessCache()


class InMemorySink(SinkBase):
    """Collects every event as a plain dict — what the analyzers and the
    Streamlit governance panel both consume, no file round-trip required."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def write(self, event) -> bool:
        self.events.append(event.to_dict())
        return True


class GovernanceSession:
    """One Sentience Governor session spanning the full 3-agent pipeline run."""

    def __init__(self) -> None:
        self.session_id = str(uuid.uuid4())
        # Unique per run, not the shared constant AGENT_ID — SessionManager
        # tracks "one active session per agent_id" and force-closes the
        # prior one on a collision (by design, for a real long-running
        # agent process). On a deployed app, one Python process can be
        # serving multiple browser sessions/users concurrently, all
        # importing this same module and its module-level _SESSION_MANAGER.
        # A fixed agent_id meant every concurrent run collided with
        # whichever other run started most recently, force-closing
        # sessions still mid-flight waiting on a human's Approve/Deny click
        # (visible in logs as "SESSION_FORCE_CLOSED"). Suffixing with this
        # run's own session_id makes every run's agent_id unique, so two
        # concurrent runs can never collide.
        self.agent_id = f"{AGENT_ID}-{self.session_id}"
        self._sink = InMemorySink()
        self._sink_writer = SinkWriter(self._sink)
        self.profile = (
            GovernanceProfile.from_file(PROFILE_PATH)
            if PROFILE_PATH.is_file()
            else GovernanceProfile.defaults()
        )
        _SESSION_MANAGER.session_start(
            session_id=self.session_id, agent_id=self.agent_id, profile=self.profile
        )
        _CACHE.init_session(self.session_id)
        self.builder = EventBuilder(
            session_manager=_SESSION_MANAGER,
            cache=_CACHE,
            agent_id=self.agent_id,
            session_id=self.session_id,
        )
        self.blocked_attempts: list[dict] = []
        self._closed = False

    # ------------------------------------------------------------------
    # The four control points this demo actually exercises
    # ------------------------------------------------------------------

    def register(self, declared_capabilities: list[str]) -> None:
        event = self.builder.build_agent_registered(
            agent_version=AGENT_VERSION,
            vendor_id="northfield-community-lending",
            declared_capabilities=declared_capabilities,
            owner_claim="loan-review-desk",
        )
        self._emit(event)

    def declare_intent(self, stated_objective: str, session_scope_hint: list[str]) -> None:
        event = self.builder.build_intent_declared(
            stated_objective=stated_objective,
            intent_source=IntentSource.explicit,
            intent_confidence=IntentConfidence.explicit,
            authorization_claim=None,
            session_scope_hint=session_scope_hint,
        )
        self._emit(event)

    def access_data(
        self,
        name: str,
        call_fn: Callable[..., Any],
        *,
        authorization_claim: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """Governed read/write: emits SCOPE_ASSERTED first (so the attempt is
        on the record even if it's about to be denied), enforces the actual
        block for 'deny' tier entries, then emits a correctly-classified
        CONTEXT_SNAPSHOT for allowed reads/writes."""
        entry = data_access.DATA_REGISTRY[name]
        operation_type = OperationType.WRITE if name.startswith("write_") else OperationType.READ

        scope_event = self.builder.build_scope_asserted(
            tool_id=name,
            asserted_permissions=[operation_type.value.lower()],
            target_system=name,
            operation_type=operation_type,
            authorization_claim=authorization_claim,
        )
        self._emit(scope_event)

        if entry["access"] == "deny":
            raise data_access.AccessDenied(
                f"'{name}' is a restricted data surface. This {operation_type.value} "
                f"was blocked before {entry['file']} was opened."
            )

        result = call_fn(**kwargs)

        ctx_event = self.builder.build_context_snapshot(
            data_classifications=[entry["tier"]],
            classification_source=ClassificationSource.explicit,
            provenance=[name],
            retention_flags=["session-only"],
            context_size_tokens=len(str(result).split()),
            authorization_claim=authorization_claim,
        )
        self._emit(ctx_event)
        return result

    def record_blocked(self, tool_name: str, reason: str) -> None:
        self.blocked_attempts.append({"tool": tool_name, "reason": reason})

    def memory_write(self, *, target_store: str, write_size_tokens: int, classified: bool) -> None:
        """Records the RAG index build as a MEMORY_WRITE_ATTEMPT.

        Deliberately left unclassified (no retention flag) on this demo's
        golden path — POL-004 fires on purpose here, as a live example of
        what an unclassified memory write looks like (see
        governance/policy_constraints.md, Policy 4).
        """
        event = self.builder.build_memory_write_attempt(
            write_type=WriteType.write_to_persistence_target,
            detection_mechanism=DetectionMechanism.config_registry,
            target_store=target_store,
            write_classification="internal" if classified else "unclassified",
            write_size_tokens=write_size_tokens,
            retention_requested="session-only" if classified else None,
        )
        self._emit(event)

    # ------------------------------------------------------------------

    def _emit(self, event) -> None:
        if event is not None:
            self._sink_writer.write(event, self.session_id)

    def close(self) -> None:
        if self._closed:
            return
        self._sink_writer.session_closed(self.session_id, self.agent_id)
        _SESSION_MANAGER.session_end(self.session_id)
        _CACHE.clear_session(self.session_id)
        self._closed = True

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def events(self) -> list[dict]:
        return list(self._sink.events)

    def run_analyzers(self) -> dict:
        events = self.events
        undeclared = compute_undeclared_intent_spend(events)
        burn_rate = compute_policy_violation_burn_rate(events)

        advisory_counts: dict[str, int] = {}
        violation_counts: dict[str, int] = {}
        for e in events:
            for flag in e.get("advisory_flags", []):
                advisory_counts[flag] = advisory_counts.get(flag, 0) + 1
            for violation in e.get("policy_violations", []):
                violation_counts[violation] = violation_counts.get(violation, 0) + 1

        return {
            "undeclared_intent": undeclared,
            "policy_violation_burn_rate": burn_rate,
            "advisory_flag_counts": advisory_counts,
            "policy_violation_counts": violation_counts,
        }


# ---------------------------------------------------------------------------
# Governed tool factories — one per data surface, each a thin, explicit
# wrapper so the LLM sees a normal typed tool while every call is scoped,
# classified, and (for the restricted file) actually blocked underneath.
# ---------------------------------------------------------------------------

# Fixed "standing" authorization for routine, non-PII company data — this is
# what client_requirements.md Clause 4 means by "separate from routine company
# data access": public financials and internal policy don't need a per-call
# human authorization the way applicant PII does. Sensitivity-escalation still
# gets FLAGGED (advisory) when tier increases even with this claim present —
# only the POL-005 *violation* is suppressed, so the trace still shows the
# escalation happened, just an authorized one.
_ROUTINE_DATA_AUTHORIZATION = "standing-authorization:routine-company-data"


def build_read_company_financials_tool(session: GovernanceSession):
    def read_company_financials_public() -> str:
        """Read the company's public quarterly financial summary: loans issued, total value, default rate."""
        try:
            return session.access_data(
                "read_company_financials_public",
                data_access.read_company_financials_public,
                authorization_claim=_ROUTINE_DATA_AUTHORIZATION,
            )
        except data_access.AccessDenied as e:
            session.record_blocked("read_company_financials_public", str(e))
            return f"ACCESS BLOCKED: {e}"

    return tool(read_company_financials_public)


def build_read_credit_policy_tool(session: GovernanceSession):
    def read_internal_credit_policy() -> str:
        """Read the internal credit policy thresholds (minimum credit score, max debt-to-income, etc)."""
        try:
            return session.access_data(
                "read_internal_credit_policy",
                data_access.read_internal_credit_policy,
                authorization_claim=_ROUTINE_DATA_AUTHORIZATION,
            )
        except data_access.AccessDenied as e:
            session.record_blocked("read_internal_credit_policy", str(e))
            return f"ACCESS BLOCKED: {e}"

    return tool(read_internal_credit_policy)


def build_search_company_knowledge_tool(session: GovernanceSession, retriever):
    def search_company_knowledge(query: str) -> str:
        """Semantic search over public + internal company documents (financial summary, credit policy). Never returns applicant or employee data."""

        def _do_search(query: str) -> str:
            docs = retriever.invoke(query)
            return "\n---\n".join(d.page_content for d in docs)

        try:
            return session.access_data(
                "search_company_knowledge",
                _do_search,
                authorization_claim=_ROUTINE_DATA_AUTHORIZATION,
                query=query,
            )
        except data_access.AccessDenied as e:
            session.record_blocked("search_company_knowledge", str(e))
            return f"ACCESS BLOCKED: {e}"

    return tool(search_company_knowledge)


def build_read_loan_applicants_tool(session: GovernanceSession, authorization_claim: Optional[str]):
    def read_loan_applicants(application_id: str) -> str:
        """Read the confidential personal/financial record for one loan application_id. Requires prior human authorization for PII access."""
        try:
            return session.access_data(
                "read_loan_applicants",
                data_access.read_loan_applicants,
                authorization_claim=authorization_claim,
                application_id=application_id,
            )
        except data_access.AccessDenied as e:
            session.record_blocked("read_loan_applicants", str(e))
            return f"ACCESS BLOCKED: {e}"

    return tool(read_loan_applicants)


def build_read_employee_salaries_tool(session: GovernanceSession):
    def read_employee_salaries_confidential() -> str:
        """Read confidential employee salary records. Not authorized for use in loan-review sessions under any circumstance."""
        try:
            return session.access_data(
                "read_employee_salaries_confidential",
                data_access.read_employee_salaries_confidential,
            )
        except data_access.AccessDenied as e:
            session.record_blocked("read_employee_salaries_confidential", str(e))
            return f"ACCESS BLOCKED: {e}"

    return tool(read_employee_salaries_confidential)


def build_write_decision_log_tool(session: GovernanceSession):
    def write_decision_log(application_id: str, decision: str) -> str:
        """Append the final, human-approved decision (approve/deny/refer) for an application to the public audit log."""
        try:
            return session.access_data(
                "write_decision_log",
                data_access.append_decision_log,
                application_id=application_id,
                decision=decision,
                agent_id=session.agent_id,
                session_id=session.session_id,
            )
        except data_access.AccessDenied as e:
            session.record_blocked("write_decision_log", str(e))
            return f"ACCESS BLOCKED: {e}"

    return tool(write_decision_log)
