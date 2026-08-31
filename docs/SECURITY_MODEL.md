# FOSSIL security model

FOSSIL's security boundary is designed around one rule:

> **Retrieval convenience must never widen durable authority or disclose evidence the caller is not allowed to observe.**

Authorization, redaction, lifecycle truth, and provenance are separate concerns. Search/ranking can help choose candidates, but it cannot grant permission, revive suppressed evidence, or create semantic authority.

## Security layers

```text
caller / agent
    |
node-owned AgentContext + Skill/capability
    |
mounted pack read/write authority
    |
visibility / ACL / sensitivity / suppression policy
    |
retrieval candidates
    |
hybrid / reranker
    |
defense-in-depth output filtering
    |
lifecycle / lineage authority
    |
citations / answer / receipt
```

The exact composition can vary by runtime, but the authority direction does not.

## Pack isolation

Knowledge packs are explicit security and semantic boundaries.

A caller authorized for Pack A must not gain Pack B material simply because:

- Pack B has a higher BM25 score;
- Pack B is semantically closer in a dense index;
- reciprocal-rank fusion promotes it;
- a reranker scores it first;
- a Graphiti relationship points to it;
- a stale projection still contains it;
- an event/citation ID can be guessed.

Read scope and write scope are distinct. Reading shared knowledge does not grant permission to modify it.

## Node-owned authority

The persistent node owns its mounted pack, `PackAccess`, Skill registry, and `AgentContext`.

Network request bodies cannot supply a different durable actor or pack manifest to widen authority. The MCP layer delegates to the same canonical `CorpusService` boundary rather than implementing a second authorization model.

## Capability boundary

The MCP surface is limited to:

```text
fossil.search
fossil.read
fossil.lineage
fossil.propose
fossil.validate
fossil.commit
fossil.manage
```

Tools are still subject to the configured Skill/capability policy. Merely reaching the MCP transport does not imply that every tool or every pack is authorized.

The MCP surface does not expose arbitrary:

- Neo4j/Cypher mutation;
- Graphiti mutation;
- shell execution;
- filesystem access;
- environment access;
- database administration.

## Retrieval filtering

The retained v1 security proof covers all normal retrieval paths:

- BM25/lexical;
- D021 dense;
- deterministic hybrid/RRF;
- cross-encoder reranked hybrid;
- direct canonical reads/citations where applicable.

The security contract is stronger than "filter the final answer." Denied candidates must not become user-visible evidence through intermediate routing, ranking, citation, projection, receipt, or export behavior.

Authorization should occur as early as practical and is checked again at durable/output boundaries where defense in depth is useful.

## Dense/vector retrieval

Semantic similarity does not create authorization.

The final adversarial benchmark deliberately included cases where unauthorized evidence was the strongest semantic match. The caller-filtered dense route still had to exclude that evidence from returned context/citations.

Embedding models and vector indexes are disposable projections. They are not trusted to encode ACL or lifecycle truth implicitly in the vector geometry.

## Hybrid/RRF retrieval

Combining lexical and dense scores must not reintroduce evidence removed by visibility policy.

A candidate denied by pack/ACL/sensitivity/suppression rules cannot become authorized merely because it wins one component of the fusion ranking.

## Reranking

A cross-encoder may reorder eligible candidates; it cannot grant access.

The final security proof verifies that unauthorized evidence cannot cross into caller-visible context or citations via reranking. Where a runtime evaluates a wider internal candidate set, it must preserve a hard authorization boundary before caller-visible output and must not leak denied source text through diagnostics/receipts.

## Redaction and suppression

Normal intellectual revision is append-only. Privacy/legal erasure is exceptional.

FOSSIL redaction follows **tombstone before delete**:

```text
publish durable redaction tombstone
        |
confirm durable tombstone
        |
remove sensitive payload bytes
        |
purge/suppress replaceable projections
        |
future rebuild continues to suppress identity
```

A redacted identity cannot simply be republished under the same canonical content identity. Old projection/index material must not resurrect it.

Suppression/redaction must hold through:

- lexical/vector retrieval;
- reranking/context construction;
- citations;
- answers;
- Graphiti/Neo4j projections;
- exports;
- query-execution receipts;
- destructive rebuilds.

## Lifecycle is not ACL

Security answers:

> What may this caller observe or change?

Lifecycle answers:

> What is the canonical epistemic/history state of this knowledge?

Do not conflate them.

An unauthorized stale claim may be hidden from a caller, but that does not erase the fact that it is canonically stale/superseded. Likewise, an authorized item is not automatically current or supported simply because the caller may read it.

Canonical lifecycle states include proposed, supported, disputed, rejected, superseded, retracted, and stale/pending-review semantics. Lifecycle/lineage resolution remains deterministic FOSSIL authority outside retrieval/reranker scores.

