# Industry Standards Mapping

Two voluntary frameworks referenced constantly in AI governance conversations, and exactly
where each one shows up as a concrete artifact in this repo — not just a checkbox.

## NIST AI RMF — Govern / Map / Measure / Manage

| NIST function | What it asks | Where it lives here |
|---|---|---|
| **Govern** | Set up policy and accountability before you build | `governance/profile.yaml` — the operator-authored governance profile that defines intent requirements, task-boundary signals, and high-consequence tool patterns |
| **Map** | Understand the risks of this specific use case | `governance/data_access_scope.md` — the explicit tiering of every data file by sensitivity |
| **Measure** | Test and quantify the risk | The Sentience Governor trace + `sentience pulse` style report the Auditor Agent produces after every run |
| **Manage** | Put controls in place and keep monitoring | The always-on human-in-the-loop checkpoint, plus the app-level access guard in `src/data_access.py` |

## ISO/IEC 42001 — documented, repeatable process
ISO 42001 doesn't grade the model — it grades whether you have a *repeatable process* for
managing AI risk. In this repo, that process is: same profile, same data tiers, same agent
graph, same trace format, run after run. Nothing about the governance behavior depends on
which applicant or which LLM provider you pick — that repeatability is the point being
demonstrated, not just the individual flags.
