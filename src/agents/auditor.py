"""Governance/Auditor Agent — the 'prove it' step. Runs Sentience Governor's
own analyzers over the session's real trace, performs a control test against
the restricted data file (confirming the boundary actually holds, not just
trusting it does), and produces a plain-English compliance summary grounded
in this project's actual governance documents.

The control test is run directly in Python, NOT delegated to the LLM as a
tool call. An earlier version gave the model a tool and asked it to "run a
control test" — and it fabricated a plausible, correctly-formatted result
(complete with a specific POL code) without ever calling the tool: only 2
events existed in the trace afterward (AGENT_REGISTERED, INTENT_DECLARED),
no SCOPE_ASSERTED at all. That's the exact failure mode governance exists to
catch — an actor's self-report of what it did, unverified against a real
trace — so it would have been indefensible to leave a compliance-critical
check sitting on model discretion. The test now always actually runs; the
LLM only summarizes its real, already-known result.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import governance_wiring  # noqa: E402

GOVERNANCE_DIR = Path(__file__).resolve().parent.parent.parent / "governance"

AUDITOR_SYSTEM_PROMPT_TEMPLATE = """You are the Governance/Auditor Agent for Northfield \
Community Lending's loan-review pipeline. You do not make or influence the \
credit decision — you review, after the fact, whether this session's data \
access was compliant, and you report in plain English a non-technical \
stakeholder (a regulator, a client, a loan officer) can follow.

Ground your summary in these actual project documents, don't invent policy:

--- governance/client_requirements.md ---
{client_requirements}

--- governance/policy_constraints.md ---
{policy_constraints}

--- governance/regulatory_mapping.md ---
{regulatory_mapping}

--- governance/industry_standards.md ---
{industry_standards}

You will be given, as established fact (already executed and logged — not \
something you need to verify or repeat), the result of a control test \
against the restricted employee-salary file, plus this session's advisory \
flag counts, policy violation counts, and blocked-attempt log. Write a short \
compliance summary: what happened, which flags/violations fired and why, \
and which client clause / internal policy / regulation each one maps back \
to. Be direct about anything that fired — don't downplay it, and don't \
overstate what the open-source tooling actually enforces (observe + flag) \
versus what this application's own code enforced (the actual block).
"""


def _load_governance_docs() -> dict[str, str]:
    return {
        "client_requirements": (GOVERNANCE_DIR / "client_requirements.md").read_text(encoding="utf-8"),
        "policy_constraints": (GOVERNANCE_DIR / "policy_constraints.md").read_text(encoding="utf-8"),
        "regulatory_mapping": (GOVERNANCE_DIR / "regulatory_mapping.md").read_text(encoding="utf-8"),
        "industry_standards": (GOVERNANCE_DIR / "industry_standards.md").read_text(encoding="utf-8"),
    }


def _run_control_test(session) -> str:
    """Directly (not via the LLM) attempt the restricted read, so the block
    is always actually exercised and always actually in the trace."""
    control_tool = governance_wiring.build_read_employee_salaries_tool(session)
    return control_tool.invoke({})


def run_auditor(
    application_id: str,
    decision: str,
    human_approved: bool,
    session,
    llm,
) -> dict:
    control_test_result = _run_control_test(session)
    analysis = session.run_analyzers()

    system_prompt = AUDITOR_SYSTEM_PROMPT_TEMPLATE.format(**_load_governance_docs())
    user_message = (
        f"Application: {application_id}\n"
        f"Decision recommended: {decision}\n"
        f"Human approved: {human_approved}\n\n"
        f"Control test result (already executed): {control_test_result}\n\n"
        f"Advisory flag counts: {analysis['advisory_flag_counts']}\n"
        f"Policy violation counts: {analysis['policy_violation_counts']}\n"
        f"Blocked access attempts this session: {session.blocked_attempts}\n\n"
        "Write the compliance summary."
    )
    response = llm.invoke(
        [
            ("system", system_prompt),
            ("user", user_message),
        ]
    )
    return {
        "report": response.content,
        "control_test_result": control_test_result,
        "analysis": analysis,
    }
