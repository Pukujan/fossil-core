# FOSSIL project state

**Project:** FOSSIL — Fault-tolerant Open Semantic Store for Intellectual Lineage  
**Durable substrate:** DICS — Durable Intellectual Corpus System  
**Architecture authority:** Issue #86  
**Execution/claim ledger:** Issue #94  
**Last reconciled:** 2026-08-31

## Current state

FOSSIL's core semantic and retrieval architecture is now substantially settled.

The governing invariant remains:

> **Compute may disappear; truth must not.**

Canonical FOSSIL knowledge is:

- immutable evidence/source snapshots;
- stable corpus-owned IDs;
- append-only validated knowledge events;
- versioned pack/ontology/contracts;
- provenance and exact citations;
- lifecycle and lineage history;
- explicit authorization/redaction semantics.

Graphiti/Neo4j, lexical/vector indexes, embedding/reranking models, MCP/HTTP transports, machines, observability systems, Cortex, LiteLLM, and future databases remain replaceable infrastructure/projections.

## v1 capability status

### Durable evidence and knowledge semantics — complete

FOSSIL has implemented/tested:

- content-addressed immutable artifacts;
- source snapshots and exact citations;
- deterministic event identity/idempotency;
- append-only event history;
- claim/relation lifecycle and temporal replay;
- disagreement/supersession/retraction/staleness;
- knowledge-pack read/write boundaries;
- provenance-preserving promotion;
- proposal -> validate -> commit authority;
- exceptional redaction with tombstone-before-delete and non-resurrection.

### Projection/rebuild — complete for the current architecture

The Graphiti/Neo4j projection is explicitly noncanonical and rebuildable.

Evidence includes:

- real Graphiti + Neo4j materialization;
- idempotent projection ledger behavior;
- projection failure without durable-event rollback;
- destructive rebuild with fresh build identity;
- semantic reconstruction from durable events;
- redaction purge + fresh-rebuild non-resurrection.

### Persistent node / MCP boundary — implemented on main

The merged node runtime provides:

- filesystem canonical stores separated from operational projection state;
- restartable projector worker;
- `CorpusService` composition;
- reviewed ingestion;
- MCP Streamable HTTP `/mcp`;
- `/healthz` and `/readyz`;
- the frozen seven-tool MCP surface:
  - `fossil.search`
  - `fossil.read`
  - `fossil.lineage`
  - `fossil.propose`
  - `fossil.validate`
  - `fossil.commit`
  - `fossil.manage`.

Public Internet ingress/bearer deployment is a separate operational lane and is not required for local v1 core completion.

### S3-compatible storage — contract and real-service fixture complete

FOSSIL has provider-neutral S3-compatible artifact/event adapters with the same semantic contract as the filesystem path.

A real disposable MinIO HTTP service proof passed:

- immutable creation and byte-identical replay;
- loud stable-key conflicts;
- corruption detection;
- redaction tombstone-before-delete;
- non-republication/non-resurrection;
- fresh-client restart;
- zero-local-state rebuild;
- fail-closed endpoint outage.

A real-cloud R2/S3 activation remains an optional operational durability decision unless the owner explicitly makes off-machine provider proof a v1 release requirement. Provider selection is not semantic architecture truth.

## Post-Gate-2 retrieval campaign — complete

Issues #47 and #48 are closed completed.

The final retained retrieval path is:

```text
RAW canonical source representation
        -> BM25 + revision-pinned D021 dense
        -> deterministic hybrid/RRF
        -> pinned cross-encoder reranker
        -> caller ACL/redaction boundary
        -> lifecycle/lineage resolution
        -> exact cited evidence
```

Final decisions:

```text
representation = RETAIN_RAW
embedding = RETAIN_D021
embedding_ladder = STOP_MODEL_LADDER
Graphiti retrieval = RETAIN_PROJECTION_NOT_RETRIEVAL
routing = REJECT_ROUTING
LLM planner = DISABLED / not justified
security boundary = PASS
```

### Graph retrieval

Current Graphiti route vs reranked route:

- hit/recall/MRR: `0.048/0.016/0.024` vs `1.000/1.000/0.873`;
- answer/citation correctness: `0.333/0.333` vs `0.833/1.000`;
- no query class produced a graph-quality win.

Decision: Graphiti stays a relationship/inspection/rebuild projection, not the normal retrieval route.

### Contextual representation

Raw vs deterministic source-contextualized representation:

- raw `1.000/1.000/0.873` hit/recall/MRR;
- contextual `1.000/0.984/0.889`;
- answer/citation unchanged `0.833/1.000`;
- contextual improved 2 retrieval cases and regressed 4;
- p95 increased from `206.66 ms` to `569.43 ms`.

