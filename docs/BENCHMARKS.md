# FOSSIL v1 benchmark evidence

This document summarizes the evidence that closed the post-Gate-2 retrieval/RAG campaign. It records **what was actually tested**, the resulting decisions, and important scope limits so later work does not repeatedly reopen settled questions or over-generalize a bounded experiment.

The accepted campaign is tracked in closed Issues [#47](https://github.com/Pukujan/fossil-core/issues/47) and [#48](https://github.com/Pukujan/fossil-core/issues/48), with execution receipts coordinated through [#94](https://github.com/Pukujan/fossil-core/issues/94).

The final evidence was produced on dedicated benchmark branches after the current runtime `main` baseline. Those branches remain immutable evidence sources until a separate v1 consolidation reconciles the accepted artifacts into `main`.

## Frozen benchmark shape

The final retrieval experiments reused the same compact corpus/evaluation shape wherever applicable:

- **27 documents**;
- **51 durable events**;
- **21 retrieval cases**;
- **6 answer cases**;
- retrieval limit **5**;
- exact pack revisions and stable IDs;
- deterministic lifecycle/lineage resolution;
- exact citation evaluation;
- pack isolation/security invariants;
- query-execution receipt validation.

This is intentionally a bounded decision benchmark, not a claim that the metrics predict every future corpus or workload.

## Final decision table

| Question | Key evidence | Decision |
| --- | --- | --- |
| Graphiti graph route vs retained reranked route | Graph hit/recall/MRR `0.048/0.016/0.024`; reranked `1.000/1.000/0.873` | **RETAIN_PROJECTION_NOT_RETRIEVAL** |
| Raw vs contextualized source projection | contextual improved 2 cases, regressed 4, answer quality unchanged, much slower | **RETAIN_RAW** |
| D021 vs Qwen3-Embedding 0.6B | identical quality; Qwen materially slower and larger | **RETAIN_D021**, **STOP_MODEL_LADDER** |
| Fixed policy vs deterministic router | router introduced one critical miss for small latency savings | **REJECT_ROUTING** |
| Final ACL/redaction boundary | 32 adversarial route cases, zero leaks | **PASS** |

## 1. Graphiti / GraphRAG decision benchmark

Evidence branch: `task/fossil-graphrag-bench-01`  
Final evidence commit: `f872f04f414943b2c5a70436e52e43a95f992dab`

The benchmark compared the existing FOSSIL Graphiti retrieval route with the retained reranked retrieval path.

| Metric | Graphiti route | Reranked route |
| --- | ---: | ---: |
| Hit rate | `0.048` | `1.000` |
| Recall | `0.016` | `1.000` |
| MRR | `0.024` | `0.873` |
| Answer correctness | `0.333` | `0.833` |
| Citation correctness | `0.333` | `1.000` |
| Retrieval p95 | `75.55 ms` | `214.75 ms` |
| Query classes with graph quality win | `0` | baseline winner |

### Decision

**`RETAIN_PROJECTION_NOT_RETRIEVAL`**

Graphiti/Neo4j remains useful as a rebuildable relationship/inspection projection, but the tested graph route is not the normal v1 retrieval path.

### Critical scope caveat

This was **not a benchmark of every possible GraphRAG architecture**. The bounded graph candidate was effectively:

```text
BM25 anchor
    -> bounded graph BFS expansion
    -> RRF
    -> canonical FOSSIL mapping/resolution
```

The benchmark recorded no retrieval model calls for the graph route. It did not implement a modern semantic-seed + typed/relevance-aware traversal + graph/text fusion + cross-encoder pipeline.

The result answers:

> Can the existing FOSSIL Graphiti projection cheaply earn a place as the normal retrieval path on this corpus?

Answer: **no**.

It does not answer:

> Are knowledge graphs or future graph-assisted retrieval universally bad?

A future corpus with many contracts, amendments, entities, obligations, ownership relationships, dependencies, or genuine multi-hop questions could justify a new graph-assisted design. That would be a new benchmark with a new architecture, not a reinterpretation of this result.

## 2. Contextual retrieval benchmark

Evidence branch: `task/fossil-contextual-retrieval-bench-01`  
Final head: `f2032266485af311452d2acfaabc456bf8cd184f`

The experiment compared the retained raw representation with an auditable deterministic source-only contextual projection while keeping the surrounding retrieval/evaluation conditions matched.

| Metric | RAW reranked | Contextual reranked |
| --- | ---: | ---: |
| Hit rate | `1.000` | `1.000` |
| Recall | `1.000` | `0.984` |
| MRR | `0.873` | `0.889` |
| Answer correctness | `0.833` | `0.833` |
| Citation correctness | `1.000` | `1.000` |
| Unsupported-claim rate | `0.167` | `0.167` |
| Retrieval p95 | `206.66 ms` | `569.43 ms` |
| Retrieval cases improved | — | `2` |
| Retrieval cases regressed | — | `4` |

All six answer cases were unchanged.

The contextual build produced 27 auditable records with no unsupported cross-pack/lifecycle override behavior, so the rejection was not simply caused by a broken provenance implementation.

### Decision

**`RETAIN_RAW`**

The contextual projection did not earn its added latency/complexity on the frozen corpus.

### Scope caveat

The tested contextualizer was deterministic and source-only. This is not a universal claim that every LLM-generated contextual-retrieval technique is ineffective. The v1 decision is narrower: **do not add another contextualization layer when the current raw representation already performs strongly and the matched candidate did not improve end-to-end answers.**

## 3. Final embedding benchmark: D021 vs Qwen3-Embedding 0.6B

Evidence branch: `codex/fossil-embedding-final-01-20260830`  
Final head: `36629238e2907601c85bc4716af512aeaae05470`  
Execution source: `e9639fab6bc2207a6aa7b484ca623af68d69128c`

Models:

- incumbent D021: `BAAI/bge-small-en-v1.5@5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`;
- challenger: `Qwen/Qwen3-Embedding-0.6B@97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`.

Everything else was frozen: raw representation, BM25, hybrid/RRF, candidate budgets, cross-encoder reranker, query/answer cases, packs, lifecycle/lineage rules, citations, and security invariants.

| Metric | D021 | Qwen3 0.6B |
| --- | ---: | ---: |
| Hit / recall@5 / MRR | `1.000 / 1.000 / 0.873` | `1.000 / 1.000 / 0.873` |
| Critical misses | `0` | `0` |
| Answer correctness | `0.833` | `0.833` |
| Citation correctness | `1.000` | `1.000` |
| Unsupported-claim rate | `0.167` | `0.167` |
| Retrieval p95 | `232.85 ms` | `510.03 ms` |
| Process RSS | about `140 MiB` | about `1066 MiB` |
| Embedding dimension | `384` | `1024` |

All **189 receipts** validated and the required lifecycle/lineage/citation/security/pack invariants passed. Full suite at closeout: **703 passed, 1 skipped**.

### Decision

**`RETAIN_D021`** and **`STOP_MODEL_LADDER`**.

Qwen3 0.6B produced no quality improvement while materially increasing latency and memory. The predefined escalation condition was therefore not met, so Qwen 4B/8B were not authorized merely to see whether a larger model might help.

## 4. Deterministic routing benchmark

Evidence branch: `codex-fossil-routing-final-01-20260830`  
Evidence lineage culminated before the final security branch.

The router used interpretable classes such as exact/identifier, conceptual, current/latest, lineage/history, broad synthesis, and direct-source read. Lifecycle and lineage authority remained deterministic and outside the router.

| Metric | Fixed RAW_RERANKED | Deterministic router |
| --- | ---: | ---: |
| Hit rate | `1.000` | `0.952` |
| Recall | `1.000` | `0.937` |
| MRR | `0.873` | `0.867` |
| Critical misses | `0` | `1` |
| Answer correctness | `0.833` | `0.833` |
| Citation correctness | `1.000` | `1.000` |
| Unsupported-claim rate | `0.167` | `0.167` |
| Retrieval p95 | `173.40 ms` | `164.34 ms` |

### Decision

**`REJECT_ROUTING`**.

About 9 ms of p95 savings did not justify a retrieval-quality regression and a decision-critical miss. No LLM planner/decomposition layer was authorized after the deterministic router failed to earn its complexity.

## 5. Final retrieval security benchmark

Evidence branch: `codex/fossil-retrieval-security-final-20260831`  
Security implementation/evidence commit: `7e69bdb955b74e7bfadb71b8ebf6cdbab77a295f`  
Final campaign-closeout head: `0da699e58df85beea14a3dbc8fc9a048a5893b6d`

The final security proof attacked all retained retrieval paths with deliberately attractive unauthorized/suppressed evidence.

Coverage included:

- lexical/BM25;
- D021 dense;
- hybrid/RRF;
- reranked hybrid;
- direct canonical read/citation boundaries where applicable;
- pack isolation;
- caller ACL;
- sensitivity restrictions;
- suppression/redaction;
- stale/superseded attractive evidence;
- projection/index residual material;
- citation construction;
- receipts/exports;
- projection rebuild followed by repeated authorization checks.

### Results

- **32 adversarial route cases**;
- **0 unauthorized stable-ID leaks**;
- **0 unauthorized text leaks**;
- **0 unauthorized citation leaks**;
- **0 cross-pack leaks**;
- **0 suppressed/redacted resurrection leaks**;
- **0 reranker promotions across the authorization boundary**;
- **0 projection/receipt/export leaks**;
- **23 focused tests passed**;
- **709 full-suite tests passed, 1 skipped**;
- **4 query-execution receipts validated**;
- receipt-file SHA-256: `d18744f61a8d338701a2db4ca083a6493a5392a6889e0daf93c21232d28c559e`.

### Decision

**`security_boundary = PASS`**

Security filtering determines what a caller may observe. It does not redefine lifecycle truth: current/superseded/retracted/disputed/history semantics remain canonical FOSSIL authority.

## Final retained v1 policy

```text
representation = RAW
embedding = D021 / BAAI bge-small-en-v1.5, revision pinned
lexical = BM25
fusion = deterministic hybrid / RRF
reranker = pinned cross-encoder
normal_policy = RAW_RERANKED
routing = REJECT_ROUTING
llm_planner = DISABLED
contextual_enrichment = not promoted
Graphiti = RETAIN_PROJECTION_NOT_RETRIEVAL
embedding_ladder = STOP_MODEL_LADDER
security_boundary = PASS
```

## What should trigger a new benchmark?

Do not reopen this campaign because a new RAG technique becomes fashionable. A new experiment should start only when there is a concrete new requirement or observed failure, for example:

- the corpus becomes orders of magnitude larger;
- document layout/multimodal content causes measured misses;
- a relationship-heavy corpus produces genuine multi-hop failures;
- the retained model becomes unavailable or operationally unsuitable;
- security/authorization topology changes materially;
- a new candidate has a plausible measured benefit large enough to change the v1 decision.

Until then, the purpose of these benchmarks is convergence: **use the simplest retained system that passed the evidence gates.**
