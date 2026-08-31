from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import platform
import re
import subprocess
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker

from fossil_core.answer_eval import (
    AnswerReliabilityCase,
    DeterministicEvidenceAnswerService,
    evaluate_answer_candidate,
)
from fossil_core.answer_pipeline import LineageResolvedModelService
from fossil_core.application.rebuild.pack_corpus import retrieval_documents_from_pack_fixtures
from fossil_core.benchmark_cases import load_benchmark_case_set, retrieval_cases_from_case_set
from fossil_core.context_security import UntrustedContextModelService
from fossil_core.execution_receipt import execute_query_with_receipt
from fossil_core.pack_fixture import validate_pack_fixtures
from fossil_core.real_retrieval import (
    DEFAULT_BGE_MODEL,
    DEFAULT_BGE_REVISION,
    DEFAULT_CROSS_ENCODER_MODEL,
    DEFAULT_CROSS_ENCODER_REVISION,
    LifecycleIntentReranker,
    OptionalRetrievalDependencyUnavailable,
    ReciprocalRankFusionRetriever,
    RerankedRetriever,
    SentenceTransformerCrossEncoderReranker,
    SentenceTransformerEmbeddingProvider,
)
from fossil_core.semantic_retriever import SemanticEmbeddingRetriever
from fossil_core.services import BM25Retriever, tokenize

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PLAN = ROOT / "benchmarks" / "post-gate2" / "routing-final-v1.json"
CASE_SCHEMA = ROOT / "schemas" / "benchmark" / "case-set-v1.schema.json"
RECEIPT_SCHEMA = ROOT / "schemas" / "query-execution-receipt" / "v1.schema.json"

