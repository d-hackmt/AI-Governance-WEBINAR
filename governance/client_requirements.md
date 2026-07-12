# Client Requirements — Contract Clauses (Fictional)

This project is built for a fictional client, **Northfield Community Lending**, a small
lender that wants an AI assistant to help loan officers triage credit applications faster.
These are the clauses from their statement of work that constrain what we're allowed to build.

## Clause 1 — No autonomous denial
> "The system may recommend approval or denial, but a human loan officer must review and
> approve every decision before the applicant is notified. The AI does not have final say."

**How the demo satisfies this:** the LangGraph workflow always pauses at a human-in-the-loop
checkpoint after the Decision Agent proposes an outcome (see `src/graph.py`). No decision
reaches the applicant without a human clicking Approve.

## Clause 2 — Full traceability
> "Northfield must be able to show a regulator, on request, exactly what data the AI looked
> at and why it reached its recommendation, for every application processed."

**How the demo satisfies this:** every tool call, intent declaration, and data access is
captured by Sentience Governor as a structured trace, and summarized by the Auditor agent
at the end of each run.

## Clause 3 — Least-privilege data access
> "The AI may read applicant financial data relevant to the specific application it is
> processing. It must never access unrelated company records (e.g. payroll) and must never
> delete any company data."

**How the demo satisfies this:** each data file has an explicit access tier (read-only /
read-write / no-access) enforced in `src/data_access.py`, and the AI's declared intent scope
only ever covers what the current application needs — see `data_access_scope.md`.

## Clause 4 — PII handling
> "Applicant personal and financial information is confidential. Any access to it must be
> logged and require an explicit authorization step, separate from routine company data
> access."

**How the demo satisfies this:** reading `loan_applicants.xlsx` (confidential/PII tier) after
only touching public data is treated as a sensitivity escalation — Sentience Governor's
`POL-005` rule — and requires an authorization flag before the Decision Agent can proceed.
