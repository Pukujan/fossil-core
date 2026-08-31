# Current Handoff

**Date:** 2026-08-19  
**Project:** **FOSSIL — Fault-tolerant Open Semantic Store for Intellectual Lineage**  
**Repository:** `Pukujan/fossil-core`  
**Architecture authority:** Issue #86  
**Execution queue / claim ledger:** Issue #94  
**Semantic hardening authority:** Issue #111  
**PDD / formal assurance campaign:** Issue #176

## Current checkpoint

Verified pre-handoff `main`:

`e14ef747547e86add2d3e819a537c1a8d2b35294` — `[ARCH] Accept issue #111 semantic freeze (#221)`

Do **not** treat that SHA as a live lock. Re-fetch `main`, Issue #94, Issue #111, and Issue #176 before any mutation.

The detailed current-session transfer record is:

`docs/handoffs/2026-08-19-pdd-semantic-freeze-session-handoff.md`

Read that file before resuming work.

## Immediate state

The public PDD campaign has landed its property catalog/oracles, currently unblocked mutation lanes, public hidden-holdout receipt/manifest boundary, TLA+/Lean foundations, CI hygiene, and fail-closed formal-reference traceability.

PR #221 has now merged the accepted #111 semantic freeze. This **freezes the semantic target but does not complete #111 implementation**.

The required post-freeze implementation sequence is:

1. event-type contract/evidence-policy registry + deterministic fail-closed accepted-commit gates;
2. `dkg.packset-lock.v1` + exact revision locking + cycle/layer validation + replay/portability tests;
3. versioned promotion payload with source revision/event pin + source-resolvability tests;
4. longitudinal epistemic benchmark;
5. reviewed evidence ingestion + compact receipt.

Promotion mutation/Lean work under #176 must wait until the Promotion law and its prerequisites are implemented. Do not skip directly to formal/mutation evidence merely because the semantic freeze is accepted.

## Exact stop point

The previous session briefly claimed Step 1, characterized the current event envelope / event store / agent boundary, then the user requested a session stop and durable handoff.

That Step 1 claim was explicitly released. There were:

- no Step 1 repository-file changes;
- no Step 1 implementation branch to preserve;
- no Step 1 PR to resume.

Restart from live `main` after re-reading #94.

## Hidden holdout boundary

The abstract least-privilege mechanism is approved, but concrete private placement/verifier provisioning remains out-of-band.

Public repository rules remain strict:

- no sealed cases;
- no private exact oracles;
- no credentials;
- no private paths/URLs;
- no verifier identity;
- public receipts contain safe aggregate evidence only.

No sealed-execution PASS is currently claimed.

## Mandatory coordination

Before any GitHub mutation:

```text
CLAIM task=<TASK_ID>
agent=<unique-agent-id>
mode=<LOCAL_CODEX|CLOUD_CODEX|CHATGPT|ACTIONS>
lease_until=<ISO-8601 UTC>
repo=<repo>
starting_ref=<branch/SHA/PR>
scope=<bounded scope>
parallel_safe=<yes|no>
```

Immediately re-fetch #94 and confirm the claim wins. One active mutating owner per repo lane unless explicitly parallel-safe.

Close work with `DONE`, `BLOCKED`, or `RELEASE` and exact evidence.

Before merge, re-check exact head, exact-head CI, current main, changed-file scope, mergeability, reviews, review threads, conversation blockers, and #94 ownership. Use SHA-fenced merge for bounded assurance/architecture PRs.

## Frozen boundaries

- Compute/projections/models are replaceable; durable evidence/events/contracts/provenance remain authority.
- Retrieval rank, reranker score, model confidence, and multi-model agreement are not truth.
- Historical events must remain replayable and must not be silently upgraded.
- Ordinary PR CI remains secretless.
- Never weaken acceptance merely to obtain green.
- Never expose private hidden-holdout material.
- Never conflate mutation / holdout / TLA+ / Lean in one slice.
- No production promotion or deployment is authorized by this handoff.
- Do not touch unrelated open PRs just to keep the queue busy.

