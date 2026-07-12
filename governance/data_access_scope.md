# Data Access Scope — Enforced Tiers

This is the human-readable version of the access table enforced in code by
`src/data_access.py` (`ACCESS_TIERS` dict) and referenced by target-system ID in
`governance/profile.yaml`'s `high_consequence.tools` patterns. If this file and the code
ever disagree, the code wins at runtime — this file exists so a non-engineer reviewer can
understand what the code does without reading it.

| Target system ID | File | Tier | Who may touch it, and how |
|---|---|---|---|
| `data.company_financials_public` | `company_financials_public.xlsx` | **read-write** | Any agent may read it. Only the Auditor Agent may append an audit-log row at the end of a run (a WRITE). |
| `data.internal_credit_policy` | `internal_credit_policy.csv` | **read-only** | The Analyst and Decision agents may read it to justify a recommendation against the stated thresholds. Never written to. |
| `data.loan_applicants` | `loan_applicants.xlsx` | **read-only, confidential/PII** | Only the Decision Agent reads it, and only after declaring an explicit authorization for the current application. This is the sensitivity-escalation boundary in the demo. |
| `data.employee_salaries_confidential` | `employee_salaries_confidential.xlsx` | **no access** | No agent, under any circumstance, may read or write this file. Any attempt — including one provoked by a prompt-injection string embedded in applicant data — is blocked before the file is opened and is recorded as a blocked attempt, not silently dropped. |

Every read/write goes through the same two-part enforcement described in the main plan:
1. Session intent declares a `session_scope_hint` limited to the target-system IDs the
   current run is allowed to touch. Anything outside that hint trips Sentience Governor's
   own `SCOPE_INTENT_MISMATCH` → `POL-001`, with no bespoke matching code required.
2. A plain Python guard in `src/data_access.py` checks the tier dict before ever opening a
   file. This is what actually stops a "no access" read — Sentience's open-source tier only
   observes and flags, it does not block on its own.