Decision: retain raw.

### Final embedding decision

D021 vs Qwen3-Embedding 0.6B:

- identical retrieval quality: `1.000/1.000/0.873`;
- identical answer/citation correctness: `0.833/1.000`;
- D021 p95 `232.85 ms` vs Qwen `510.03 ms`;
- D021 RSS about `140 MiB` vs Qwen about `1066 MiB`.

Decision: `RETAIN_D021`; no Qwen 4B/8B escalation.

### Routing decision

Fixed RAW_RERANKED vs deterministic six-class router:

- fixed `1.000/1.000/0.873`, critical misses `0`;
- router `0.952/0.937/0.867`, critical misses `1`;
- latency improvement was only about 9 ms p95.

Decision: reject routing and retain the simple fixed policy.

### Final security proof

The final retrieval security closeout passed:

- 32 adversarial route cases;
- zero unauthorized IDs/text/citations;
- zero cross-pack leaks;
- zero suppression/redaction resurrection;
- zero reranker/projection/receipt/export boundary leaks;
- 23 focused tests passed;
- full non-network suite: 709 passed, 1 skipped;
- four valid query-execution receipts.

See [`BENCHMARKS.md`](BENCHMARKS.md) and [`SECURITY_MODEL.md`](SECURITY_MODEL.md).

## Important branch/main reconciliation fact

At the start of the 2026-08-31 documentation task, default `main` was still:

`c8217bbd978b7a3005f94fcb772d86568ea33d11` — NODE-02 MCP/HTTP network boundary.

The accepted final retrieval evidence was produced later on dedicated branches:

- `task/fossil-graphrag-bench-01`;
- `task/fossil-contextual-retrieval-bench-01`;
- `codex/fossil-embedding-final-01-20260830`;
- `codex-fossil-routing-final-01-20260830`;
- `codex/fossil-retrieval-security-final-20260831`.

The campaign issues are closed, but those accepted evidence artifacts are not automatically assumed to be merged into `main` merely because the decisions were accepted.

**The next repository-finalization task is a bounded v1 consolidation/reconciliation, not another RAG experiment.** It should deliberately bring the accepted evidence/tests/decision records onto the current main lineage without blindly merging independent benchmark branches.

## What is actually left for “FOSSIL v1 finished”

### Required repository closeout

1. Merge/reconcile the current v1 documentation update.
2. Run a bounded `FOSSIL-V1-CONSOLIDATION` from live `main`:
   - inventory accepted benchmark/security artifacts;
   - reconcile only the intended evidence/tests/decision records;
   - preserve current runtime semantics;
   - run exact-head full CI/assurance;
   - update/close stale issue/PR state where mechanically justified.
3. Record the final v1 release boundary and current handoff.

### Owner decision: off-machine durability

Decide explicitly whether v1 requires a real-cloud S3/R2 proof.

If yes, run the existing live object-store gate with narrowly scoped credentials and zero-local-state recovery.

If no, record remote-provider activation as deferred operational work because provider-neutral storage plus real S3-compatible service behavior is already proven.

### Not v1 blockers by default

The following should not keep the core project permanently “unfinished” unless the owner explicitly promotes them to release requirements:

- new embedding/reranker sweeps;
- new GraphRAG architecture;
- contextual retrieval retesting;
- LLM query planning;
- public Internet MCP hosting;
- ChatGPT-specific Action/OpenAPI integration;
- dashboards/control rooms;
- Kubernetes/Kafka/Redis;
- full completion of every optional PDD/formal-methods research lane;
- unrelated corpus-ingestion PRs.

## Current documentation

Use these instead of stale issue prose when learning the current system:

- [`../README.md`](../README.md) — what FOSSIL does and why;
- [`GETTING_STARTED.md`](GETTING_STARTED.md) — install and first operations;
- [`USING_FOSSIL.md`](USING_FOSSIL.md) — practical usage and MCP surface;
- [`BENCHMARKS.md`](BENCHMARKS.md) — matched evidence and decisions;
- [`SECURITY_MODEL.md`](SECURITY_MODEL.md) — security/authorization boundary;
- [`../ARCHITECTURE.md`](../ARCHITECTURE.md) — durable architecture contract;
- [`architecture/public-api.md`](architecture/public-api.md) — versioned Python public API;
- [`HANDOFF_CURRENT.md`](HANDOFF_CURRENT.md) — current implementation continuation.

## Coordination rule

Before any mutating work, read live `main` plus Issue #94 and claim the bounded task. Live GitHub state overrides any stale embedded SHA or old tracker body.

Do not manufacture a new task merely because an old umbrella issue still has unchecked boxes. Reconcile it against current code/evidence first.