## Fresh-session first action

1. Read `AGENTS.md`, `ARCHITECTURE.md`, #86, latest #94, #111, #176, this file, and the dated handoff.
2. Re-fetch current `main`.
3. Confirm no newer owner/PR exists for #111 Step 1.
4. If unowned, claim a bounded Step 1 lane and characterize all event types/direct durable-store callers before implementation.
5. If another owner exists, do not duplicate it.

If live GitHub state differs from this document, live GitHub wins.

## 2026-08-31 retrieval campaign continuation

The final embedding gate retained D021 and stopped the Qwen ladder. Graphiti/Neo4j remains projection-only and contextual enrichment was rejected. The deterministic routing gate `FOSSIL-ROUTING-FINAL-01` is now complete from live `main` at `c8217bbd978b7a3005f94fcb772d86568ea33d11` on branch `codex/fossil-routing-final-01-20260830`.

Routing compared fixed `RAW_RERANKED` against a six-class lexical router over 27 documents, 51 events, 21 retrieval cases, and 6 answer cases. Fixed hit/recall@5/MRR was `1.000/1.000/0.873` with zero decision-critical misses. The router was `0.952/0.937/0.867`, introduced one decision-critical current-architecture miss, and reduced retrieval p95 only from `173.40 ms` to `164.34 ms`, below the 10% material-latency threshold. Answer/citation/unsupported behavior was unchanged. Decision: `REJECT_ROUTING`; keep fixed `RAW_RERANKED`; do not build an LLM planner.

Evidence is in `docs/implementation/2026-08-31-final-routing-benchmark-proof.md`, `benchmarks/post-gate2/results/2026-08-31-routing-final/report.json`, and `receipts.jsonl`. The next and final RAG campaign task is `FOSSIL-RETRIEVAL-SECURITY-FINAL-01`: prove BM25, dense, hybrid, and reranked routes cannot bypass sensitivity/ACL/redaction/suppression filters. Do not close #47 or #48 until that security gate and final reconciliation are complete.

## Final RAG campaign closeout — 2026-08-31

`FOSSIL-RETRIEVAL-SECURITY-FINAL-01` passed on security implementation SHA `7e69bdb955b74e7bfadb71b8ebf6cdbab77a295f`, starting from routing SHA `31a6644040db97a71baaf80778845d4a70df68da`. The proof covered BM25, D021 dense, hybrid/RRF, pinned reranked hybrid, direct canonical reads, citations, exports, receipts, answer/context construction, disposable projection inspection, and projection rebuild. Across 32 adversarial route/case observations, the caller-scoped routes had zero unauthorized IDs/text/citations, reranker promotions, cross-pack results, suppression/redaction resurfacing, projection leaks, or receipt/export leaks. Four receipts validated; sidecar SHA-256 is `d18744f61a8d338701a2db4ca083a6493a5392a6889e0daf93c21232d28c559e`.

Final retrieval policy is frozen as `RAW` + D021 + BM25 + deterministic RRF + pinned cross-encoder, normal policy `RAW_RERANKED`; routing is `REJECT_ROUTING`, LLM planner `DISABLED`, contextual enrichment not promoted, Graphiti `RETAIN_PROJECTION_NOT_RETRIEVAL`, embedding ladder `STOP_MODEL_LADDER`, security boundary `PASS`. The final embedding result retained D021 over equally accurate but slower/larger Qwen 0.6B and did not justify 4B/8B. The final routing result retained fixed policy at hit/recall/MRR `1.000/1.000/0.873` versus router `0.952/0.937/0.867`, with one router critical miss and insufficient latency gain.

Issue #47 and Issue #48 were reconciled and closed after this proof. Issue #94 received the exact `DONE` closeout. The RAG campaign is complete; no next RAG experiment is authorized. Cortex V5 and LiteLLM/CKFF remain read-only, and no production promotion is authorized.