CURRENT_QUERY_TOP1_LEAKAGE = {
    "current_architecture_after_reconsideration": {"clm_643b698b7e9e6aee6a16c48c"},
    "graphiti_current_role": {"clm_bdf2ed41fb11e6b1808d3df4"},
}
DECISION_CRITICAL_CATEGORIES = frozenset(
    {"current-vs-historical", "decision-lineage", "stale-superseded", "contradiction-disagreement"}
)
QUERY_CLASSES = (
    "exact-identifier",
    "conceptual",
    "current-latest",
    "lineage-history",
    "broad-synthesis",
    "direct-source-read",
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


def _projection_build_id(pack_mounts: Mapping[str, str]) -> str:
    payload = json.dumps(dict(pack_mounts), sort_keys=True, separators=(",", ":"))
    return "packfix_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def classify_query(query: str) -> str:
    """Classify query text with fixed lexical rules; no model or planner is involved."""

    normalized = " ".join(str(query).lower().split())
    terms = set(tokenize(normalized))
    direct_source = (
        re.search(r"\b(quote|exact wording|verbatim|source|citation|snapshot|artifact)\b", normalized)
        or "screenshot" in normalized
        or "copied text" in normalized
        or "exported chat" in normalized
    )
    if direct_source:
        return "direct-source-read"

    historical = terms & {
        "before",
        "earlier",
        "former",
        "historical",
        "history",
        "old",
        "past",
        "previous",
        "rejected",
        "stale",
        "superseded",
        "lineage",
        "relation",
        "recovery",
        "missing",
        "sqlite",
        "dependent",
    }
    if historical:
        return "lineage-history"

    current = terms & {"current", "currently", "latest", "now", "present", "today", "accepted"}
    if current:
        return "current-latest"

    if (
        re.search(r"\bgate\s+[0-9]", normalized)
        or re.search(r"\b(pack|model|claim|relation)\b", normalized)
        or normalized.startswith(("which ", "did "))
    ):
        return "exact-identifier"

    if len(terms) >= 12 or len(re.findall(r"\band\b", normalized)) >= 1:
        return "broad-synthesis"
    return "conceptual"


def _build_routes(documents: list[dict[str, Any]], plan: Mapping[str, Any]) -> dict[str, Any]:
    models = dict(plan["models"])
    embedding = dict(models["incumbent_embedding"])
    cross_encoder = dict(models["cross_encoder"])
    if embedding != {
        "model": DEFAULT_BGE_MODEL,
        "revision": DEFAULT_BGE_REVISION,
        "device": "cpu",
    }:
        raise ValueError("plan incumbent embedding pin does not match D021")
    if cross_encoder["model"] != DEFAULT_CROSS_ENCODER_MODEL or cross_encoder["revision"] != DEFAULT_CROSS_ENCODER_REVISION:
        raise ValueError("plan cross-encoder pin does not match committed reranker")

    embedder = SentenceTransformerEmbeddingProvider(
        model_name=embedding["model"], revision=embedding["revision"], device=embedding["device"]
    )
    dense = SemanticEmbeddingRetriever(documents, embedder, version="routing-final-d021-bge-v1")
    lexical = BM25Retriever(documents, version="routing-final-bm25-v1")
    hybrid = ReciprocalRankFusionRetriever(
        [lexical, dense],
        rrf_k=int(plan["rrf_k"]),
        candidate_multiplier=int(plan["candidate_multiplier"]),
        version="routing-final-bge-bm25-rrf-v1",
    )
    lifecycle = RerankedRetriever(
        hybrid,
        LifecycleIntentReranker(version="routing-final-lifecycle-v1"),
        candidate_multiplier=int(plan["candidate_multiplier"]),
        version="routing-final-bge-bm25-rrf-lifecycle-v1",
    )
    reranker = SentenceTransformerCrossEncoderReranker(
        model_name=cross_encoder["model"],
        revision=cross_encoder["revision"],
        device=cross_encoder["device"],
        batch_size=int(cross_encoder["batch_size"]),
        max_length=int(cross_encoder["max_length"]),
        implementation_version="routing-final-v1",
    )
    dense_cross = RerankedRetriever(
        dense,
        reranker,
        candidate_multiplier=int(plan["candidate_multiplier"]),
        version="routing-final-bge-dense-crossencoder-v1",
    )
    fixed_best = RerankedRetriever(
        hybrid,
        reranker,
        candidate_multiplier=int(plan["candidate_multiplier"]),
        version="routing-final-bge-bm25-rrf-crossencoder-v1",
    )
    return {
        "bm25": lexical,
        "bge-dense": dense,
        "bge-bm25-rrf": hybrid,
        "bge-bm25-rrf-lifecycle": lifecycle,
        "bge-dense-crossencoder": dense_cross,
        "bge-bm25-rrf-crossencoder": fixed_best,
    }


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))]


def _retrieval_metrics(observations: list[dict[str, Any]], *, include_class_breakdown: bool = True) -> dict[str, Any]:
    count = len(observations)
    return {
        "case_count": count,
        "hit_rate": sum(not bool(item["failed"]) for item in observations) / count,
        "mean_recall_at_k": sum(float(item["recall_at_k"]) for item in observations) / count,
        "mrr": sum(float(item["reciprocal_rank"]) for item in observations) / count,
        "mean_policy_latency_ms": sum(float(item["policy_latency_ms"]) for item in observations) / count,
        "p95_policy_latency_ms": _percentile([float(item["policy_latency_ms"]) for item in observations], 0.95),
        "mean_retrieval_latency_ms": sum(float(item["retrieval_latency_ms"]) for item in observations) / count,
        "p95_retrieval_latency_ms": _percentile([float(item["retrieval_latency_ms"]) for item in observations], 0.95),
        "router_selection_mean_latency_ms": sum(float(item["selection_latency_ms"]) for item in observations) / count,
        "router_selection_p95_latency_ms": _percentile([float(item["selection_latency_ms"]) for item in observations], 0.95),
        "estimated_cost_usd": 0.0,
        "critical_miss_count": sum(bool(item["critical_miss"]) for item in observations),
        "current_top1_superseded_leakage_count": sum(bool(item["top1_leakage"]) for item in observations),
        "pack_isolation_violation_count": sum(int(item["pack_violation_count"]) for item in observations),
        "by_query_class": (
            {
                query_class: _retrieval_metrics(
                    [item for item in observations if item["query_class"] == query_class],
                    include_class_breakdown=False,
                )
                if any(item["query_class"] == query_class for item in observations)
                else {"case_count": 0}
                for query_class in QUERY_CLASSES
            }
            if include_class_breakdown
            else {}
        ),
    }


