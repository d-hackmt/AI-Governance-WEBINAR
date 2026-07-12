"""The 3-agent LangGraph pipeline: Analyst -> Decision -> human approval
(always-on interrupt) -> Auditor.

One GovernanceSession spans the whole run (see governance_wiring.py for why).
The human-in-the-loop checkpoint always fires — every run pauses for a real
approve/deny click before the Auditor's compliance report is produced. This
satisfies client_requirements.md Clause 1 ("no autonomous denial") and the
EU AI Act's human-oversight expectation for a high-risk credit decision.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, TypedDict

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agents.analyst import run_analyst  # noqa: E402
from agents.auditor import run_auditor  # noqa: E402
from agents.decision import run_decision  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.graph import END, StateGraph  # noqa: E402
from langgraph.types import interrupt  # noqa: E402


class PipelineState(TypedDict, total=False):
    application_id: str
    analyst_summary: str
    decision: str
    rationale: str
    human_approved: bool
    human_feedback: str
    auditor_report: str
    auditor_analysis: dict
    control_test_result: str


def build_graph(session, retriever, llms: dict, pii_authorization_claim: Optional[str]):
    """Compile the pipeline graph.

    llms: {"analyst": BaseChatModel, "decision": BaseChatModel, "auditor": BaseChatModel}
    pii_authorization_claim: set (non-None) only when the human reviewer has
        pre-authorized PII access for this specific application BEFORE the
        run starts — this is a human precondition, never something the LLM
        can grant itself.
    """

    def analyst_node(state: PipelineState) -> dict:
        summary = run_analyst(state["application_id"], session, retriever, llms["analyst"])
        return {"analyst_summary": summary}

    def decision_node(state: PipelineState) -> dict:
        result = run_decision(
            state["application_id"],
            state["analyst_summary"],
            session,
            pii_authorization_claim,
            llms["decision"],
        )
        return {"decision": result["decision"], "rationale": result["rationale"]}

    def human_approval_node(state: PipelineState) -> dict:
        approval = interrupt(
            {
                "application_id": state["application_id"],
                "decision": state["decision"],
                "rationale": state["rationale"],
            }
        )
        return {
            "human_approved": bool(approval.get("approved", False)),
            "human_feedback": approval.get("feedback", ""),
        }

    def auditor_node(state: PipelineState) -> dict:
        audit = run_auditor(
            state["application_id"],
            state["decision"],
            state["human_approved"],
            session,
            llms["auditor"],
        )
        return {
            "auditor_report": audit["report"],
            "auditor_analysis": audit["analysis"],
            "control_test_result": audit["control_test_result"],
        }

    graph = StateGraph(PipelineState)
    graph.add_node("analyst", analyst_node)
    graph.add_node("decision", decision_node)
    graph.add_node("human_approval", human_approval_node)
    graph.add_node("auditor", auditor_node)
    graph.set_entry_point("analyst")
    graph.add_edge("analyst", "decision")
    graph.add_edge("decision", "human_approval")
    graph.add_edge("human_approval", "auditor")
    graph.add_edge("auditor", END)

    return graph.compile(checkpointer=InMemorySaver())
