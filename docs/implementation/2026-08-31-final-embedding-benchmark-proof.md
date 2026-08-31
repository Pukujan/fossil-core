# FOSSIL-EMBEDDING-FINAL-01 — D021 versus Qwen3-Embedding 0.6B

**Result:** `RETAIN_D021` / `STOP_MODEL_LADDER`

## Scope and exact inputs

The benchmark ran from fossil-core source head `227d74d449b0eabe1700edfd87005ae2b819500e` using the frozen post-Gate-2 corpus: 27 projected documents, 51 events, 21 retrieval cases, 6 answer cases, and retrieval limit 5. Pack revisions were `fossil-common@d583005dce06dbb499c3c0de5c22b899655eb8d2` and `fossil-ai-systems@84accd2ee895663990e82ca5b79b592cb503db24`.

The only experimental variable was the local embedding model. Both models used the same RAW BM25 control, dense cosine retriever, BM25+RRF hybrid (`k=60`, candidate multiplier 4), pinned cross-encoder reranker, deterministic lifecycle/lineage resolution, answer/citation evaluator, untrusted-context evaluator, pack filters, and receipt contract.

## Matched reranked result

| Metric | D021 BGE-small | Qwen3-Embedding 0.6B |
| --- | ---: | ---: |
| Hit rate | 1.000 | 1.000 |
| Recall@5 | 1.000 | 1.000 |
| MRR | 0.873 | 0.873 |
| Decision-critical misses | 0 | 0 |
| Current top-1 superseded leakage | 0 | 0 |
| Answer correctness | 0.833 | 0.833 |
| Citation correctness | 1.000 | 1.000 |
| Unsupported-claim rate | 0.167 | 0.167 |
| Appropriate abstention | 0.500 | 0.500 |
| Retrieval p95 | 224.82 ms | 345.79 ms |
| Answer-path p95 | 197.65 ms | 280.57 ms |

The stale-before-relevant diagnostic was 1 for each reranked route; it remained visible in the report and was governed by the existing durable lifecycle/lineage resolver. It did not become truth authority or a canonical mutation.

## Runtime and cost evidence

- D021: `BAAI/bge-small-en-v1.5@5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`, Sentence Transformers `5.2.2`, Torch `2.13.0`, Transformers `5.16.1`, CPU, 384 dimensions.
- Qwen: `Qwen/Qwen3-Embedding-0.6B@97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`, same local runtime, CPU, 1024 dimensions.
- Pinned reranker: `cross-encoder/ms-marco-MiniLM-L6-v2@ce0834f22110de6d9222af7a7a03628121708969`, CPU, batch 16, max length 512.
- Local embedding calls had estimated cost `$0`; Qwen index build/load measured `2.14 s`/`1.49 s` versus D021 `0.33 s`/`0.38 s` in this run.
- Process RSS growth was approximately `139 MiB` for D021 and `1064 MiB` for Qwen. These are same-process local measurements, not a hosted-serving estimate.
- Model-load failure is fail-closed: the runner records `BLOCKED_MODEL_LOAD` and does not substitute another model or emit a promotion result. The candidate loaded successfully for this run.

## Invariants and receipts

Pack isolation, lifecycle/lineage target correctness on the compared reranked routes, current top-1 superseded leakage, citation identity, and the shared poisoning/context-security suite all passed. The shared poisoning suite reported 1.0 final correctness, citation correctness, pack isolation, candidate-only authority, durable-claim boundary, executable-output containment, and security-boundary pass rate.

All 189 receipts (7 routes × 27 frozen queries) validate against `fossil.query-execution-receipt.v1`; the sidecar SHA-256 is `35994c08f2d1da371058cb82f3a541e39bd6c92d345008ed0f707747cbaef861`.

Because Qwen did not improve the matched reranked quality metrics and is materially slower/larger locally, it does not materially beat D021. No Qwen 4B/8B benchmark is authorized from this result.

Evidence files:

- `benchmarks/post-gate2/embedding-final-v1.json`
- `scripts/run_fossil_embedding_final.py`
- `benchmarks/post-gate2/results/2026-08-31-embedding-final/report.json`
- `benchmarks/post-gate2/results/2026-08-31-embedding-final/receipts.jsonl`
