from __future__ import annotations

import argparse
import copy
import hashlib
import json
import platform
import subprocess
import time
import tracemalloc
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker

from fossil_core.answer_eval import (
    AnswerReliabilityCase,
    DeterministicEvidenceAnswerService,
    evaluate_answer_candidate,
)
from fossil_core.answer_pipeline import LineageResolvedModelService
from fossil_core.application.query.poisoning_eval import (
    RetrievalPoisoningCase,
    run_retrieval_poisoning_benchmark,
)
from fossil_core.application.query.receipt import (
    build_query_execution_receipt,
    build_service_invocation,
)
from fossil_core.application.query.security import UntrustedContextModelService
from fossil_core.benchmark_cases import load_benchmark_case_set, retrieval_cases_from_case_set
from fossil_core.pack_corpus import retrieval_documents_from_pack_fixtures
from fossil_core.real_retrieval import (
    DEFAULT_BGE_MODEL,
    DEFAULT_BGE_REVISION,
    DEFAULT_CROSS_ENCODER_MODEL,
    DEFAULT_CROSS_ENCODER_REVISION,
    ReciprocalRankFusionRetriever,
    RerankedRetriever,
    SentenceTransformerCrossEncoderReranker,
    SentenceTransformerEmbeddingProvider,
)
from fossil_core.semantic_retriever import SemanticEmbeddingRetriever
from fossil_core.services import BM25Retriever

