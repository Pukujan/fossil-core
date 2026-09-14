# Shared-chat capture completeness — implementation and assurance plan

Status: ACTIVE implementation plan for Issue #247.

## Authority and evidence baseline

- Architecture authority: `ARCHITECTURE.md` and Issue #86.
- Focused defect/spec: Issue #247.
- Execution/claim ledger: Issue #94.
- Property-driven assurance process: `docs/architecture/property-driven-assurance.md`.
- Exact implementation base: `c080572905238d9d326401f85e66d04a84832014`.
- Durable external reproduction: `Pukujan/test-repo` commit `e325e3471dca8562e75afdefeaf0c2b237e4dfef`, experiment `fossil-2026-09-14-shared-chat-completeness`.

The external reproduction established that a public-share response may contain a much larger provider conversation graph than the initially visible browser surface, while a provider continuation can remain unresolved. It also established that the current FOSSIL path can accept a bounded conversation subset without a machine-readable capture-completeness gate.

## Property to strengthen

`FOSSIL-PROP-CAPTURE-COMPLETENESS-001`

> A remote/shared conversation may be promoted as a complete conversation source only when FOSSIL can mechanically account for every node/reference exposed by the captured representation and has no unresolved continuation/pagination obligation. Partial or uncertain acquisition remains durable evidence but cannot silently become complete conversation truth.

This is a **STRENGTHEN** change. It does not change canonical-vs-projection authority, evidence immutability, conversation lineage semantics, or the existing reconstructed/verbatim distinction.

## Separate dimensions

Capture fidelity and capture completeness are independent.

Fidelity:

- `verbatim` — exact bytes/text as exposed by the captured source representation;
- `reconstructed` — derived reconstruction, explicitly labelled;
- `mixed` — only when existing conversation rules permit and identify mixed evidence.

Completeness:

- `complete` — mechanically proven for the captured source representation;
- `incomplete` — known unresolved/missing acquisition obligation exists;
- `unknown` — evidence is insufficient to prove either complete or a specific partial condition.

`verbatim` MUST NOT imply `complete`.

## Capture/ingest boundary

Acquisition and durable conversation promotion are separate operations:

```text
remote/public source
        |
        v
capture exact observed bytes
        |
        v
capture receipt + graph accounting
        |
        +--> incomplete/unknown -> preserve evidence, refuse complete-conversation promotion
        |
        `--> complete -> eligible for conversation ingestion subject to existing provenance/schema gates
