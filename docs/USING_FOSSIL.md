# Using FOSSIL

FOSSIL can be used as a Python library, as a persistent local node, or through its MCP capability surface. In all three forms, the same semantic rule applies: **canonical evidence/events and their authorization rules are authoritative; indexes and projections are not.**

## The main concepts

### Evidence

Evidence is immutable observed source material stored content-addressably. Summaries, claims, graph nodes, and model outputs do not replace the source bytes they derive from.

### Knowledge events

Knowledge-changing operations are append-only events: a claim can be proposed, supported, disputed, superseded, retracted, or made stale without erasing the earlier state.

### Packs

A knowledge pack is a portable logical boundary with stable identity, dependencies/read mounts, and write scope. Pack identity is independent of repository path, database, graph namespace, or machine placement.

### Projections

Graphiti/Neo4j, lexical indexes, vector indexes, analytics tables, and exports are rebuildable representations of durable truth. They can fail or be destroyed without gaining authority over the canonical corpus.

## Supported Python surfaces

The compatibility-stable package-root API is documented in [`architecture/public-api.md`](architecture/public-api.md) and machine-checked by `contracts/python-public-api-v1.json`.

Typical imports include:

```python
from fossil_core import (
    ArtifactStore,
    DurableEventStore,
    KnowledgePackValidator,
    PackAccess,
    build_promotion_event,
)
```

New application code should prefer bounded canonical modules where one exists:

```python
from fossil_core.domain.lifecycle import KnowledgeState, LifecycleError
from fossil_core.domain.pack import PackAccess, PackBoundaryError
from fossil_core.ports import Retriever, Reranker, EmbeddingProvider
from fossil_core.adapters.filesystem import ArtifactStore, DurableEventStore
from fossil_core.adapters.s3 import S3ArtifactStore, S3DurableEventStore
```

`src/dkg` is only a deprecated compatibility shim. New code belongs under `fossil_core`.

## Common workflow: preserve source evidence

```python
from pathlib import Path
from fossil_core.adapters.filesystem import ArtifactStore

store = ArtifactStore(Path(".fossil/artifacts"))
manifest = store.put_file(Path("source.pdf"), media_type="application/pdf")

artifact_id = manifest["artifact_id"]
assert store.verify(artifact_id)
```

The returned artifact ID is derived from canonical source content. A database row ID, vector ID, or Neo4j UUID must not replace it.

For source snapshots and exact citations, use the source/citation contracts under `schemas/source-snapshot/` and `schemas/citation/`. Exact citation identity is tied to observed immutable source material and byte spans.

## Common workflow: packs and access

A pack manifest describes the pack's identity and boundaries. The included fixtures show the shape:

```text
examples/packs/common/manifest.json
examples/packs/plugin-harness/manifest.json
```

Validate a manifest before relying on it:

```python
from pathlib import Path
from fossil_core import KnowledgePackValidator

validator = KnowledgePackValidator(Path("schemas/knowledge-pack/v1.schema.json"))
manifest = validator.load_and_validate(Path("examples/packs/common/manifest.json"))
```

`PackAccess` enforces readable mounts and writable targets. Read access to shared knowledge is not automatically write authority. Cross-pack promotion is explicit and provenance-preserving.

## Common workflow: changing knowledge over time

FOSSIL does not overwrite an earlier claim just because a newer conclusion exists. Instead, lifecycle events preserve the sequence.

Conceptually:

```text
claim A proposed
    -> evidence attached
    -> claim A supported
    -> new evidence appears
    -> claim A superseded
    -> claim B supported
```

A query for current knowledge can resolve claim B while a historical/lineage query can still explain claim A, the evidence that supported it, and the event that superseded it.

This separation is why lifecycle/lineage resolution remains outside retrieval-model authority: a high similarity score cannot decide whether an item is current, historical, disputed, or superseded.

## The retained retrieval path

The completed post-Gate-2 campaign retained this fixed policy:

```text
RAW source representation
        |
BM25 + revision-pinned D021 dense
        |
deterministic hybrid / reciprocal-rank fusion
        |
pinned cross-encoder reranker
        |
caller authorization / redaction boundary
        |
FOSSIL lifecycle + lineage resolution
        |
exact cited evidence
```

The current evidence does **not** justify:

- Graphiti as the normal retrieval route;
- a contextualized source projection for the tested corpus;
- replacing D021 with Qwen3-Embedding 0.6B;
- escalating to Qwen 4B/8B;
- a deterministic adaptive router;
- an LLM retrieval planner.

See [`BENCHMARKS.md`](BENCHMARKS.md) for the measured results and scope limitations.

## Persistent node composition

The filesystem node separates canonical state from operational projection state:

```text
<data_root>/canonical/artifacts
<data_root>/canonical/sources
<data_root>/canonical/events
<data_root>/operational/projection-ledger
```

A minimal composition looks like:

```python
from pathlib import Path
from fossil_core.runtime import FilesystemNodeConfig, compose_filesystem_node

repo = Path(".").resolve()

config = FilesystemNodeConfig(
    repository_root=repo,
    data_root=repo / ".fossil-node",
    pack_manifest_path=repo / "examples/packs/common/manifest.json",
    projection_build_id="local-v1",
    projection_build_manifest={
        "graphiti_version": "0.29.3",
        "neo4j_version": "local",
        "model_id": "projection-runtime",
        "ontology_version": "1.0.0",
        "software_commit": "local-checkout",
    },
)

node = compose_filesystem_node(config)
```