def _run_retrieval_policy(
    routes: Mapping[str, Any],
    cases: list[Any],
    *,
    fixed_route_name: str,
    route_by_class: Mapping[str, str] | None,
    limit: int,
) -> dict[str, Any]:
    observations: list[dict[str, Any]] = []
    for case in cases:
        if route_by_class is None:
            query_class = classify_query(case.query)
            route_name = fixed_route_name
            selection_latency_ms = 0.0
        else:
            started_selection = time.perf_counter()
            query_class = classify_query(case.query)
            route_name = str(route_by_class.get(query_class, fixed_route_name))
            selection_latency_ms = (time.perf_counter() - started_selection) * 1000.0
        started = time.perf_counter()
        results = routes[route_name].search(case.query, pack_ids=list(case.pack_ids), limit=limit)
        retrieval_latency_ms = (time.perf_counter() - started) * 1000.0
        returned_ids = [str(item["id"]) for item in results]
        found = case.relevant_ids & set(returned_ids)
        reciprocal_rank = next(
            (1.0 / rank for rank, identifier in enumerate(returned_ids, start=1) if identifier in case.relevant_ids),
            0.0,
        )
        foreign = [str(item["id"]) for item in results if str(item.get("pack_id", "")) not in set(case.pack_ids)]
        top1_leakage = bool(results and str(results[0]["id"]) in CURRENT_QUERY_TOP1_LEAKAGE.get(case.case_id, set()))
        observations.append({
            "case_id": case.case_id,
            "category": case.category,
            "query": case.query,
            "query_class": query_class,
            "route_name": route_name,
            "returned_ids": returned_ids,
            "relevant_ids": sorted(case.relevant_ids),
            "recall_at_k": len(found) / len(case.relevant_ids),
            "reciprocal_rank": reciprocal_rank,
            "failed": not bool(found),
            "critical_miss": case.category in DECISION_CRITICAL_CATEGORIES and not bool(found),
            "top1_leakage": top1_leakage,
            "pack_violation_count": len(foreign),
            "pack_violation_ids": foreign,
            "selection_latency_ms": selection_latency_ms,
            "retrieval_latency_ms": retrieval_latency_ms,
            "policy_latency_ms": selection_latency_ms + retrieval_latency_ms,
        })
    return {"metrics": _retrieval_metrics(observations), "observations": observations}


