# Current FOSSIL handoff

**Date:** 2026-08-31  
**Project:** FOSSIL — Fault-tolerant Open Semantic Store for Intellectual Lineage  
**Architecture authority:** Issue #86  
**Execution/claim ledger:** Issue #94

## Read this first

The old August 15/19 continuation path is superseded.

Do **not** resume:

- the old #111 semantic-freeze Step 1 sequence;
- retrieval/model bakeoff work from #47/#48;
- GraphRAG optimization;
- contextual-retrieval optimization;
- Qwen 4B/8B testing;
- deterministic/LLM routing experiments.

Those lanes have either completed, been rejected by evidence, or moved into historical/optional status.

## Current checkpoint

At the start of the v1 documentation task, live `main` was:

`c8217bbd978b7a3005f94fcb772d86568ea33d11` — `Add FOSSIL node MCP and HTTP network boundary (#234)`.

Always re-fetch live `main`; this SHA is a reconciliation anchor, not a lock.

The post-Gate-2 retrieval campaign is complete and Issues #47/#48 are closed.

Final retained policy:

```text
RAW source representation
    -> BM25 + revision-pinned D021 dense
    -> deterministic hybrid / RRF
    -> pinned cross-encoder reranker
    -> authorization / redaction boundary
    -> lifecycle / lineage resolution
    -> exact cited evidence
```

Decisions:

```text
RETAIN_RAW
RETAIN_D021
STOP_MODEL_LADDER
RETAIN_PROJECTION_NOT_RETRIEVAL
REJECT_ROUTING
LLM_PLANNER_DISABLED
RETRIEVAL_SECURITY_PASS
```

Final security evidence:

- 32 adversarial route cases;
- zero leaks;
- 23 focused tests passed;
- full non-network suite 709 passed, 1 skipped;
- 4 valid query-execution receipts;
- closeout head `0da699e58df85beea14a3dbc8fc9a048a5893b6d`.

## Current documentation task

`FOSSIL-V1-DOCS-01` exists to make the repository understandable without reconstructing weeks of issue history.

Documentation branch:

`docs/fossil-v1-20260831`

It is documentation-only. It must not change runtime, schema, storage, retrieval, models, MCP behavior, hosting, Cortex, LiteLLM, or canonical FOSSIL semantics.

The intended documentation set is:

- `README.md` — what FOSSIL does, problems it solves, final v1 policy, proof summary, install;
- `docs/GETTING_STARTED.md` — install and first durable operations;
- `docs/USING_FOSSIL.md` — practical library/node/MCP workflows;
- `docs/BENCHMARKS.md` — matched benchmark results and scope caveats;
- `docs/SECURITY_MODEL.md` — authorization/redaction/untrusted-context boundary;
- `docs/PROJECT_STATE.md` — current actual state and remaining closeout;
- this file — exact continuation.

## Next real code/repository task after docs

After this documentation PR is reviewed/merged, the next bounded repository task should be:

**`FOSSIL-V1-CONSOLIDATION-01` — reconcile accepted post-Gate-2 evidence into current main and declare the v1 boundary.**

This is not permission to merge experiment branches wholesale.

Preflight should inventory the accepted branches and selectively reconcile the intended artifacts/tests/decision records:

- GraphRAG evidence branch `task/fossil-graphrag-bench-01`;
- contextual branch `task/fossil-contextual-retrieval-bench-01`;
- embedding branch `codex/fossil-embedding-final-01-20260830`;
- routing branch `codex-fossil-routing-final-01-20260830`;
- security branch `codex/fossil-retrieval-security-final-20260831`.

The consolidation must preserve all accepted decisions and run exact-head full CI/assurance after reconciliation.

## Off-machine durability decision

Issue #87's provider-neutral S3-compatible implementation and real MinIO service proof are already meaningful completed evidence.

A live R2/AWS S3 proof is an **owner-level release/operations decision**, not permission to reopen storage architecture.

Two legitimate outcomes exist:

```text
A. v1 requires off-machine live durability
   -> run the existing credentialed live object-store gate
   -> prove fresh remote recovery

B. v1 local/core release does not require provider activation
   -> explicitly defer live provider activation
   -> retain the proven S3-compatible contract and fixture evidence
```

Do not choose R2/S3 provider semantics as canonical truth.

## Persistent node / MCP state

NODE-01 and NODE-02 are merged on main.

The node has:

- canonical filesystem evidence/source/event stores;
- separate operational projection ledger;
- restartable Graphiti projector;
- `CorpusService`;
- reviewed ingestion;
- `/mcp`, `/healthz`, `/readyz`, `/ingest`;
- seven MCP tools: search/read/lineage/propose/validate/commit/manage.

Public bearer-authenticated Internet deployment remains a separate unmerged/operational lane. Do not make public hosting or ChatGPT-specific integration a prerequisite for v1 core consolidation unless the owner explicitly reactivates that requirement.

## Frozen boundaries

- Canonical evidence/events/contracts/provenance/lifecycle remain authority.
- Graph/vector indexes and models are replaceable projections/services.
- Graphiti stays projection-only for normal v1 retrieval.
- D021 remains the pinned dense embedding incumbent.
- The model ladder is stopped.
- The fixed RAW_RERANKED policy remains the normal retrieval policy.
- Retrieval text is untrusted data, not executable policy.
- Pack/ACL/redaction filtering cannot be bypassed by retrieval or reranking.
- Agents propose; deterministic gates commit.
- Do not weaken acceptance thresholds to obtain PASS.
- Do not invent new RAG experiments during closeout.

## Fresh-session procedure

Before any repository mutation:

1. Read `AGENTS.md`.
2. Read `README.md`, `ARCHITECTURE.md`, `docs/PROJECT_STATE.md`, and this file.
3. Re-fetch live Issue #94 comments.
4. Re-fetch current `main` and relevant PR/branch heads.
5. Confirm no active winning mutation claim exists for the intended lane.
6. Claim one bounded task in #94 and immediately re-fetch to confirm ownership.
7. Work only inside the claimed scope.
8. Close with exact mechanical evidence using `DONE`, `BLOCKED`, or `RELEASE`.

If old issue bodies conflict with live code, accepted closeout evidence, or this reconciled documentation, investigate/reconcile them rather than blindly executing their unchecked boxes.

## Stop condition

Once documentation and v1 consolidation are merged and the remote-durability decision is explicitly recorded, stop treating FOSSIL as an open-ended RAG/architecture research campaign.

At that point, new work should be driven by actual use: ingesting knowledge, integrating real clients, fixing observed defects, or adding evidence-backed capabilities when a real corpus requires them.
