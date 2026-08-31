# FOSSIL-ROUTING-FINAL-01 — deterministic routing decision

**Date:** 2026-08-31  
**Decision:** `REJECT_ROUTING`  
**Authority:** evidence-only retrieval policy evaluation; durable FOSSIL evidence, lifecycle/lineage, citation, pack, redaction, and security semantics remain authoritative.

## Scope

This final routing gate compared the retained fixed `RAW_RERANKED` policy against a small deterministic lexical query classifier. The router selected only among the already retained BM25, D021 dense, hybrid/RRF, lifecycle-aware, and pinned cross-encoder routes. It used no LLM planner, new embedding model, Graphiti retrieval, contextual enrichment, provider gateway, or canonical-semantic change.

The frozen inputs were 27 projected documents, 51 durable events, 21 retrieval cases, and 6 answer cases. Exact pack revisions were common `d583005dce06dbb499c3c0de5c22b899655eb8d2` and AI systems `84accd2ee895663990e82ca5b79b592cb503db24`.

The six deterministic classes and route map were:

| Query class | Selected route |
| --- | --- |
| exact-identifier | D021 dense + cross-encoder |
| conceptual | D021 dense + cross-encoder |
| current-latest | hybrid + lifecycle reranker |
| lineage-history | hybrid + lifecycle reranker |
| broad-synthesis | fixed RAW reranked |
| direct-source-read | BM25 |

## Matched result

| Metric | Fixed `RAW_RERANKED` | Deterministic router |
| --- | ---: | ---: |
| Hit rate | 1.000 | 0.952 |
| Recall@5 | 1.000 | 0.937 |
| MRR | 0.873 | 0.867 |
| Decision-critical misses | 0 | 1 |
| Current-top-1 superseded leakage | 0 | 0 |
| Answer correctness | 0.833 | 0.833 |
| Citation correctness | 1.000 | 1.000 |
| Unsupported claim rate | 0.167 | 0.167 |
| Retrieval policy p95 | 173.40 ms | 164.34 ms |
| Answer total p95 | 189.48 ms | 166.65 ms |

The router's approximately 5.2% retrieval p95 reduction did not meet the predeclared 10% material-latency threshold and came with a decision-critical miss on `current_architecture_after_reconsideration`. Therefore the router is not promoted. The fixed `RAW_RERANKED` policy remains the normal retrieval policy, and no LLM planner should be built.

## Controls and replay evidence

- Pack isolation violations: 0 for both policies.
- Current-top-1 superseded leakage: 0 for both policies.
- All answer/citation/security controls remained matched and passed.
- 12 query-execution receipts were emitted and all validated against `fossil.query-execution-receipt.v1`.
- Receipt sidecar SHA-256: `bfb15c7673d371240fe1d254d491d5207ea36a1f91437840a43355abd9f733df`.
- `acceptance_weakened=false`; Graphiti remains projection-only; contextual enrichment and Qwen model escalation remain rejected/stopped.

## Reproduction artifacts

- Plan: `benchmarks/post-gate2/routing-final-v1.json`
- Runner: `scripts/run_fossil_routing_final.py`
- Report: `benchmarks/post-gate2/results/2026-08-31-routing-final/report.json`
- Receipts: `benchmarks/post-gate2/results/2026-08-31-routing-final/receipts.jsonl`

The next and final RAG campaign task is `FOSSIL-RETRIEVAL-SECURITY-FINAL-01`.