## Retrieved text is untrusted data

Source and retrieved text can contain instructions, prompt-injection content, fabricated metadata, or statements that conflict with actual corpus authority.

FOSSIL therefore treats retrieved source text as **untrusted source data**, not executable policy.

Retrieved text cannot:

- widen pack scope;
- invent a durable actor;
- change a Skill/capability grant;
- mutate graph/database state;
- declare its own lifecycle state authoritative;
- fabricate a valid canonical citation merely by printing an ID;
- bypass proposal/validation/commit gates.

The post-Gate-2 campaign includes poisoning/untrusted-context regression coverage in addition to the final ACL/redaction benchmark.

## Exact citations and anti-laundering

A citation is meaningful only when it resolves to canonical source/snapshot identity and the observed source span/hash required by the citation contract.

A model cannot make unsupported text authoritative by attaching a plausible-looking citation string. Citation construction and source role/provenance are validated separately from answer fluency.

## Proposal before commit

Agents do not receive unrestricted write authority.

The intended knowledge-changing flow is:

```text
agent/model
   -> structured proposal
   -> schema validation
   -> stable-reference validation
   -> pack/write-scope validation
   -> provenance/evidence policy
   -> deterministic commit gate
   -> append-only durable event
   -> asynchronous projection
```

Transport success is not semantic success. A model saying "done" is not a committed event. A graph mutation is not a canonical knowledge commit.

## Projection security

Graphiti/Neo4j and retrieval indexes can contain stale or derived state because they are rebuildable operational projections.

Security policy therefore cannot rely on "if it is in the graph/index, it must be visible." Caller visibility is resolved through FOSSIL policy/canonical metadata.

A destructive projection rebuild must preserve redaction/non-resurrection and pack/visibility semantics. Graph-native IDs are never durable authorization or knowledge identity.

## Receipts and observability

Query-execution receipts record safe operational/replay evidence such as route, model/provider identity, pack scope, result IDs, timings, and resolver behavior.

Receipts are **execution observability only**. They are not canonical truth and cannot become a side channel for protected source text.

The final security closeout validated four receipts with SHA-256:

`d18744f61a8d338701a2db4ca083a6493a5392a6889e0daf93c21232d28c559e`

## Final adversarial proof

Evidence branch: `codex/fossil-retrieval-security-final-20260831`  
Security evidence commit: `7e69bdb955b74e7bfadb71b8ebf6cdbab77a295f`  
Final closeout head: `0da699e58df85beea14a3dbc8fc9a048a5893b6d`

The final proof exercised **32 adversarial route cases** across the retained paths.

Results:

- unauthorized stable IDs exposed: **0**;
- unauthorized source/chunk text exposed: **0**;
- unauthorized citations emitted: **0**;
- cross-pack leaks: **0**;
- suppressed/redacted resurrection: **0**;
- reranker promotion across the caller boundary: **0**;
- projection/export/receipt leaks: **0**;
- focused tests: **23 passed**;
- full non-network suite: **709 passed, 1 skipped**;
- valid query-execution receipts: **4**.

Decision: **`security_boundary = PASS`**.

The proof deliberately arranged cases so denied evidence could be lexically/semantically attractive. Passing therefore means the authorization boundary held despite retrieval pressure; it does not mean denied documents were simply absent from every disposable internal fixture/projection.

## Public/network deployment

The merged core node exposes local/network ASGI + MCP composition. Public Internet ingress is a separate deployment/security decision.

Do not expose the merged unauthenticated local node directly to the public Internet.

A bearer-authenticated public MCP edge has been developed in a separate unmerged lane, but public deployment/authentication topology is not part of the v1 core semantic contract documented here. If that lane is resumed, authentication must happen before protected MCP execution and must **not** replace pack/Skill/capability authorization.

Neo4j, shell, filesystem, and admin surfaces should remain non-public.

## Multi-user/shared deployment checklist

Before treating a new shared deployment topology as production-ready, prove at minimum:

1. caller identity/authentication is explicit;
2. pack/ACL/sensitivity policy maps to that identity;
3. denied material cannot cross lexical, dense, hybrid, or reranked paths;
4. direct reads/citations fail closed;
5. redaction reaches projections/exports/caches;
6. receipts/logs do not leak protected source content;
7. durable writes preserve proposal/validation/commit authority;
8. internal graph/database/admin surfaces remain isolated;
9. restore/rebuild repeats the same security invariants;
10. failure is explicit rather than silently falling back to a wider policy.

A new authentication provider, proxy, hosted vector database, or graph service is infrastructure; none is authorized to redefine canonical FOSSIL semantics.
