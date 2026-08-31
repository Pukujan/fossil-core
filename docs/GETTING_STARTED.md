# Getting started with FOSSIL

This guide gets you from a clean checkout to the first useful FOSSIL operations without requiring a public deployment or a running graph database.

FOSSIL requires **Python 3.11+**.

## 1. Install

From the repository root:

```bash
python -m venv .venv
# activate the virtual environment for your shell
python -m pip install -e .
```

Useful optional extras:

```bash
python -m pip install -e ".[test]"       # pytest + development dependencies
python -m pip install -e ".[semantic]"   # sentence-transformer retrieval
python -m pip install -e ".[node]"       # MCP node dependencies
python -m pip install -e ".[graphiti]"   # Graphiti + Neo4j projection
python -m pip install -e ".[s3]"         # S3-compatible durable storage
```

The package import is `fossil_core`. The old `dkg` package exists only as a deprecated compatibility shim.

## 2. Validate a knowledge pack

Knowledge packs are stable logical boundaries for evidence and knowledge. Start with the included common-pack fixture:

```python
from pathlib import Path
from fossil_core import KnowledgePackValidator

repo = Path(".")
validator = KnowledgePackValidator(repo / "schemas/knowledge-pack/v1.schema.json")
manifest = validator.load_and_validate(repo / "examples/packs/common/manifest.json")

print(manifest["pack_id"])
```

A pack's stable `pack_id` is semantic identity. Repository paths, Neo4j namespaces, vector-index IDs, and physical storage locations are not.

## 3. Store immutable evidence

The filesystem artifact store is the simplest durable adapter:

```python
from pathlib import Path
from fossil_core.adapters.filesystem import ArtifactStore

store = ArtifactStore(Path(".fossil/artifacts"))
manifest = store.put_bytes(
    b"This is the original observed evidence.\n",
    media_type="text/plain",
)

artifact_id = manifest["artifact_id"]
assert store.verify(artifact_id)
assert store.read_bytes(artifact_id) == b"This is the original observed evidence.\n"
print(artifact_id)
```

The artifact identity is content-addressed. Repeating the same write is safe and does not manufacture a second semantic identity.

Redaction is intentionally exceptional: FOSSIL records a durable tombstone before removing sensitive bytes, and the same redacted content identity cannot simply be republished as though nothing happened.

## 4. Understand the write path

Normal knowledge-changing work follows this shape:

```text
source evidence
    |
structured proposal
    |
schema + reference + pack/capability checks
    |
validate
    |
durable append-only event commit
    |
asynchronous rebuildable projections
```

Agents do not normally mutate Neo4j or another projection directly. A durable event exists independently of whether projection work succeeds immediately.

For application/agent workflows, use the `CorpusService` boundary through the supported node/MCP composition rather than teaching clients to write projection internals.

## 5. Run the deterministic suite

Install the test extra, then:

```bash
python -m pytest -q
```

Some optional/live integration workflows require their corresponding dependencies or external services. The repository also has separate Graphiti, S3-compatible service-fixture, mutation, TLA+, Lean, security, and engineering-assurance workflows.

## 6. Add semantic retrieval

Install:

```bash
python -m pip install -e ".[semantic]"
```

The retained v1 retrieval design is:

```text
RAW source representation
    -> BM25 + pinned D021 dense retrieval
    -> deterministic hybrid/RRF
    -> pinned cross-encoder reranker
    -> authorization/redaction filtering
    -> lifecycle/lineage resolution
    -> exact cited evidence
```

The embedding model is revision-pinned `BAAI/bge-small-en-v1.5`. Graphiti/Neo4j remains a rebuildable relationship projection rather than the normal retrieval route.

See [`BENCHMARKS.md`](BENCHMARKS.md) for the matched evidence that produced these decisions.

## 7. Compose a local FOSSIL node

A node combines canonical filesystem stores, one mounted pack, the `CorpusService`, reviewed ingestion, and a Graphiti projection worker.

With the Graphiti extra installed and Neo4j available through the environment contract (`NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`):

```python
from pathlib import Path

from fossil_core.agent import AgentContext
from fossil_core.runtime import FilesystemNodeConfig, compose_filesystem_node
from fossil_core.runtime.network import create_node_network_app

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
context = AgentContext(
    actor_id="local-operator",
    model_id="local-client",
    harness_version="manual-v1",
    skill_id="skill_corpus-search",
    skill_version="1.0.0",
)

app = create_node_network_app(node, context=context)
```

`app` is a Starlette ASGI application. The repository deliberately exposes an application factory rather than making a particular process manager or public hosting topology part of FOSSIL's semantic contract. Run it with an ASGI server appropriate to your environment.

The application surface includes:

- `/mcp` — MCP Streamable HTTP;
- `/healthz` — process/service liveness;
- `/readyz` — canonical + projection readiness;
- `/ingest` — reviewed evidence ingestion constrained to the mounted pack.

A projection outage may make `/readyz` return not-ready while canonical durable truth remains available. That distinction is intentional.

## 8. Use the MCP tools

The seven supported MCP tools are:

```text
fossil.search
fossil.read
fossil.lineage
fossil.propose
fossil.validate
fossil.commit
fossil.manage
```

The transport cannot widen the node-owned `AgentContext`, mounted pack, Skill, or capability authority. See [`USING_FOSSIL.md`](USING_FOSSIL.md) for tool semantics and common workflows.

## 9. Choose storage deliberately

For local use, the filesystem adapters are the reference implementation.

For remote/off-machine durability, FOSSIL also provides provider-neutral S3-compatible adapters:

```python
from fossil_core.adapters.s3 import S3ArtifactStore, S3DurableEventStore
```

The adapter contract has been exercised against a real disposable MinIO HTTP service, including zero-local-state rebuild and outage behavior. Activating R2, AWS S3, or another compatible provider is an operational deployment choice; it must not change FOSSIL semantic identity or authority.

## 10. Where to go next

- [`USING_FOSSIL.md`](USING_FOSSIL.md) — practical workflows and MCP surface.
- [`BENCHMARKS.md`](BENCHMARKS.md) — what was tested and why the v1 retrieval stack looks the way it does.
- [`SECURITY_MODEL.md`](SECURITY_MODEL.md) — authorization, redaction, untrusted context, and commit authority.
- [`architecture/public-api.md`](architecture/public-api.md) — exact supported Python import surfaces.
- [`../ARCHITECTURE.md`](../ARCHITECTURE.md) — durable architecture contract.
