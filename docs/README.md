# Docs — how this demo is put together

The top-level [`../README.md`](../README.md) explains *what* this demo shows and *why*. These pages explain *how* — one file per piece of code, in plain words, with a diagram, so you can point at any file in `src/` and know exactly what it's for.

Read them in this order if you're new to the project:

0. [The package: sentience-governor](00-sentience-governor.md) — what the library itself is, its classes, and its Python methods, independent of this project
1. [Data & access tiers](01-data-access.md) — the four files, who's allowed to touch what
2. [Governance wiring](02-governance-wiring.md) — how this project actually talks to `sentience-governor`
3. [The three agents](03-agents.md) — Analyst, Decision, Auditor
4. [The pipeline graph](04-graph.md) — how the agents are wired together, and the human checkpoint
5. [LLM providers](05-llm-providers.md) — BYOK, live model lists, the two provider bugs we found and fixed
6. [The web app](06-app-ui.md) — what each part of the Streamlit page does
7. [Reading the output](07-outputs.md) — what every number, flag, and banner in the results actually means

Every page below maps to one real file in [`src/`](../src/) or the project root — nothing here is aspirational or describes code that doesn't exist.
