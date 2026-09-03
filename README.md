# FOSSIL

**Fault-tolerant Open Semantic Store for Intellectual Lineage**

FOSSIL is durable decision and intellectual-lineage infrastructure for human-led technical work.

A programmer, architect, or researcher can spend days reading sources, testing alternatives, running benchmarks, and using AI to help search, compare, code, critique, and summarize. The human still guides the work and makes the consequential decision.

Months later, the raw material usually still exists in transcripts, tickets, commits, benchmark output, source documents, notes, and review comments. What is often missing is the reasoning chain that connected those artifacts.

FOSSIL preserves that chain so a project can later answer:

- Why did we make this decision?
- Which sources and tests mattered?
- What did we try before?
- What changed our mind?
- What was believed then versus now?
- Which later conclusions depend on an old premise?

An AI can search old artifacts and reconstruct a plausible explanation. FOSSIL is built for a different standard: **preserve the captured evidence and recorded reasoning history so the answer can be inspected instead of invented from fragments at query time.**

> **Compute may disappear; truth must not.**

FOSSIL keeps original evidence, stable identities, provenance, lifecycle history, citations, pack authorization, and accepted knowledge events durable. Search indexes, graph databases, embedding models, rerankers, LLMs, runtimes, and machines are replaceable projections around that durable corpus.

The durable substrate is **DICS, Durable Intellectual Corpus System**.

For the canonical product intent and problem framing, see [`docs/PROBLEM_STATEMENT.md`](docs/PROBLEM_STATEMENT.md).

## What problem does FOSSIL solve?

Modern technical work saves a lot of artifacts, but those artifacts do not automatically preserve what the work established.

A transcript can show what was said. Git can show how code changed. Jira can show the work item. An ADR can record the final decision. Search or AI memory can retrieve useful prior context. None of those, by itself, guarantees a faithful history of why a position became justified, what evidence supported it, what alternatives failed, or how that position changed later.

That creates a practical problem months into a project. You may still know **what** the system does, but not be able to reliably answer **why** it ended up that way without reconstructing the story from scattered fragments.

FOSSIL is designed for that gap. It provides:

- **durable evidence**: immutable source bytes and content-addressed identities;
- **exact provenance and citations**: conclusions can resolve back to observed source material;
- **history instead of overwrite**: proposals, support, disputes, retractions, supersession, and stale state remain reconstructable;
- **current and historical lineage**: a project can distinguish what was believed then from what is believed now;
- **portable knowledge packs**: stable logical boundaries with explicit read/write authority;
- **proposal-before-commit semantics**: humans or assistants can propose; deterministic validation and policy gates own durable writes;
- **replaceable retrieval and graph infrastructure**: losing Neo4j, Graphiti, a vector index, or an embedding model does not redefine canonical truth;
- **redaction and non-resurrection**: exceptional erasure is explicit and projections or rebuilds must continue to respect it;
- **reproducible retrieval evidence**: benchmark runs record exact models, routes, packs, receipts, latency, and failure behavior.

FOSSIL is therefore not primarily a vector database, a knowledge graph, or an AI memory system. It is the **durable semantic and evidence layer that preserves how project knowledge changed over time**.

A useful shorthand is:

> Your transcripts remember what was said. FOSSIL preserves what the work established.

For software projects:

> Git tells you how the code changed. FOSSIL helps explain why the project's thinking changed.

## Architecture in one picture

```text
                    humans + AI assistants
                             |
                  proposal + query boundary
                             |
                pack / capability authorization
                             |
                    FOSSIL CorpusService
                             |
         +-------------------+-------------------+
         |                                       |
  CANONICAL DURABLE TRUTH                  REBUILDABLE VIEWS
         |                                       |
 immutable evidence                         BM25 / dense index
 stable corpus IDs                         hybrid / reranker
 append-only events                        Graphiti / Neo4j
 provenance + citations                    exports / analytics
 lifecycle + lineage                       future projections
 pack contracts
         |
 filesystem or S3-compatible storage
```

The core rule is simple: **a projection may help find or inspect knowledge; it may not become the authority that defines the knowledge.**

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the full contract.

## Current v1 retrieval policy

The completed post-Gate-2 campaign converged on a deliberately simple policy:

```text
RAW canonical source material
        |
        +--> BM25
        |
        +--> pinned D021 dense retrieval
                 |
           hybrid / RRF
                 |
       pinned cross-encoder reranker
                 |
          ACL / redaction boundary
                 |
        lifecycle / lineage resolution
                 |
          exact cited evidence
```

Current decisions:

| Question | Evidence-backed decision |
| --- | --- |
| Raw vs contextual representation | **RETAIN_RAW** |
| Embedding model | **RETAIN_D021** (`BAAI/bge-small-en-v1.5`, revision-pinned) |
| Qwen 4B/8B model ladder | **STOP_MODEL_LADDER** |
| Graphiti as normal retriever | **RETAIN_PROJECTION_NOT_RETRIEVAL** |
| Deterministic query router | **REJECT_ROUTING**; keep the fixed reranked policy |
| LLM retrieval planner | not justified / disabled |
| Retrieval ACL/redaction boundary | **PASS** |

The accepted benchmark evidence lives on the completed post-Gate-2 evidence branches and closed Issues [#47](https://github.com/Pukujan/fossil-core/issues/47) and [#48](https://github.com/Pukujan/fossil-core/issues/48). A separate v1 consolidation step is responsible for reconciling those accepted evidence artifacts into `main`; this documentation update does not alter runtime semantics.

## What has been tested?

A few headline results from the final matched campaign:

| Experiment | Result | Decision |
| --- | --- | --- |
| Graphiti graph retrieval vs reranked retrieval | hit rate `0.048` vs `1.000`; answer correctness `0.333` vs `0.833` | graph remains a projection, not the normal retrieval path |
| Contextual projection vs raw | same answer quality; contextual improved 2 retrieval cases and regressed 4; materially slower | retain raw |
| Qwen3-Embedding 0.6B vs D021 | identical `1.000/1.000/0.873` hit/recall/MRR; Qwen retrieval p95 `510.03 ms` vs `232.85 ms`, RSS `1066 MiB` vs `140 MiB` | retain D021; stop ladder |
| Deterministic router vs fixed reranked policy | router introduced one critical miss for ~9 ms p95 savings | reject routing |
| Final retrieval security | 32 adversarial route cases, **zero leaks** | pass |
| Final non-network suite during security closeout | `709 passed, 1 skipped` | pass |

See [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md) for scope, caveats, exact decisions, and evidence refs. The graph experiment tested the existing bounded Graphiti `BM25 + BFS + RRF` route; it was **not** a claim that all knowledge graphs or all GraphRAG architectures perform poorly.

## Install

FOSSIL requires Python 3.11+.

```bash
python -m venv .venv
# activate the environment for your shell
python -m pip install -e .
```

Install only the optional capabilities you need:

```bash
# development/test suite
python -m pip install -e ".[test]"

# local sentence-transformer retrieval
python -m pip install -e ".[semantic]"

# MCP node surface
python -m pip install -e ".[node]"

# Graphiti / Neo4j projection
python -m pip install -e ".[graphiti]"

# S3-compatible durable storage adapter
python -m pip install -e ".[s3]"
```

For a development node using the tested semantic and MCP surfaces together:

```bash
python -m pip install -e ".[test,semantic,node,graphiti]"
```

## Five-minute start

The quickest way to understand the durable core is to validate a pack and write content-addressed evidence:

```python
from pathlib import Path

from fossil_core import KnowledgePackValidator
from fossil_core.adapters.filesystem import ArtifactStore

repo = Path(".")

validator = KnowledgePackValidator(repo / "schemas/knowledge-pack/v1.schema.json")
pack = validator.load_and_validate(repo / "examples/packs/common/manifest.json")
print(pack["pack_id"])

store = ArtifactStore(repo / ".fossil" / "artifacts")
manifest = store.put_bytes(
    b"Original evidence remains the source of truth.\n",
    media_type="text/plain",
)
assert store.verify(manifest["artifact_id"])
print(manifest)
```

That demonstrates two important FOSSIL properties: the pack boundary is validated from a versioned contract, and the evidence identity is derived from immutable source bytes rather than a database row or vector ID.

For a practical walkthrough of durable events, packs, retrieval, node composition, and MCP tools, continue with:

- [`docs/GETTING_STARTED.md`](docs/GETTING_STARTED.md)
- [`docs/USING_FOSSIL.md`](docs/USING_FOSSIL.md)
- [`docs/SECURITY_MODEL.md`](docs/SECURITY_MODEL.md)

## MCP and assistant surface

FOSSIL includes a real MCP Streamable HTTP server. The frozen tool surface is:

- `fossil.search`
- `fossil.read`
- `fossil.lineage`
- `fossil.propose`
- `fossil.validate`
- `fossil.commit`
- `fossil.manage`

The MCP transport delegates to the same `CorpusService` and pack/capability boundary; it is not a second knowledge model and does not expose arbitrary Neo4j, shell, or filesystem mutation.

The merged node runtime provides `/mcp`, `/healthz`, `/readyz`, and reviewed `/ingest` composition. **Public Internet deployment and bearer-authenticated ingress are a separate operational lane and are not required to understand or use the local v1 core.**

See [`docs/USING_FOSSIL.md`](docs/USING_FOSSIL.md) for node composition and MCP usage.

## Storage and rebuildability

The canonical storage contract has filesystem and provider-neutral S3-compatible adapters. The S3-compatible implementation has been exercised against a real disposable MinIO HTTP service for immutable/idempotent writes, conflicts, corruption detection, redaction tombstone-before-delete, non-republication, restart, zero-local-state rebuild, and fail-closed endpoint outage.

A real-cloud R2/S3 activation is an operational durability decision, not a change to FOSSIL's semantic model. Provider-specific behavior must not leak into stable IDs, hashes, events, provenance, lifecycle, redaction, or rebuild semantics.

## Knowledge packs

A **knowledge pack** is a logical portable unit, not a database shard. Packs let one project or assistant read shared knowledge without automatically acquiring permission to modify it. Stable `pack_id` identity survives repository movement, projection rebuilds, and physical storage changes.

Examples in this repository include:

- `fossil-common`: shared research/engineering methods;
- `fossil-ai-systems`: AI-systems knowledge that can read shared common knowledge;
- project/personal packs: narrower evidence and proposals with explicit promotion into broader packs.

Cross-pack promotion creates new durable lineage; it does not mutate the source pack into a different history.

## What FOSSIL deliberately does not do

- It does not let vector similarity or graph proximity define truth.
- It does not treat model agreement as evidence.
- It does not silently overwrite history when a claim changes.
- It does not require Graphiti/Neo4j to preserve canonical knowledge.
- It does not give assistants arbitrary database mutation authority.
- It does not automatically promote retrieved text into executable policy.
- It does not require a large-model routing/planning layer when a fixed policy is empirically stronger.
- It does not claim to recover reasoning that was never captured.

## Documentation map

- [`docs/PROBLEM_STATEMENT.md`](docs/PROBLEM_STATEMENT.md): canonical product intent and human-led problem framing.
- [`docs/GETTING_STARTED.md`](docs/GETTING_STARTED.md): install and first durable operations.
- [`docs/USING_FOSSIL.md`](docs/USING_FOSSIL.md): packs, evidence, events, node composition, MCP tools, and common workflows.
- [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md): retrieval/model/GraphRAG decisions and measured evidence.
- [`docs/SECURITY_MODEL.md`](docs/SECURITY_MODEL.md): pack isolation, ACL/redaction, untrusted context, proposal/commit authority.
- [`ARCHITECTURE.md`](ARCHITECTURE.md): durable architecture contract and non-goals.
- [`docs/architecture/public-api.md`](docs/architecture/public-api.md): versioned Python public API.
- [`docs/PROJECT_STATE.md`](docs/PROJECT_STATE.md): current v1 state and remaining consolidation/operational work.
- [`docs/HANDOFF_CURRENT.md`](docs/HANDOFF_CURRENT.md): current continuation point for implementation agents.
- [`docs/DECISION_LOG.md`](docs/DECISION_LOG.md): durable architectural decisions.

## License and rights

**FOSSIL is proprietary software and is not open source.**

Copyright © 2026 Pukujan. **All rights reserved.** No license is granted to use, copy, modify, distribute, sublicense, sell, host, deploy, or create derivative works from this repository or its original contents, in whole or in part, without prior express written permission from the copyright holder.

Public visibility of this repository does not grant additional permission to use the code. Third-party software, dependencies, data, and other materials remain subject to their respective licenses and rights holders.

See [`LICENSE`](LICENSE) for the repository's proprietary rights notice.
