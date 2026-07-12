"""Analyst Agent — read-only. Builds a financial summary for one application
from public + internal company data only. Never touches applicant PII."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import governance_wiring  # noqa: E402
from langchain.agents import create_agent  # noqa: E402

ANALYST_PROMPT = (
    "You are the Analyst Agent for Northfield Community Lending's loan-review "
    "pipeline. Your job is strictly limited to public and internal company "
    "data: the quarterly financial summary and the internal credit policy "
    "thresholds, plus semantic search over that same material. You do not "
    "have access to any applicant's personal data — that is the Decision "
    "Agent's job, not yours. Produce a concise financial-context summary "
    "(the relevant credit policy thresholds, and any relevant company "
    "context) that the Decision Agent can use to evaluate one specific "
    "application. Do not recommend approve/deny yourself."
)


def run_analyst(application_id: str, session, retriever, llm) -> str:
    tools = [
        governance_wiring.build_read_company_financials_tool(session),
        governance_wiring.build_read_credit_policy_tool(session),
    ]
    if retriever is not None:
        tools.append(governance_wiring.build_search_company_knowledge_tool(session, retriever))
    agent = create_agent(model=llm, tools=tools, system_prompt=ANALYST_PROMPT)
    result = agent.invoke(
        {
            "messages": [
                (
                    "user",
                    f"Prepare the financial-context summary for loan application "
                    f"{application_id}. Check the internal credit policy thresholds "
                    f"and note anything relevant from the public company financials.",
                )
            ]
        }
    )
    return result["messages"][-1].content