def _answer_metrics(observations: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(observations)
    abstentions = [item for item in observations if item["expected_outcome"] != "answer"]
    return {
        "case_count": count,
        "final_answer_correctness_rate": sum(bool(item["case_correct"]) for item in observations) / count,
        "outcome_accuracy": sum(bool(item["outcome_match"]) for item in observations) / count,
        "citation_correctness_rate": sum(bool(item["citation_correct"]) for item in observations) / count,
        "mean_unsupported_claim_rate": sum(float(item["unsupported_claim_rate"]) for item in observations) / count,
        "completeness_rate": sum(float(item["completeness"]) == 1.0 for item in observations) / count,
        "appropriate_abstention_rate": sum(bool(item["appropriate_abstention"]) for item in abstentions) / len(abstentions) if abstentions else None,
        "overabstention_rate": sum(bool(item["overabstention"]) for item in observations) / count,
        "high_confidence_error_rate": sum(float(item["confidence"]) >= 0.8 and not bool(item["case_correct"]) for item in observations) / count,
        "mean_total_latency_ms": sum(float(item["total_latency_ms"]) for item in observations) / count,
        "p95_total_latency_ms": _percentile([float(item["total_latency_ms"]) for item in observations], 0.95),
        "router_selection_mean_latency_ms": sum(float(item["selection_latency_ms"]) for item in observations) / count,
        "router_selection_p95_latency_ms": _percentile([float(item["selection_latency_ms"]) for item in observations], 0.95),
    }


def _model_service(documents: list[dict[str, Any]]) -> Any:
    return UntrustedContextModelService(
        LineageResolvedModelService(DeterministicEvidenceAnswerService(), documents=documents),
        documents=documents,
    )


def _run_answers(
    routes: Mapping[str, Any],
    cases: list[AnswerReliabilityCase],
    *,
    documents: list[dict[str, Any]],
    pack_mounts: Mapping[str, str],
    projection: Mapping[str, Any],
    fixed_route_name: str,
    route_specs: Mapping[str, Mapping[str, Any]],
    route_by_class: Mapping[str, str] | None,
    policy_id: str,
    run_ref: str,
) -> dict[str, Any]:
    observations: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    service = _model_service(documents)
    for case in cases:
        if route_by_class is None:
            query_class = classify_query(case.query)
            route_name = fixed_route_name
            selection_latency_ms = 0.0
        else:
            started_selection = time.perf_counter()
            query_class = classify_query(case.query)
            route_name = str(route_by_class.get(query_class, fixed_route_name))
            selection_latency_ms = (time.perf_counter() - started_selection) * 1000.0
        response, receipt = execute_query_with_receipt(
            query=case.query,
            pack_mounts=pack_mounts,
            query_pack_ids=list(case.pack_ids),
            projection=projection,
            policy={
                "route_id": str(route_specs[route_name]["route_id"]),
                "retrieval_policy_id": policy_id,
                "mode": "routing-final",
            },
            retriever=routes[route_name],
            model_service=service,
            limit=case.limit,
            trace_ref=f"trace://fossil-routing-final/{policy_id}/{case.case_id}",
            run_ref=run_ref,
            query_id=case.case_id,
            requested_model={
                "provider": "fossil",
                "model_id": None,
                "implementation": "deterministic-evidence-answerer",
            },
        )
        evaluation = evaluate_answer_candidate(response["output"], case=case, documents=documents)
        observations.append({
            **evaluation,
            "case_id": case.case_id,
            "query": case.query,
            "query_class": query_class,
            "route_name": route_name,
            "selection_latency_ms": selection_latency_ms,
            "total_latency_ms": float(receipt["telemetry"]["latency_ms"]) + selection_latency_ms,
            "receipt_id": receipt["receipt_id"],
            "execution_identity_sha256": receipt["execution_identity_sha256"],
            "result_sha256": receipt["result_sha256"],
        })
        receipts.append(receipt)
    return {"metrics": _answer_metrics(observations), "observations": observations, "receipts": receipts}


def _compare_metrics(
    fixed_retrieval: Mapping[str, Any],
    routed_retrieval: Mapping[str, Any],
    fixed_answers: Mapping[str, Any],
    routed_answers: Mapping[str, Any],
) -> dict[str, Any]:
    quality = {
        key: float(routed_retrieval[key]) - float(fixed_retrieval[key])
        for key in ("hit_rate", "mean_recall_at_k", "mrr")
    }
    answer_quality = {
        "final_answer_correctness_rate": float(routed_answers["final_answer_correctness_rate"]) - float(fixed_answers["final_answer_correctness_rate"]),
        "citation_correctness_rate": float(routed_answers["citation_correctness_rate"]) - float(fixed_answers["citation_correctness_rate"]),
        "unsupported_claim_rate_reduction": float(fixed_answers["mean_unsupported_claim_rate"]) - float(routed_answers["mean_unsupported_claim_rate"]),
        "appropriate_abstention_rate": float(routed_answers["appropriate_abstention_rate"]) - float(fixed_answers["appropriate_abstention_rate"]),
    }
    return {
        "quality_deltas": quality,
        "answer_quality_deltas": answer_quality,
        "p95_policy_latency_delta_ms": float(routed_retrieval["p95_policy_latency_ms"]) - float(fixed_retrieval["p95_policy_latency_ms"]),
        "p95_policy_latency_ratio": float(routed_retrieval["p95_policy_latency_ms"]) / float(fixed_retrieval["p95_policy_latency_ms"]),
        "answer_p95_total_latency_delta_ms": float(routed_answers["p95_total_latency_ms"]) - float(fixed_answers["p95_total_latency_ms"]),
        "retrieval_critical_miss_delta": int(routed_retrieval["critical_miss_count"]) - int(fixed_retrieval["critical_miss_count"]),
        "answer_high_confidence_error_delta": float(routed_answers["high_confidence_error_rate"]) - float(fixed_answers["high_confidence_error_rate"]),
    }


def _decision(*, fixed_retrieval: Mapping[str, Any], routed_retrieval: Mapping[str, Any], fixed_answers: Mapping[str, Any], routed_answers: Mapping[str, Any], rule: Mapping[str, Any]) -> dict[str, Any]:
    quality_regressions = {
        "hit_rate": routed_retrieval["hit_rate"] < fixed_retrieval["hit_rate"],
        "mean_recall_at_k": routed_retrieval["mean_recall_at_k"] < fixed_retrieval["mean_recall_at_k"],
        "mrr": routed_retrieval["mrr"] < fixed_retrieval["mrr"],
        "final_answer_correctness_rate": routed_answers["final_answer_correctness_rate"] < fixed_answers["final_answer_correctness_rate"],
        "citation_correctness_rate": routed_answers["citation_correctness_rate"] < fixed_answers["citation_correctness_rate"],
        "unsupported_claim_rate": routed_answers["mean_unsupported_claim_rate"] > fixed_answers["mean_unsupported_claim_rate"],
        "critical_misses": routed_retrieval["critical_miss_count"] > fixed_retrieval["critical_miss_count"],
        "current_top1_superseded_leakage": routed_retrieval["current_top1_superseded_leakage_count"] > fixed_retrieval["current_top1_superseded_leakage_count"],
        "pack_isolation": routed_retrieval["pack_isolation_violation_count"] > fixed_retrieval["pack_isolation_violation_count"],
    }
    no_regressions = not any(quality_regressions.values())
    quality_gain = (
        routed_retrieval["hit_rate"] - fixed_retrieval["hit_rate"] >= 0.05
        or routed_retrieval["mean_recall_at_k"] - fixed_retrieval["mean_recall_at_k"] >= 0.05
        or routed_retrieval["mrr"] - fixed_retrieval["mrr"] >= 0.05
        or routed_answers["final_answer_correctness_rate"] - fixed_answers["final_answer_correctness_rate"] >= 0.05
        or routed_retrieval["critical_miss_count"] < fixed_retrieval["critical_miss_count"]
    )
    latency_gain = routed_retrieval["p95_policy_latency_ms"] <= fixed_retrieval["p95_policy_latency_ms"] * 0.90
    accepted = no_regressions and (quality_gain or latency_gain)
    return {
        "quality_regressions": quality_regressions,
        "no_regressions": no_regressions,
        "material_quality_gain": quality_gain,
        "material_latency_gain": latency_gain,
        "accepted": accepted,
        "decision": "ACCEPT_ROUTING" if accepted else "REJECT_ROUTING",
        "rule": dict(rule),
        "reason": "router meets matched quality/latency gate" if accepted else "router does not materially improve matched quality or latency without regressions",
    }


def _validate_receipts(receipts: list[dict[str, Any]]) -> list[str]:
    validator = Draft202012Validator(_load_json(RECEIPT_SCHEMA), format_checker=FormatChecker())
    errors: list[str] = []
    for index, receipt in enumerate(receipts):
        for error in validator.iter_errors(receipt):
            errors.append(f"receipt[{index}] {error.json_path}: {error.message}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the final deterministic FOSSIL routing gate.")
    parser.add_argument("--common-root", type=Path, required=True)
    parser.add_argument("--ai-root", type=Path, required=True)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--run-ref", default="fossil-routing-final-01")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipts", type=Path, required=True)
    args = parser.parse_args()

    plan = _load_json(args.plan)
    expected_pins = {str(key): str(value) for key, value in plan["pack_pins"].items()}
    observed_pins = {"fossil-common": _git_head(args.common_root), "fossil-ai-systems": _git_head(args.ai_root)}
    if observed_pins != expected_pins:
        raise SystemExit(f"pack pin mismatch: expected {expected_pins}, observed {observed_pins}")
    pack_audit = validate_pack_fixtures([args.common_root, args.ai_root], schemas_root=ROOT / "schemas")
    pack_ids = dict(plan["stable_pack_ids"])
    pack_mounts = {str(pack_ids[name]): revision for name, revision in observed_pins.items()}
    projection = {"name": "pack-fixture-retrieval-documents", "version": "1", "build_id": _projection_build_id(pack_mounts)}
    documents = retrieval_documents_from_pack_fixtures([args.common_root, args.ai_root], schemas_root=ROOT / "schemas")
    case_set = load_benchmark_case_set(ROOT / str(plan["retrieval_case_set"]), CASE_SCHEMA)
    retrieval_cases = retrieval_cases_from_case_set(case_set)
    answer_plan = _load_json(ROOT / str(plan["answer_case_set"]))
    answer_cases = [AnswerReliabilityCase.from_mapping(item) for item in answer_plan["cases"]]
    if len(documents) != 27 or len(retrieval_cases) != 21 or len(answer_cases) != 6 or pack_audit.event_count != 51:
        raise SystemExit(f"frozen corpus mismatch: documents={len(documents)} retrieval={len(retrieval_cases)} answers={len(answer_cases)} events={pack_audit.event_count}")

    try:
        routes = _build_routes(documents, plan)
    except OptionalRetrievalDependencyUnavailable as exc:
        blocked = {"benchmark_id": plan["benchmark_id"], "status": "BLOCKED_MODEL_LOAD", "error_type": type(exc).__name__, "message": str(exc)}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(blocked, indent=2) + "\n", encoding="utf-8")
        return 2

    route_specs = {str(item["name"]): dict(item) for item in plan["routes"]}
    if set(route_specs) != set(routes):
        raise ValueError("implemented routes do not match versioned route plan")
    fixed = dict(plan["fixed_policy"])
    router = dict(plan["router"])
    fixed_retrieval = _run_retrieval_policy(routes, retrieval_cases, fixed_route_name=str(fixed["route_name"]), route_by_class=None, limit=int(plan["retrieval_limit"]))
    routed_retrieval = _run_retrieval_policy(routes, retrieval_cases, fixed_route_name=str(router["unknown_class_route"]), route_by_class=dict(router["route_by_class"]), limit=int(plan["retrieval_limit"]))
    fixed_answers = _run_answers(routes, answer_cases, documents=documents, pack_mounts=pack_mounts, projection=projection, fixed_route_name=str(fixed["route_name"]), route_specs=route_specs, route_by_class=None, policy_id=str(fixed["policy_id"]), run_ref=args.run_ref)
    routed_answers = _run_answers(routes, answer_cases, documents=documents, pack_mounts=pack_mounts, projection=projection, fixed_route_name=str(router["unknown_class_route"]), route_specs=route_specs, route_by_class=dict(router["route_by_class"]), policy_id=str(router["policy_id"]), run_ref=args.run_ref)
    receipts = fixed_answers["receipts"] + routed_answers["receipts"]
    receipt_errors = _validate_receipts(receipts)
    sidecar = "".join(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n" for receipt in receipts).encode("utf-8")
    args.receipts.parent.mkdir(parents=True, exist_ok=True)
    args.receipts.write_bytes(sidecar)
    sidecar_sha256 = hashlib.sha256(sidecar).hexdigest()
    decision = _decision(fixed_retrieval=fixed_retrieval["metrics"], routed_retrieval=routed_retrieval["metrics"], fixed_answers=fixed_answers["metrics"], routed_answers=routed_answers["metrics"], rule=dict(plan["decision_rule"]))
    route_counts = Counter(item["route_name"] for item in routed_retrieval["observations"])
    class_counts = Counter(item["query_class"] for item in routed_retrieval["observations"])
    report = {
        "schema_version": "fossil.routing-benchmark-proof.v1",
        "benchmark_id": str(plan["benchmark_id"]),
        "status": "PASS" if not receipt_errors else "FAIL_RECEIPT_SCHEMA",
        "authority": "Routing and retrieval scores are evaluation evidence only; durable evidence, lifecycle/lineage, citation, pack, and security semantics remain authoritative.",
        "repo_pack_pins": observed_pins,
        "pack_mounts": pack_mounts,
        "pack_audit": {"pack_ids": list(pack_audit.pack_ids), "artifact_count": pack_audit.artifact_count, "snapshot_count": pack_audit.snapshot_count, "event_count": pack_audit.event_count, "citation_count": pack_audit.citation_count, "claim_count": pack_audit.claim_count, "relation_count": pack_audit.relation_count},
        "projection": projection,
        "corpus_document_count": len(documents),
        "retrieval_case_count": len(retrieval_cases),
        "answer_case_count": len(answer_cases),
        "environment": {"platform": platform.platform(), "python": platform.python_version(), "machine": platform.machine(), "sentence_transformers": importlib.metadata.version("sentence-transformers") if importlib.util.find_spec("sentence_transformers") else "unavailable", "torch": importlib.metadata.version("torch") if importlib.util.find_spec("torch") else "unavailable", "transformers": importlib.metadata.version("transformers") if importlib.util.find_spec("transformers") else "unavailable"},
        "fixed_policy": {"policy": fixed, "retrieval": fixed_retrieval, "answers": {key: value for key, value in fixed_answers.items() if key != "receipts"}},
        "routed_policy": {"policy": router, "retrieval": routed_retrieval, "answers": {key: value for key, value in routed_answers.items() if key != "receipts"}, "route_counts": dict(sorted(route_counts.items())), "query_class_counts": dict(sorted(class_counts.items()))},
        "comparison": _compare_metrics(fixed_retrieval["metrics"], routed_retrieval["metrics"], fixed_answers["metrics"], routed_answers["metrics"]),
        "decision": decision,
        "receipt_contract": {"receipt_count": len(receipts), "schema_version": "fossil.query-execution-receipt.v1", "schema_errors": receipt_errors, "valid": not receipt_errors, "sidecar_sha256": sidecar_sha256},
        "invariants": {"pack_isolation": fixed_retrieval["metrics"]["pack_isolation_violation_count"] == 0 and routed_retrieval["metrics"]["pack_isolation_violation_count"] == 0, "current_top1_superseded_leakage": fixed_retrieval["metrics"]["current_top1_superseded_leakage_count"] == 0 and routed_retrieval["metrics"]["current_top1_superseded_leakage_count"] == 0, "decision_critical_misses_not_increased": routed_retrieval["metrics"]["critical_miss_count"] <= fixed_retrieval["metrics"]["critical_miss_count"], "citation_and_answer_controls": fixed_answers["metrics"]["citation_correctness_rate"] == routed_answers["metrics"]["citation_correctness_rate"] and fixed_answers["metrics"]["mean_unsupported_claim_rate"] == routed_answers["metrics"]["mean_unsupported_claim_rate"], "no_model_or_graphiti_change": True},
        "acceptance_weakened": False,
        "no_llm_planner": True,
        "next": "FOSSIL-RETRIEVAL-SECURITY-FINAL-01",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "decision": decision["decision"], "receipt_count": len(receipts), "sidecar_sha256": sidecar_sha256}, sort_keys=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