```

An incomplete capture may be stored as immutable evidence. The prohibited behavior is representing it as a complete conversation source or emitting a success state that implies complete acquisition.

## Versioned receipt contract

Introduce `fossil.shared-chat-capture-receipt.v1`, validated by JSON Schema.

Minimum semantic fields:

- receipt schema/version and provider/adaptor identity;
- source URL/reference and exact captured artifact/content digest;
- fidelity status;
- completeness status;
- discovered node IDs/count;
- accounted node IDs/count;
- message-bearing node count;
- root node IDs and current/active node when exposed;
- unresolved parent/child references;
- active/visible branch accounting separately from non-active exposed nodes;
- continuation/pagination presence;
- continuation attempts and terminal state;
- deterministic termination reason;
- capture timestamp/metadata sufficient to distinguish additive later captures.

### Complete predicate

`complete` is permitted only when all required conditions hold:

1. every node exposed by the captured representation is accounted for;
2. there are no unresolved graph references required for traversal;
3. all known continuation/pagination obligations have terminal successful resolution;
4. traversal ends under a mechanically defined terminal condition rather than a heuristic;
5. the receipt itself validates and all referenced captured evidence is resolvable.

None of the following prove completeness: HTTP 2xx, valid HTML/JSON, a large message count, a current-node field, a final assistant message, a rendered viewport, or model judgment.

## Development/assurance sequence

### Phase 0 — SDD/PDD traceability

- Add this implementation plan.
- Add `FOSSIL-PROP-CAPTURE-COMPLETENESS-001` to the public property catalog.
- Add the capture-receipt JSON Schema.
- Keep Issue #247 acceptance and the external lab baseline as durable evidence anchors.

Exit: contract is explicit enough to write executable failure oracles without guessing implementation behavior.

### Phase 1 — RED deterministic TDD

Create deterministic provider-neutral fixtures, independent of the live ChatGPT endpoint:

- short complete graph;
- long complete graph;
- multi-page/continued graph;
- continuation HTTP 403/unavailable;
- continuation timeout/reset;
- malformed/truncated continuation payload;
- unresolved child/parent reference;
- duplicate page/replay;
- branching graph with explicit active branch;
- zero usable conversation structure despite successful transport;
- partial capture followed by a fuller additive capture.

The first RED oracle must demonstrate that the current path has no completeness-aware promotion boundary. Preserve the failed-first result in the issue/PR evidence; do not weaken the oracle.

Exit: tests fail for the missing contract/gate for the intended reason.

### Phase 2 — GREEN minimum implementation

Implement the smallest provider-neutral capture-accounting model and promotion gate needed to satisfy the deterministic contract.

Required behavior:

- exact observed capture remains preservable regardless of completeness;
- `complete` cannot be constructed/accepted with unresolved continuation or graph obligations;
- `incomplete`/`unknown` cannot be promoted as a complete conversation source;
- repeated identical receipts/captures are idempotent;
- a later fuller capture is additive and cannot rewrite the earlier partial capture/provenance;
- existing reconstructed/verbatim conversation semantics remain unchanged.

Do not add model/LLM inference to completeness decisions.

### Phase 3 — property-based/generative assurance

Use Hypothesis (already in the test extra) to generate bounded conversation graphs and continuation partitions.

Minimum invariants:

- `accounted_nodes` is a subset of/equal to discovered nodes;
- `complete => accounted == discovered`;
- `complete => unresolved_refs == empty`;
- `complete => no unresolved continuation obligation`;
- every active-branch node exists in the discovered graph;
- identical replay is idempotent;
- conflicting bytes/meaning under the same stable capture identity fail loudly;
- partial -> fuller acquisition preserves earlier provenance;
- removing a reachable required node or continuation can never make a capture *more* complete.

### Phase 4 — metamorphic assurance

For semantically equivalent source representations, completeness/accounting must remain invariant under:

- JSON/map key reordering;
- irrelevant whitespace/serialization differences after parsing;
- different continuation page boundaries;
- repeated delivery of an identical continuation page;
- reordered independent mapping entries;
- different transport chunk boundaries.

Destructive transformations (remove required node/page, break a reference, make continuation unresolved) must preserve or reduce completeness, never increase it.

### Phase 5 — differential oracle

Add a deliberately small test-only reference graph walker independent of the production traversal implementation.

For generated and fixed fixtures:

- production discovered/accounted node sets must match the reference walker;
- active branch membership must match;
- unresolved reference sets must match.

Do not use rendered/browser-visible message enumeration as the reference oracle.

### Phase 6 — fault injection

Mechanically exercise acquisition failures:

- HTTP 403/429/500;
- timeout and connection reset;
- truncated body;
- malformed but transport-success payload;
- continuation loop;
- A -> B -> A continuation cycle;
- same continuation returned indefinitely;
- checksum/content mismatch where applicable.

Expected invariant: preserve what was observed, record deterministic failure/termination evidence, remain `incomplete`/failed, and never report complete conversation promotion.

### Phase 7 — targeted mutation assurance

Mutation scope is intentionally narrow: the capture predicate, graph accounting, continuation termination, and complete-conversation promotion gate.

Required mutant classes to kill include:

- ignoring unresolved references;
- treating attempted continuation as resolved continuation;
- treating HTTP failure as successful terminal traversal;
- weakening equality/subset node-accounting predicates;
- changing default/unknown state to complete;
- skipping a continuation page;
- removing the incomplete-promotion refusal;
- suppressing conflicting replay/capture identity checks.

Mutation testing is not required across unrelated FOSSIL modules for this issue.

### Phase 8 — E2E FOSSIL integration

Prove both paths:

Complete source:

```text
capture -> valid complete receipt -> conversation source/envelope -> durable event -> semantic/lineage path
```

Incomplete source:

```text
capture -> immutable evidence + incomplete receipt -> complete-conversation promotion refused
```

The second path is a successful fail-closed outcome, not lost evidence.

Run existing conversation lineage, provenance, citation, pack-validation, and relevant rebuild tests as regression gates.

### Phase 9 — live/provider regression and lab Run 2

The real ChatGPT share is integration evidence, not the deterministic CI oracle.

After GREEN + adversarial assurance:

- rerun the exact public share as a new additive run in `Pukujan/test-repo`;
- never overwrite `run-20260914-local-001`;
- compare completeness and ingest behavior mechanically against the baseline;
- if continuation remains blocked, expected behavior is a clean `incomplete` receipt plus refused complete ingestion;
- if continuation becomes traversable and all obligations resolve, continue the lab's blocked semantic query, lineage, and projection-rebuild checks.

## Acceptance matrix

| Surface | Required result |
|---|---|
| Exact source bytes | preserved/content-addressed |
| Fidelity | explicit, independent of completeness |
| Graph accounting | deterministic and exhaustive for exposed representation |
| Continuation | exhausted or explicit unresolved state |
| `complete` claim | only after mechanical proof |
| Incomplete evidence | preserved, not discarded |
| Complete-conversation promotion | fail closed on incomplete/unknown |
| Replay | idempotent |
| Partial -> fuller | additive provenance; no history rewrite |
| Branching graph | active branch explicit; other exposed nodes retained/accounted |
| Existing conversation provenance | unchanged/green |
| Property tests | green |
| Metamorphic tests | green |
| Differential walker | agrees on accounting |
| Fault injection | no false complete |
| Targeted mutations | dangerous predicate/gate mutants killed |
| E2E | complete and incomplete paths both mechanically proven |
| Live share | recorded as evidence, never sole acceptance oracle |

## Stop conditions

Stop and report BLOCKED rather than weakening acceptance if:

- the only path to green infers completeness heuristically;
- provider-specific behavior would leak into canonical conversation semantics;
- a required provider/live proof needs credentials or access not authorized here;
- the branch/base moves and has not been reconciled;
- another valid #94 mutation claim wins the FOSSIL lane;
- existing provenance/citation/lineage invariants would need weakening.

## PR traceability

When a PR is opened, include:

```text
Properties: FOSSIL-PROP-CAPTURE-COMPLETENESS-001
Property impact: STRENGTHEN
Oracle: deterministic + property/metamorphic/differential/fault tests for shared-chat capture
Mutation impact: targeted completeness predicate/promotion gate lane
Formal impact: N/A initially; add TLA+ only if continuation/retry protocol complexity justifies it
External evidence: Pukujan/test-repo@e325e3471dca8562e75afdefeaf0c2b237e4dfef
```

TLA+ is not a prerequisite for the first implementation because the initial boundary is deterministic and single-process. If continuation/retry state becomes sufficiently temporal/concurrent to make state exploration materially useful, model it as a separately bounded follow-up rather than using formalism decoratively.
