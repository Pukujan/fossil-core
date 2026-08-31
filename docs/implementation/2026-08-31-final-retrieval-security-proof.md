# FOSSIL-RETRIEVAL-SECURITY-FINAL-01 — final retrieval ACL/redaction proof

**Date:** 2026-08-31  
**Decision:** `PASS`  
**Security boundary:** `PASS`  
**Authority:** authorization determines caller visibility; durable FOSSIL evidence, lifecycle, lineage, provenance, citation, and redaction semantics remain authoritative.

## Exact execution

- Starting SHA: `31a6644040db97a71baaf80778845d4a70df68da` (routing-final branch, based on live `main` `c8217bbd978b7a3005f94fcb772d86568ea33d11`).
- Execution/final security implementation SHA: `7e69bdb955b74e7bfadb71b8ebf6cdbab77a295f`.
- Canonical pack pins were validated before the proof: fossil-common `d583005dce06dbb499c3c0de5c22b899655eb8d2`, fossil-ai-systems `84accd2ee895663990e82ca5b79b592cb503db24`; the validated corpus remained 27 projected documents and 51 events.

## Frozen policy and routes

The proof exercised the retained policy only:

`RAW canonical source → BM25 + D021 → deterministic hybrid/RRF → pinned cross-encoder reranker → lifecycle/lineage resolution → authorized canonical evidence → exact citation/answer`.

The final decisions are:

| Decision | Retained value |
| --- | --- |
| representation | `RAW` |
| embedding | `D021 / BAAI bge-small-en-v1.5` at pinned revision `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a` |
| lexical | `BM25` |
| fusion | deterministic hybrid/RRF |
| reranker | retained pinned cross-encoder |
| normal policy | `RAW_RERANKED` |
| routing | `REJECT_ROUTING` |
| LLM planner | `DISABLED` |
| contextual enrichment | `RETAIN_RAW / not promoted` |
| Graphiti | `RETAIN_PROJECTION_NOT_RETRIEVAL` |
| embedding ladder | `STOP_MODEL_LADDER` |
| security boundary | `PASS` |

No model, GraphRAG, contextual-enrichment, router/planner, Cortex V5, LiteLLM, MCP, hosting, or deployment change was made.

## Security model and filtering location

`RetrievalVisibilityPolicy` requires a readable pack, non-empty ACL containing the caller or `*`, an allowed sensitivity level, and neither suppression nor redaction. Missing security metadata fails closed. Documents are deep-copied into a caller-scoped projection before BM25/D021 index construction. `SecurityFilteredRetriever` checks the requested pack and validates every returned candidate again, so a leaky component cannot cross the final boundary. Direct reads, citation authorization, and exports use the same policy.

The proof caller was `alice`, allowed only `pack_security_a` and `internal` sensitivity. The 9-document fixture contained 3 ACL-denied records, 1 foreign-pack record, 1 sensitivity-restricted record, and 1 suppressed/redacted record. Lifecycle state was retained as metadata; security never changed or encoded lifecycle truth.

## Adversarial evidence

The deterministic fixture covered exact identifier, conceptual dense, hybrid component, reranked, same-topic cross-pack, suppressed projection, restricted sensitivity, and stale/superseded unauthorized cases. It additionally attempted denied direct read, denied citation construction, denied export/receipt material, foreign-pack access, and projection rebuild.

All 8 cases were exercised through all 4 retained routes: **32 route/case observations**. The unfiltered control was intentionally run over all fixture documents: **32/32 adversarial probes returned denied material as a candidate**, demonstrating that the boundary was tested against attractive lexical/semantic inputs rather than benign absence. The caller-filtered routes produced:

- unauthorized stable IDs: `0`
- unauthorized source/chunk text: `0`
- unauthorized citations: `0`
- reranker ACL promotions: `0`
- cross-pack results: `0`
- suppressed/redacted resurfacing: `0`
- projection-rebuild route leaks: `0`
- export/receipt leaks: `0`

The reranked route was pre-filtered, so the cross-encoder only received authorized candidates; the final candidate check remains defense in depth. The disposable projection retained suppressed material internally for the rebuild test, but no caller-scoped index or answer context received it.

## Answer, citation, receipt, and export checks

The existing lineage and untrusted-context wrappers received only caller-authorized candidates. Four receipts (one per retained route) validated against `fossil.query-execution-receipt.v1`. The receipt sidecar is `benchmarks/post-gate2/results/retrieval-security-final-receipts.jsonl` with SHA-256:

`d18744f61a8d338701a2db4ca083a6493a5392a6889e0daf93c21232d28c559e`

Receipts contain safe candidate/context/citation IDs and service metadata only; no protected source text, denied ID, denied citation, or secret-shaped source content was present.

## Verification counts

- Focused security plus impacted compatibility tests: **23 passed**.
- Full non-network suite: **709 passed, 1 skipped, 1 warning**. The warning is the existing legacy `dkg` deprecation warning for Issue #82.
- Security proof runner: **PASS**, 0 failures.
- Canonical semantics changed: **no**.
- Implementation change required: **yes**, a small caller-visibility policy/wrapper, fixture, runner, and regression-test addition; no canonical lifecycle/lineage change.

## Prior campaign decisions retained

The final embedding evidence remains unchanged: D021 and Qwen 0.6B had identical retrieval/answer quality; Qwen was materially slower/larger; therefore `RETAIN_D021`, with no 4B/8B escalation justified. The final routing evidence remains unchanged: fixed `RAW_RERANKED` hit/recall/MRR `1.000/1.000/0.873`, zero critical misses; deterministic router `0.952/0.937/0.867`, one critical miss; latency savings were insufficient; therefore `REJECT_ROUTING` and no LLM planner. Graphiti and contextual decisions were retained without rerun.

## Residual risks

This is a structural caller-boundary proof, not a claim that arbitrary natural-language model behavior is universally safe. Graphiti/Neo4j remains a rebuildable projection/inspection surface and is never granted canonical evidence authority. Any future new retrieval surface must be wrapped by the same visibility policy and repeat the adversarial boundary tests.

## Reproduction artifacts

- Fixture: `benchmarks/post-gate2/retrieval-security-fixtures-v1.json`
- Runner: `scripts/run_fossil_retrieval_security_final.py`
- Report: `benchmarks/post-gate2/results/retrieval-security-final-v1.json`
- Receipts: `benchmarks/post-gate2/results/retrieval-security-final-receipts.jsonl`
- Tests: `tests/test_retrieval_security_final.py`

This closes the substantive RAG campaign. No next RAG experiment is authorized.