Without an injected test client, Graphiti is created from `NEO4J_URI`, `NEO4J_USER`, and `NEO4J_PASSWORD`.

A projection failure does not roll back an already accepted durable event. The projector is restartable and catches up from canonical events.

## Network/MCP application

Create the node-owned agent context and ASGI application:

```python
from fossil_core.agent import AgentContext
from fossil_core.runtime.network import create_node_network_app

context = AgentContext(
    actor_id="local-operator",
    model_id="local-client",
    harness_version="manual-v1",
    skill_id="skill_corpus-search",
    skill_version="1.0.0",
)

app = create_node_network_app(node, context=context)
```

The app provides:

| Route | Purpose |
| --- | --- |
| `/mcp` | MCP Streamable HTTP capability surface |
| `/healthz` | liveness |
| `/readyz` | canonical + projection readiness |
| `/ingest` | reviewed evidence ingest into the mounted writable pack |

`/readyz` distinguishes canonical durability from projection readiness. If Neo4j/Graphiti is unavailable, FOSSIL can report that the node is not ready for projection-backed operation while durable truth remains available.

The application factory returns a Starlette ASGI application. Process management, reverse proxying, and public hosting are deployment concerns rather than semantic FOSSIL APIs.

## MCP tools

The MCP surface is intentionally frozen to seven tools.

### `fossil.search`

Search readable packs through the canonical corpus service.

Inputs: `query`, optional `limit`.

Search results are evidence/corpus candidates. Ranking does not create truth authority.

### `fossil.read`

Read one durable event by `event_id` when its pack is mounted for the caller.

### `fossil.lineage`

Read conversation/intellectual lineage using `conversation_id` and an optional `node_id`.

### `fossil.propose`

Prepare a structured durable knowledge event. Required inputs include event type, pack, subject references, payload, times, and idempotency key. Proposal does not by itself mean committed truth.

### `fossil.validate`

Validate a prepared event without committing it.

### `fossil.commit`

Commit an event through the canonical writer after the caller's pack/capability and semantic checks succeed.

### `fossil.manage`

Read the bounded management surface, such as supported capabilities/health. It is not an arbitrary admin shell.

The MCP transport never exposes arbitrary graph mutation, shell execution, or general filesystem access.

## Reviewed ingestion

The HTTP `/ingest` route is designed for reviewed evidence, not arbitrary model-authored memory injection. The node itself owns:

- mounted pack identity;
- pack write authority;
- durable actor identity;
- AgentContext;
- canonical stores.

A request cannot supply a different pack manifest or durable actor in order to widen authority.

The ingest flow stores evidence/source material and a durable proposed event before projection. Failure to update Graphiti does not convert the operation into false durable success or erase the canonical event.

## Filesystem vs S3-compatible storage

Use the filesystem adapters for simple local deployments:

```python
from fossil_core.adapters.filesystem import ArtifactStore, DurableEventStore
```

Use the S3-compatible adapters when you need a remote/off-machine canonical object store:

```python
from fossil_core.adapters.s3 import S3ArtifactStore, S3DurableEventStore
```

Both implement the same core semantics: immutable/idempotent writes, loud stable-identity conflicts, integrity verification, redaction/non-resurrection behavior, and rebuildable canonical events.

The S3-compatible path has been tested against a disposable real MinIO HTTP service. No permanent R2/AWS-provider semantics are part of the domain model.

## Graphiti / Neo4j

Graphiti is useful in FOSSIL for:

- relationship projection;
- temporal/lineage inspection;
- entity/fact exploration;
- rebuild/migration proof;
- future relationship-heavy workloads.

The current benchmark rejected the existing Graphiti projection as the normal retrieval index. That decision should not be generalized to "knowledge graphs never help." The tested graph route was a bounded `BM25 + BFS + RRF` retrieval architecture over a small corpus and performed poorly relative to the retained reranked hybrid path.

A future large multi-document corpus with explicit relationships, amendments, ownership, obligations, dependencies, or true multi-hop questions could justify a different graph-assisted retrieval experiment. That would be a new evidence-backed architecture decision, not a reason to reopen the completed v1 campaign now.

## Authorization and redaction

Authorization is not a final-answer-only filter. The retained security design constrains candidates, context, citations, receipts, projections, and exports so denied/suppressed information cannot simply reappear through another route.

Read [`SECURITY_MODEL.md`](SECURITY_MODEL.md) before exposing FOSSIL to multiple users, external agents, or remote ingress.

## Operational boundaries

FOSSIL's core does not require:

- public Internet ingress;
- a ChatGPT-specific Action/OpenAPI adapter;
- a specific reverse proxy or tunnel;
- Kubernetes, Kafka, Redis, or a service mesh;
- Cortex or LiteLLM as semantic authority.

A public bearer-authenticated MCP edge has been explored separately, but public deployment is not part of the local v1 semantic contract. Do not interpret transport deployment work as permission to weaken the existing pack/Skill/capability boundary.

## Troubleshooting principles

When something fails, first classify which layer failed:

```text
canonical evidence/event store
projection/index
retrieval/model service
MCP/HTTP transport
external deployment/host
```

A projection outage is not canonical data loss. A transport 2xx with empty or malformed semantic output is not success. A model or reranker result is not durable knowledge until the normal FOSSIL validation/commit rules are satisfied.
