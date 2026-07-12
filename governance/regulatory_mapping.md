# Regulatory Mapping

Where the constraints in this project come from in actual law/guidance, not just internal
preference. (Background: see `../ai-governance-transcript.md` for the full walkthrough this
project is based on.)

## EU AI Act — high-risk system (Annex III: creditworthiness assessment)
Credit-scoring AI is explicitly named a **high-risk** system under the EU AI Act. High-risk
systems require, among other things: human oversight, logging/traceability, and risk
management documentation. This demo's mandatory human-in-the-loop checkpoint and Sentience
Governor trace are direct stand-ins for those obligations. (Note: high-risk obligations for
standalone systems like this one are staged to apply from December 2027 — this demo builds
the controls now, ahead of the deadline, which is the point.)

## India — DPDP Act (Digital Personal Data Protection Act)
`loan_applicants.xlsx` contains personal financial data. The DPDP Act requires clear,
explicit handling of personal data — no scraping or unconsented reuse. In this demo, that
translates into: PII access is a distinct, logged, authorization-gated event
(`POL-005` / sensitivity escalation), never a routine read indistinguishable from public data.

## India — AI Governance Guidelines (Feb 2026)
Built around seven principles: trust, people-first governance, innovation over restraint,
fairness and equity, accountability, understandability by design, safety and resilience.
The "understandability by design" and "accountability" principles map directly to the
Auditor Agent's plain-English compliance summary — a regulator or a loan officer should be
able to read it without knowing what a policy violation flag is.

## Client-touches-EU carve-out
If Northfield ever serves an EU-resident applicant, the EU AI Act mapping above applies in
full; for an India-only lender, the DPDP Act and India AI Governance Guidelines are the
primary floor. This demo is built to satisfy both simultaneously rather than assuming one
jurisdiction.
