# Internal Policy — Our Own Risk Appetite

Independent of what the client asked for or what the law requires, this is the bar we hold
ourselves to as the team building the agent. These are stricter in places than the client
contract or the regulations, because we'd rather over-govern a demo than under-govern one.

## Policy 1 — Every agent is registered, never anonymous
No agent runs without a declared `agent_id`, version, and declared capabilities. An
unregistered agent (Sentience's `POL-002`) is treated as a build error, not a runtime
condition to tolerate.

## Policy 2 — Intent before action
Every session declares what it's trying to do (`stated_objective`) and the scope of data it
expects to touch (`session_scope_hint`) before the first tool call. Acting without declared
intent is `POL-001` and gets flagged, not ignored.

## Policy 3 — Company data is never deleted by an agent
None of the agents in this demo are given a delete tool. This is enforced twice: there is no
`delete_file` tool wired into any agent's toolset, and even if one were added by mistake, the
target files are all opened in a mode that never truncates or removes rows on disk from
`src/data_access.py`.

## Policy 4 — Unclassified data and memory writes are not acceptable
Any context snapshot or memory write that doesn't carry an explicit data classification and
retention flag is a policy violation (`POL-003` / `POL-004`), not a "known limitation." The
demo intentionally shows what this looks like when it happens, so the audience can see the
difference between "flagged and explained" and "silently allowed."

## Policy 5 — Prompt injection attempts are surfaced, not just survived
If input data (e.g. a note field on an application) tries to redirect the agent toward
out-of-scope data, the correct outcome isn't just "the agent didn't fall for it" — it's "the
attempt is visible in the trace and in the UI." Silently resisting an attack teaches the
audience nothing; showing the attempt and the block does.