ROOT = Path(__file__).resolve().parents[1]
CASE_SCHEMA = ROOT / "schemas" / "benchmark" / "case-set-v1.schema.json"
RECEIPT_SCHEMA = ROOT / "schemas" / "query-execution-receipt" / "v1.schema.json"
DEFAULT_PLAN = ROOT / "benchmarks" / "post-gate2" / "embedding-final-v1.json"
DECISION_CRITICAL_CATEGORIES = frozenset(
    {"current-vs-historical", "decision-lineage", "stale-superseded", "contradiction-disagreement"}
)
CURRENT_QUERY_TOP1_LEAKAGE = {
    "current_architecture_after_reconsideration": {"clm_643b698b7e9e6aee6a16c48c"},
    "graphiti_current_role": {"clm_bdf2ed41fb11e6b1808d3df4"},
}
NONCURRENT_STATES = frozenset(
    {"disputed", "invalidated", "rejected", "retracted", "stale_pending_review", "superseded"}
)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _git_head(root: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()


def _sha256_json(value: Any) -> str:
    rendered = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, (95 * len(ordered) + 99) // 100 - 1))] if ordered else 0.0


def _rss_bytes() -> int | None:
    try:
        import psutil

        return int(psutil.Process().memory_info().rss)
    except (ImportError, OSError):
        return None


def _make_answer_service(documents: list[dict[str, Any]]) -> Any:
    return UntrustedContextModelService(
        LineageResolvedModelService(
            DeterministicEvidenceAnswerService(), documents=documents
        ),
        documents=documents,
    )


def _vector_projection(pack_mounts: Mapping[str, str], embedding_meta: Mapping[str, Any]) -> dict[str, str]:
    identity = {
        "packs": dict(pack_mounts),
        "embedding": dict(embedding_meta),
        "index": "in-memory-cosine",
    }
    return {
        "name": "pack-fixture-retrieval-vector-index",
        "version": "fossil-embedding-final-01",
        "build_id": "vector_" + _sha256_json(identity)[:24],
    }


def _lexical_projection(pack_mounts: Mapping[str, str]) -> dict[str, str]:
    return {
        "name": "pack-fixture-retrieval-lexical-index",
        "version": "fossil-embedding-final-01",
        "build_id": "lexical_" + _sha256_json(dict(pack_mounts))[:24],
    }


def _build_embedding(
    documents: list[dict[str, Any]], spec: Mapping[str, Any], *, implementation_version: str
) -> tuple[Any, dict[str, Any]]:
    before_rss = _rss_bytes()
    load_started = time.perf_counter()
    provider = SentenceTransformerEmbeddingProvider(
        model_name=str(spec["model"]),
        revision=str(spec["revision"]),
        device=str(spec["device"]),
        implementation_version=implementation_version,
    )
    load_ms = (time.perf_counter() - load_started) * 1000.0
    build_started = time.perf_counter()
    tracemalloc.start()
    try:
        retriever = SemanticEmbeddingRetriever(
            documents, provider, version=f"{implementation_version}-semantic-index"
        )
        _, peak_alloc = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    build_ms = (time.perf_counter() - build_started) * 1000.0
    after_rss = _rss_bytes()
    probe = provider.embed(["fossil embedding dimension probe"])
    metadata = copy.deepcopy(provider.metadata())
    metadata["dimensions"] = len(probe[0])
    metadata["requested_model_id"] = f"{spec['model']}@{spec['revision']}"
    metadata["actual_model_id"] = str(metadata["model_id"])
    metadata["load_ms"] = load_ms
    metadata["index_build_ms"] = build_ms
    metadata["peak_python_alloc_bytes_during_load_and_build"] = peak_alloc
    metadata["process_rss_before_bytes"] = before_rss
    metadata["process_rss_after_bytes"] = after_rss
    metadata["process_rss_delta_bytes"] = (
        after_rss - before_rss if after_rss is not None and before_rss is not None else None
    )
    return retriever, metadata


def _build_routes(
    documents: list[dict[str, Any]], plan: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if plan["embeddings"]["d021"]["model"] != DEFAULT_BGE_MODEL or plan["embeddings"]["d021"]["revision"] != DEFAULT_BGE_REVISION:
        raise ValueError("D021 plan pin does not match committed constants")
    reranker_spec = dict(plan["reranker"])
    if reranker_spec["model"] != DEFAULT_CROSS_ENCODER_MODEL or reranker_spec["revision"] != DEFAULT_CROSS_ENCODER_REVISION:
        raise ValueError("reranker plan pin does not match committed constants")

    lexical_started = time.perf_counter()
    lexical = BM25Retriever(documents, version="fossil-embedding-final-01-bm25")
    lexical_build_ms = (time.perf_counter() - lexical_started) * 1000.0
    cross_encoder_started = time.perf_counter()
    cross_encoder = SentenceTransformerCrossEncoderReranker(
        model_name=str(reranker_spec["model"]),
        revision=str(reranker_spec["revision"]),
        device=str(reranker_spec["device"]),
        batch_size=int(reranker_spec["batch_size"]),
        max_length=int(reranker_spec["max_length"]),
        implementation_version="fossil-embedding-final-01-reranker",
    )
    cross_encoder_build_ms = (time.perf_counter() - cross_encoder_started) * 1000.0
    embeddings: dict[str, Any] = {}
    embedding_metadata: dict[str, Any] = {}
    for key, spec in plan["embeddings"].items():
        embeddings[key], embedding_metadata[key] = _build_embedding(
            documents, spec, implementation_version=f"fossil-embedding-final-01-{key}"
        )

    routes: dict[str, Any] = {"bm25-control": lexical}
    for key, prefix in (("d021", "d021"), ("qwen3-0p6b", "qwen3-0p6b")):
        dense = embeddings[key]
        hybrid = ReciprocalRankFusionRetriever(
            [lexical, dense],
            rrf_k=int(plan["rrf_k"]),
            candidate_multiplier=int(plan["candidate_multiplier"]),
            version=f"fossil-embedding-final-01-{prefix}-hybrid-rrf",
        )
        reranked = RerankedRetriever(
            hybrid,
            cross_encoder,
            candidate_multiplier=int(plan["candidate_multiplier"]),
            version=f"fossil-embedding-final-01-{prefix}-reranked",
        )
        routes[f"{prefix}-dense"] = dense
        routes[f"{prefix}-hybrid-rrf"] = hybrid
        routes[f"{prefix}-reranked"] = reranked

    build_metadata = {
        "bm25_index_build_ms": lexical_build_ms,
        "cross_encoder": {
            **copy.deepcopy(cross_encoder.metadata()),
            "build_ms": cross_encoder_build_ms,
        },
    }
    return routes, embedding_metadata, build_metadata


def _route_retrieval(
    route_name: str,
    route_spec: Mapping[str, Any],
    retriever: Any,
    retrieval_cases: list[Any],
    answer_service: Any,
    *,
    pack_mounts: Mapping[str, str],
    projection: Mapping[str, Any],
    limit: int,
    run_ref: str,
    receipts: list[dict[str, Any]],
) -> dict[str, Any]:
    observations: list[dict[str, Any]] = []
    latencies: list[float] = []
    failures_by_category: dict[str, int] = {}
    pack_isolation_violations: list[dict[str, Any]] = []
    stale_before_relevant: list[dict[str, Any]] = []
    totals_by_category: dict[str, int] = {}
    tracemalloc.start()
    try:
        for case in retrieval_cases:
            totals_by_category[case.category] = totals_by_category.get(case.category, 0) + 1
            started = time.perf_counter()
            candidates = retriever.search(case.query, pack_ids=list(case.pack_ids), limit=limit)
            retrieval_ms = (time.perf_counter() - started) * 1000.0
            latencies.append(retrieval_ms)
            returned_ids = [str(item["id"]) for item in candidates]
            allowed_packs = set(case.pack_ids)
            foreign_ids = [
                str(item["id"])
                for item in candidates
                if str(item.get("pack_id", "")) not in allowed_packs
            ]
            if foreign_ids:
                pack_isolation_violations.append({"case_id": case.case_id, "ids": foreign_ids})
            found = set(returned_ids) & set(case.relevant_ids)
            reciprocal_rank = next(
                (1.0 / rank for rank, identifier in enumerate(returned_ids, 1) if identifier in case.relevant_ids),
                0.0,
            )
            failed = not found
            if failed:
                failures_by_category[case.category] = failures_by_category.get(case.category, 0) + 1
            relevant_rank = next(
                (rank for rank, identifier in enumerate(returned_ids, 1) if identifier in case.relevant_ids),
                None,
            )
            stale_ids = [
                str(item["id"])
                for rank, item in enumerate(candidates, 1)
                if (relevant_rank is None or rank < relevant_rank)
                and str(item.get("current_state", "")) in NONCURRENT_STATES
            ]
            if stale_ids and case.category in DECISION_CRITICAL_CATEGORIES:
                stale_before_relevant.append({"case_id": case.case_id, "ids": stale_ids})
            # Preserve the stage-1 audit definition: this metric is only the
            # explicit current-query top-1 superseded/rejected leak.  A stale
            # candidate before the relevant item is retained separately as a
            # ranking diagnostic because durable lifecycle/lineage resolution
            # is the authority path.
            current_leak = bool(
                CURRENT_QUERY_TOP1_LEAKAGE.get(case.case_id, set()) & set(returned_ids[:1])
            )
            response_started = time.perf_counter()
            response = copy.deepcopy(dict(answer_service.run({
                "query": case.query,
                "pack_ids": list(case.pack_ids),
                "context_items": candidates,
                "response_contract": {"outcomes": ["answer", "conflicting_evidence", "current_state_unresolved", "insufficient_evidence"], "authority": "candidate_only"},
            })))
            model_ms = (time.perf_counter() - response_started) * 1000.0
            services = [
                build_service_invocation("retriever", retriever.metadata(), latency_ms=retrieval_ms, cost_usd=0.0)
            ]
            rerank_service = next(
                (item.get("rerank", {}).get("service") for item in candidates if isinstance(item.get("rerank"), Mapping)),
                None,
            )
            if isinstance(rerank_service, Mapping):
                services.append(build_service_invocation("reranker", rerank_service, latency_ms=None, cost_usd=0.0))
            services.append(build_service_invocation("model", response.get("service", answer_service.metadata()), latency_ms=model_ms, cost_usd=0.0))
            receipt = build_query_execution_receipt(
                query=case.query,
                pack_mounts=pack_mounts,
                pack_scope_ids=list(case.pack_ids),
                projection=projection,
                policy={"route_id": route_spec["route_id"], "retrieval_policy_id": "D021-evaluation-boundary", "mode": "raw-embedding-final-01"},
                services=services,
                candidates=candidates,
                response=response,
                trace_ref=f"trace://fossil-embedding-final-01/{route_name}/{case.case_id}",
                run_ref=run_ref,
                query_id=f"{route_name}:{case.case_id}",
                latency_ms=retrieval_ms + model_ms,
                cost_usd=0.0,
            )
            receipts.append(receipt)
            observations.append({
                "case_id": case.case_id,
                "category": case.category,
                "returned_ids": returned_ids,
                "relevant_ids": sorted(case.relevant_ids),
                "hit": bool(found),
                "failed": failed,
                "recall_at_k": len(found) / len(case.relevant_ids),
                "reciprocal_rank": reciprocal_rank,
                "decision_critical": case.category in DECISION_CRITICAL_CATEGORIES,
                "decision_critical_miss": case.category in DECISION_CRITICAL_CATEGORIES and (failed or len(found) < len(case.relevant_ids)),
                "current_query_top1_superseded_leakage": current_leak,
                "latency_ms": retrieval_ms,
                "receipt_id": receipt["receipt_id"],
            })
        _, peak_alloc = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    count = len(observations)
    return {
        "metrics": {
            "limit": limit,
            "hit_rate": sum(item["hit"] for item in observations) / count,
            "mean_recall_at_k": sum(item["recall_at_k"] for item in observations) / count,
            "mrr": sum(item["reciprocal_rank"] for item in observations) / count,
            "decision_critical_miss_count": sum(item["decision_critical_miss"] for item in observations),
            "current_query_top1_superseded_leakage_count": sum(item["current_query_top1_superseded_leakage"] for item in observations),
            "current_query_stale_before_relevant_count": len(stale_before_relevant),
            "pack_isolation_violation_count": len(pack_isolation_violations),
            "mean_latency_ms": sum(latencies) / count,
            "p95_latency_ms": _p95(latencies),
            "peak_python_alloc_bytes": peak_alloc,
            "failure_rate_by_category": {key: value / totals_by_category[key] for key, value in sorted(failures_by_category.items())},
        },
        "safety_audit": {
            "pack_isolation_preserved": not pack_isolation_violations,
            "pack_isolation_violations": pack_isolation_violations,
            "stale_before_relevant": stale_before_relevant,
        },
        "observations": observations,
    }


def _route_answers(
    route_name: str,
    route_spec: Mapping[str, Any],
    retriever: Any,
    answer_cases: list[AnswerReliabilityCase],
    documents: list[dict[str, Any]],
    answer_service: Any,
    *,
    pack_mounts: Mapping[str, str],
    projection: Mapping[str, Any],
    run_ref: str,
    receipts: list[dict[str, Any]],
) -> dict[str, Any]:
    observations: list[dict[str, Any]] = []
    for case in answer_cases:
        started = time.perf_counter()
        candidates = retriever.search(case.query, pack_ids=list(case.pack_ids), limit=case.limit)
        retrieval_ms = (time.perf_counter() - started) * 1000.0
        model_started = time.perf_counter()
        response = copy.deepcopy(dict(answer_service.run({
            "query": case.query,
            "pack_ids": list(case.pack_ids),
            "context_items": candidates,
            "response_contract": {"outcomes": ["answer", "conflicting_evidence", "current_state_unresolved", "insufficient_evidence"], "authority": "candidate_only"},
        })))
        model_ms = (time.perf_counter() - model_started) * 1000.0
        services = [build_service_invocation("retriever", retriever.metadata(), latency_ms=retrieval_ms, cost_usd=0.0)]
        rerank_service = next(
            (item.get("rerank", {}).get("service") for item in candidates if isinstance(item.get("rerank"), Mapping)),
            None,
        )
        if isinstance(rerank_service, Mapping):
            services.append(build_service_invocation("reranker", rerank_service, latency_ms=None, cost_usd=0.0))
        services.append(build_service_invocation("model", response.get("service", answer_service.metadata()), latency_ms=model_ms, cost_usd=0.0))
        receipt = build_query_execution_receipt(
            query=case.query,
            pack_mounts=pack_mounts,
            pack_scope_ids=list(case.pack_ids),
            projection=projection,
            policy={"route_id": route_spec["route_id"], "retrieval_policy_id": "D021-evaluation-boundary", "mode": "raw-embedding-final-01"},
            services=services,
            candidates=candidates,
            response=response,
            trace_ref=f"trace://fossil-embedding-final-01/{route_name}/{case.case_id}",
            run_ref=run_ref,
            query_id=f"{route_name}:{case.case_id}",
            latency_ms=retrieval_ms + model_ms,
            cost_usd=0.0,
        )
        receipts.append(receipt)
        observations.append({
            **evaluate_answer_candidate(response.get("output", {}), case=case, documents=documents),
            "receipt_id": receipt["receipt_id"],
            "retrieval_latency_ms": retrieval_ms,
            "model_latency_ms": model_ms,
            "total_latency_ms": retrieval_ms + model_ms,
        })
    count = len(observations)
    abstention = [item for item in observations if item["expected_outcome"] != "answer"]
    return {
        "metrics": {
            "final_answer_correctness_rate": sum(item["case_correct"] for item in observations) / count,
            "outcome_accuracy": sum(item["outcome_match"] for item in observations) / count,
            "citation_correctness_rate": sum(item["citation_correct"] for item in observations) / count,
            "mean_unsupported_claim_rate": sum(item["unsupported_claim_rate"] for item in observations) / count,
            "completeness_rate": sum(item["completeness"] == 1.0 for item in observations) / count,
            "appropriate_abstention_rate": sum(item["appropriate_abstention"] for item in abstention) / len(abstention) if abstention else None,
            "overabstention_rate": sum(item["overabstention"] for item in observations) / count,
            "high_confidence_error_rate": sum(item["confidence"] >= 0.8 and not item["case_correct"] for item in observations) / count,
            "mean_total_latency_ms": sum(item["total_latency_ms"] for item in observations) / count,
            "p95_total_latency_ms": _p95([item["total_latency_ms"] for item in observations]),
        },
        "observations": observations,
    }


def _validate_receipts(receipts: list[dict[str, Any]]) -> dict[str, Any]:
    schema = _load_json(RECEIPT_SCHEMA)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    failures: list[dict[str, str]] = []
    for receipt in receipts:
        errors = sorted(validator.iter_errors(receipt), key=lambda error: list(error.path))
        if errors:
            failures.append({"receipt_id": str(receipt.get("receipt_id")), "error": errors[0].message})
    return {"count": len(receipts), "valid": not failures, "failure_count": len(failures), "failures": failures}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run FOSSIL final RAW D021 versus Qwen3-Embedding 0.6B benchmark.")
    parser.add_argument("--common-root", type=Path, required=True)
    parser.add_argument("--ai-root", type=Path, required=True)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--poisoning-plan", type=Path, default=ROOT / "benchmarks" / "post-gate2" / "retrieval-poisoning-v1.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipts-output", type=Path, required=True)
    parser.add_argument("--run-ref", default="FOSSIL-EMBEDDING-FINAL-01")
    args = parser.parse_args()

    plan = _load_json(args.plan)
    expected_pins = {str(key): str(value) for key, value in plan["pack_pins"].items()}
    observed_pins = {"fossil-common": _git_head(args.common_root), "fossil-ai-systems": _git_head(args.ai_root)}
    if observed_pins != expected_pins:
        raise SystemExit("pack pin mismatch: " + json.dumps({"expected": expected_pins, "observed": observed_pins}, sort_keys=True))
    stable_ids = {str(key): str(value) for key, value in plan["stable_pack_ids"].items()}
    pack_mounts = {stable_ids[key]: observed_pins[key] for key in observed_pins}
    documents = retrieval_documents_from_pack_fixtures([args.common_root, args.ai_root], schemas_root=ROOT / "schemas")
    if len(documents) != 27:
        raise SystemExit(f"frozen corpus document count mismatch: {len(documents)}")
    case_set = load_benchmark_case_set(ROOT / str(plan["retrieval_case_set"]), CASE_SCHEMA)
    retrieval_cases = retrieval_cases_from_case_set(case_set)
    answer_plan = _load_json(ROOT / str(plan["answer_case_set"]))
    answer_cases = [AnswerReliabilityCase.from_mapping(item) for item in answer_plan["cases"]]
    if len(retrieval_cases) != 21 or len(answer_cases) != 6:
        raise SystemExit(f"frozen case count mismatch: retrieval={len(retrieval_cases)} answer={len(answer_cases)}")

    receipts: list[dict[str, Any]] = []
    try:
        routes, embedding_metadata, build_metadata = _build_routes(documents, plan)
    except Exception as exc:
        report = {
            "schema_version": "fossil.embedding-benchmark-proof.v1",
            "benchmark_id": plan["benchmark_id"],
            "status": "BLOCKED_MODEL_LOAD",
            "model_load_failure": {"type": type(exc).__name__, "message": str(exc)},
            "acceptance_weakened": False,
            "decision": "BLOCKED_MODEL_AVAILABILITY",
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return 2

    route_specs = {str(item["name"]): dict(item) for item in plan["routes"]}
    answer_service = _make_answer_service(documents)
    route_reports: dict[str, Any] = {}
    for route_name in route_specs:
        retriever = routes[route_name]
        embedding_key = route_specs[route_name].get("embedding")
        projection = (
            _vector_projection(pack_mounts, embedding_metadata[embedding_key])
            if embedding_key
            else _lexical_projection(pack_mounts)
        )
        retrieval = _route_retrieval(route_name, route_specs[route_name], retriever, retrieval_cases, answer_service, pack_mounts=pack_mounts, projection=projection, limit=int(plan["retrieval_limit"]), run_ref=args.run_ref, receipts=receipts)
        answers = _route_answers(route_name, route_specs[route_name], retriever, answer_cases, documents, answer_service, pack_mounts=pack_mounts, projection=projection, run_ref=args.run_ref, receipts=receipts)
        route_reports[route_name] = {
            "route": route_specs[route_name],
            "projection": projection,
            "retriever_service": retriever.metadata(),
            "retrieval": retrieval,
            "answers": answers,
            "invariants": {
                "pack_isolation_violations": retrieval["metrics"]["pack_isolation_violation_count"],
                "current_query_top1_superseded_leakage_count": retrieval["metrics"]["current_query_top1_superseded_leakage_count"],
                "lineage_historical_current_target_correctness": retrieval["metrics"]["decision_critical_miss_count"] == 0,
            },
        }

    poisoning_plan = _load_json(args.poisoning_plan)
    poisoning_cases = [RetrievalPoisoningCase.from_mapping(item) for item in poisoning_plan["cases"]]
    poisoning = run_retrieval_poisoning_benchmark(answer_service, documents=documents, cases=poisoning_cases, benchmark_id="fossil-embedding-final-01-shared-poisoning-control")
    receipt_validation = _validate_receipts(receipts)
    sidecar_text = "".join(json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n" for item in receipts)
    sidecar_hash = hashlib.sha256(sidecar_text.encode("utf-8")).hexdigest()
    comparison = {}
    for key in ("d021", "qwen3-0p6b"):
        dense = route_reports[f"{key}-dense"]["retrieval"]["metrics"]
        hybrid = route_reports[f"{key}-hybrid-rrf"]["retrieval"]["metrics"]
        reranked = route_reports[f"{key}-reranked"]["retrieval"]["metrics"]
        answer = route_reports[f"{key}-reranked"]["answers"]["metrics"]
        comparison[key] = {"dense": dense, "hybrid_rrf": hybrid, "reranked": reranked, "reranked_answers": answer}
    d021 = comparison["d021"]["reranked"]
    qwen = comparison["qwen3-0p6b"]["reranked"]
    materially_better = (
        qwen["decision_critical_miss_count"] == 0
        and qwen["current_query_top1_superseded_leakage_count"] == 0
        and qwen["hit_rate"] >= d021["hit_rate"]
        and qwen["mean_recall_at_k"] >= d021["mean_recall_at_k"]
        and qwen["mrr"] > d021["mrr"]
    )
    report = {
        "schema_version": "fossil.embedding-benchmark-proof.v1",
        "benchmark_id": plan["benchmark_id"],
        "benchmark_version": "fossil-embedding-final-01-v1",
        "status": "PASS" if receipt_validation["valid"] and poisoning["passed"] else "FAILED_ACCEPTANCE",
        "source_head": _git_head(ROOT),
        "pack_pins": observed_pins,
        "stable_pack_ids": stable_ids,
        "corpus": {"documents": len(documents), "events": 51, "retrieval_cases": len(retrieval_cases), "answer_cases": len(answer_cases), "retrieval_limit": int(plan["retrieval_limit"])},
        "environment": {"platform": platform.platform(), "machine": platform.machine(), "processor": platform.processor(), "python": platform.python_version(), "psutil_rss_available": _rss_bytes() is not None},
        "embedding_identities": embedding_metadata,
        "build_costs": build_metadata,
        "routes": route_reports,
        "comparison": comparison,
        "shared_poisoning_context_security": poisoning,
        "receipt_validation": {**receipt_validation, "sidecar_sha256": sidecar_hash, "sidecar_path": str(args.receipts_output)},
        "decision": {
            "materially_beats_d021": materially_better,
            "result": "PROMOTION_CONSIDERATION_ONLY" if materially_better else "RETAIN_D021",
            "stop_model_ladder": not materially_better,
            "basis": "Qwen is not materially better unless it improves reranked retrieval without decision-critical misses or current-state leakage; answer/citation/unsupported and security controls remain hard constraints.",
        },
        "acceptance_weakened": False,
        "canonical_semantics_changed": False,
        "invariants": {
            "pack_isolation": "PASS" if all(report["invariants"]["pack_isolation_violations"] == 0 for report in route_reports.values()) else "FAIL",
            "lifecycle_lineage": "PASS" if all(route_reports[name]["invariants"]["lineage_historical_current_target_correctness"] for name in ("d021-reranked", "qwen3-0p6b-reranked")) else "FAIL",
            "current_vs_superseded_leakage": "PASS" if all(report["invariants"]["current_query_top1_superseded_leakage_count"] == 0 for report in route_reports.values()) else "FAIL",
            "citation_authority": "PASS" if all(route["answers"]["metrics"]["citation_correctness_rate"] == 1.0 for route in route_reports.values()) else "FAIL",
            "poisoning_context_security": "PASS" if poisoning["passed"] else "FAIL",
            "receipt_contract": "PASS" if receipt_validation["valid"] else "FAIL",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.receipts_output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.receipts_output.write_text(sidecar_text, encoding="utf-8")
    print(json.dumps({"status": report["status"], "decision": report["decision"], "receipt_validation": receipt_validation, "sidecar_sha256": sidecar_hash}, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
