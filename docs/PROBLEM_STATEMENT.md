# FOSSIL Problem Statement

## The problem FOSSIL exists to solve

Complex technical work already leaves a lot of evidence behind.

A programmer, architect, or researcher may spend days or weeks reading documentation, reviewing papers, testing alternatives, running benchmarks, discussing tradeoffs, and using AI to help search, compare, code, critique, and summarize. The human still guides the work and makes the consequential decisions.

Months later, the raw material often still exists:

- AI transcripts;
- Jira or issue tickets;
- pull requests and commits;
- benchmark output;
- source documents and citations;
- notes and review comments;
- terminal logs and experiment results.

The problem is that these artifacts do not automatically preserve the reasoning chain that connected them.

A later reader can often find **what** was decided. What is harder to recover is:

- why that decision made sense at the time;
- which sources actually mattered;
- what exact evidence supported a claim;
- which assumptions were in force;
- what alternatives were seriously considered;
- what was tried and rejected;
- which benchmark or objection changed the direction;
- what changed after the decision;
- which later conclusions depended on an earlier premise;
- what was believed then versus what is believed now.

An AI can search the surviving artifacts and reconstruct a plausible explanation. That can be useful. It is not the same thing as proving that the reconstruction matches the reasoning path that actually led to the decision.

FOSSIL exists to close that gap.

## The FOSSIL contract

FOSSIL preserves the evidence and reasoning produced during human-led technical work as durable intellectual lineage.

The goal is that months or years later, a project can answer questions such as:

> Why did I make this decision?

> What changed my mind?

> What did we try before?

> Which evidence supported the old position?

> What replaced it, and why?

> Which later decisions depend on something that is no longer current?

The answer should be grounded in preserved evidence and recorded lineage, not invented from scattered history at query time.

## Human-led, AI-assisted

FOSSIL does not assume that an agent owns the research process or the decision.

AI can assist with retrieval, comparison, coding, testing, criticism, summarization, and structured proposals. Humans can do the same directly. The durable corpus records actor provenance so the system can distinguish human, agent, service, importer, and system actions.

The important boundary is not human versus AI. It is **preserved history versus reconstructed history**.

A useful summary is:

> Your transcripts remember what was said. FOSSIL preserves what the work established.

And for software projects:

> Git tells you how the code changed. FOSSIL helps explain why the project's thinking changed.

## What FOSSIL preserves

FOSSIL is designed to preserve more than a final decision record.

The durable model can represent evidence, sources, claims, assumptions, questions, arguments, observations, experiments, decisions, disagreement, dependencies, revisions, supersession, unresolved positions, and exact citations.

This allows a project to retain both current and historical state without rewriting the past.

For example:

```text
March
SQLite is supported under assumptions A and B.

April
A load test contradicts assumption B.
Alternative C is tested and rejected.
A source changes the recovery requirement.

May
The durable append-only core becomes the current decision.
The SQLite premise is superseded.
One dependent conclusion becomes stale_pending_review.

September
A lineage query can recover why the change happened,
what was tried before, and which evidence supported each stage.
```

## What FOSSIL is not claiming

FOSSIL does not claim to recover thoughts that were never captured.

It does not make model agreement equivalent to evidence. It does not make a cryptographic hash equivalent to truth. It does not make retrieval rank authoritative. It does not guarantee that every rationale is complete simply because an event was recorded.

Its narrower promise is that **captured evidence and recorded reasoning history remain inspectable, attributable, and reconstructable without silently replacing history with a later model-generated explanation**.

## Why the architecture looks the way it does

This problem statement explains several core architectural choices:

- immutable source snapshots preserve what was actually observed;
- exact citations bind claims to the evidence used;
- append-only events preserve change instead of overwriting it;
- stable corpus IDs keep lineage intact across rebuilds and migrations;
- current, historical, opposing, and unresolved states remain distinguishable;
- dependency relationships allow changed premises to mark downstream knowledge for review;
- reconstructed conversation material is explicitly different from verbatim material;
- graph, vector, search, and model layers are projections rather than canonical authority.

Those are not features added for their own sake. They are consequences of the core requirement: **a project should be able to explain how its knowledge changed over time and show the evidence behind that history.**

## Canonical positioning

Use this framing when describing FOSSIL publicly or internally:

> FOSSIL is durable decision and intellectual-lineage infrastructure for human-led technical work. It preserves the sources, evidence, assumptions, alternatives, tests, disagreements, and changes behind important decisions so the project can later answer why, what changed, and what was tried before without relying on an AI to reconstruct the missing history.

This document defines product intent. Runtime semantics and durable invariants remain governed by [`../ARCHITECTURE.md`](../ARCHITECTURE.md), versioned schemas, and accepted decision records.