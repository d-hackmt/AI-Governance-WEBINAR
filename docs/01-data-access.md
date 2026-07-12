# Data & access tiers

**File:** [`src/data_access.py`](../src/data_access.py)

This file answers one question for every piece of company data: *how sensitive is this, and who's allowed to touch it?* Nothing in this file talks to `sentience-governor` — it's the plain facts layer that the governance layer (next doc) reads from.

## The four files

Made up by [`scripts/generate_dummy_data.py`](../scripts/generate_dummy_data.py) — never hand-edit anything in `data/`, just rerun that script to reset.

```mermaid
graph TD
    P["company_financials_public.xlsx<br/>tier: public"] -->|read + write| OK1["Anyone may read it.<br/>Only the audit-log write is special."]
    I["internal_credit_policy.csv<br/>tier: internal"] -->|read only| OK2["The actual thresholds a<br/>decision must be checked against"]
    PII["loan_applicants.xlsx<br/>tier: pii"] -->|read only, needs sign-off| OK3["One applicant's income,<br/>debt, credit score"]
    R["employee_salaries_confidential.xlsx<br/>tier: restricted"] -->|no access, ever| BLOCK["Nothing to do with a loan decision.<br/>Never legitimately opened."]
```

## The registry — one dictionary, single source of truth

`DATA_REGISTRY` is a plain Python dict mapping a name (like `"read_loan_applicants"`) to three facts: which file it points to, its sensitivity **tier**, and whether access is `"allow"` or `"deny"`.

That name is used as *both* the tool name the AI calls *and* the identifier `sentience-governor` logs against. Keeping those identical is what lets the governance layer catch an out-of-scope attempt without any custom matching code — it's a plain string comparison under the hood.

The tier words themselves (`public`, `internal`, `confidential`, `pii`, `restricted`) aren't made up for this project — they're the exact ladder `sentience-governor` already uses internally to detect "did this session just jump to more sensitive data than before?"

## The actual lock

`check_access(name)` is the real gatekeeper. Before any file is opened, it looks the name up in `DATA_REGISTRY` — if the tier says `"deny"`, it raises `AccessDenied` *before* `pandas` ever touches the file on disk.

```mermaid
graph LR
    A["Agent wants to read a file"] --> B{"check_access(name)"}
    B -->|tier says allow| C["Open the file, return its contents"]
    B -->|tier says deny| D["Raise AccessDenied —<br/>file is never opened"]
```

This check is deliberately plain Python with no dependency on the governance package. That's on purpose — see [Governance wiring](02-governance-wiring.md) for why `sentience-governor` *can't* be the one doing the blocking.

## The optional semantic search piece

`build_company_knowledge_index(jina_key)` builds a small search index (Jina embeddings + FAISS) over exactly two documents: the public financials and the internal credit policy. Notice what's *not* in that list — applicant records and the restricted file are never even given to the index-builder. That means the search tool can't leak PII by construction, independent of any runtime check.

`JinaEmbeddings` here is a small hand-written class (not the older `langchain_community` wrapper) that calls Jina's current embeddings API directly — see [LLM providers](05-llm-providers.md) for why.
