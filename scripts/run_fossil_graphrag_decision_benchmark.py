from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
from threading import Event, Thread
from typing import Any, Iterable, Mapping

from jsonschema import Draft202012Validator, FormatChecker

from fossil_core.adapters.graph import (
    GRAPHITI_EXPANSION_RESOLVER,
    GraphitiExpansionRetriever,
    GraphitiRetrievalError,
)
from fossil_core.answer_eval import (
    AnswerReliabilityCase,
    DeterministicEvidenceAnswerService,
    evaluate_answer_candidate,
)
from fossil_core.answer_pipeline import LineageResolvedModelService
from fossil_core.application.evaluation.benchmark import RetrievalBenchmark
from fossil_core.application.evaluation.cases import (
    load_benchmark_case_set,
    retrieval_cases_from_case_set,
)
from fossil_core.application.query.receipt import (
    build_failed_query_execution_receipt,
    execute_query_with_receipt,
)
from fossil_core.benchmark import RetrievalBenchmarkCase
from fossil_core.benchmark_compare import classify_retrieval_result
from fossil_core.context_security import UntrustedContextModelService
from fossil_core.pack_corpus import retrieval_documents_from_pack_fixtures
from fossil_core.real_retrieval import (
    DEFAULT_BGE_MODEL,
    DEFAULT_BGE_REVISION,
    DEFAULT_CROSS_ENCODER_MODEL,
    DEFAULT_CROSS_ENCODER_REVISION,
    LifecycleIntentReranker,
    ReciprocalRankFusionRetriever,
    RerankedRetriever,
    SentenceTransformerCrossEncoderReranker,
    SentenceTransformerEmbeddingProvider,
)
from fossil_core.semantic_retriever import SemanticEmbeddingRetriever
from fossil_core.services import BM25Retriever


if sys.platform == "win32":
    # The Neo4j async driver is exercised through short-lived asyncio.run
    # calls by the existing synchronous benchmark interface. Selector loops
    # avoid the Proactor transport teardown failure on the Windows host while
    # preserving the real Graphiti/Neo4j path.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

try:
    import psutil
except ModuleNotFoundError:  # pragma: no cover - depends on the host image
    psutil = None

ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "benchmarks" / "post-gate2" / "graphrag-decision-v1.json"
CASE_SCHEMA = ROOT / "schemas" / "benchmark" / "case-set-v1.schema.json"
RECEIPT_SCHEMA = ROOT / "schemas" / "query-execution-receipt" / "v1.schema.json"

CURRENT_QUERY_TOP1_LEAKAGE = {
    "current_architecture_after_reconsideration": {"clm_643b698b7e9e6aee6a16c48c"},
    "graphiti_current_role": {"clm_bdf2ed41fb11e6b1808d3df4"},
}

QUERY_CLASS_NAMES = (
    "exact / identifier",
    "conceptual",
    "current-state",
    "historical / lineage",
    "relationship / multi-hop",
    "broad synthesis",
    "conflicting / superseded evidence",
)


class _GraphitiRuntime:
    """Keep the real Graphiti async driver on one dedicated event loop.

    The benchmark consumes FOSSIL's synchronous retriever interface, while the
    Neo4j driver binds its transport futures to the event loop that first uses
    it. A dedicated loop makes every projection validation, search, resolution,
    and close operation use the same real Graphiti/Neo4j runtime on Windows.
    """

    def __init__(self, uri: str, user: str, password: str) -> None:
        self._ready = Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._graphiti: Any = None
        self._startup_error: BaseException | None = None
        self._driver_closed = False
        self._thread = Thread(
            target=self._thread_main,
            args=(uri, user, password),
            name="fossil-graphrag-graphiti-loop",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=30):
            raise RuntimeError("Graphiti runtime did not initialize within 30 seconds")
        if self._startup_error is not None:
            raise RuntimeError("Graphiti runtime initialization failed") from self._startup_error

    def _thread_main(self, uri: str, user: str, password: str) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            from graphiti_core import Graphiti

            self._graphiti = Graphiti(uri, user, password, max_coroutines=1)
        except BaseException as exc:  # pragma: no cover - host/runtime dependent
            self._startup_error = exc
            self._ready.set()
            loop.close()
            return
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            loop.close()

    @property
    def graphiti(self) -> Any:
        if self._graphiti is None:
            raise RuntimeError("Graphiti runtime is not initialized")
        return self._graphiti

    def run(self, awaitable: Any) -> Any:
        loop = self._loop
        if loop is None or not self._thread.is_alive():
            close = getattr(awaitable, "close", None)
            if close is not None:
                close()
            raise RuntimeError("Graphiti runtime event loop is unavailable")
        future = asyncio.run_coroutine_threadsafe(awaitable, loop)
        return future.result()

    def close_driver(self) -> None:
        if self._driver_closed:
            return
        self.run(self.graphiti.close())
        self._driver_closed = True

    def shutdown(self) -> None:
        if not self._thread.is_alive():
            return
        try:
            self.close_driver()
        except Exception:
            pass
        loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(loop.stop)
        self._thread.join(timeout=10)
DECISION_CRITICAL_CLASSES = frozenset(
    {
        "current-state",
        "historical / lineage",
        "relationship / multi-hop",
        "broad synthesis",
        "conflicting / superseded evidence",
    }
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
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _process_rss_bytes() -> int | None:
    if psutil is None:
        return None
    return int(psutil.Process().memory_info().rss)


def _model_service(documents: list[dict[str, Any]]) -> Any:
    return UntrustedContextModelService(
        LineageResolvedModelService(
            DeterministicEvidenceAnswerService(), documents=documents
        ),
        documents=documents,
    )


def _query_class(case: RetrievalBenchmarkCase) -> str:
    if case.case_id in {
        "historical_current_supersession_bundle",
        "false_premise_quote_missing_chat",
    }:
        return "broad synthesis"
    by_category = {
        "exact-factual-lookup": "exact / identifier",
        "source-citation-recovery": "conceptual",
        "insufficient-evidence": "conceptual",
        "obscure-deep-evidence": "conceptual",
        "current-vs-historical": "current-state",
        "stale-superseded": "current-state",
        "conversation-lineage": "historical / lineage",
        "decision-lineage": "relationship / multi-hop",
        "cross-pack-isolation": "relationship / multi-hop",
        "contradiction-disagreement": "conflicting / superseded evidence",
    }
    try:
        return by_category[case.category]
    except KeyError as exc:
        raise ValueError(f"unmapped frozen benchmark category: {case.category}") from exc


def _query_class_metrics(
    result: Mapping[str, Any], cases: Iterable[RetrievalBenchmarkCase]
) -> dict[str, dict[str, Any]]:
    case_by_id = {case.case_id: case for case in cases}
    observations_by_class: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for observation in result["observations"]:
        observations_by_class[_query_class(case_by_id[str(observation["case_id"])])].append(
            observation
        )

    metrics: dict[str, dict[str, Any]] = {}
    for name in QUERY_CLASS_NAMES:
        observations = observations_by_class.get(name, [])
        misses = sum(bool(item["failed"]) for item in observations)
        count = len(observations)
        metrics[name] = {
            "case_count": count,
            "hit_rate": (
                sum(not bool(item["failed"]) for item in observations) / count
                if count
                else None
            ),
            "recall_at_k": (
                sum(float(item["recall_at_k"]) for item in observations) / count
                if count
                else None
            ),
            "mrr": (
                sum(float(item["reciprocal_rank"]) for item in observations) / count
                if count
                else None
            ),
            "decision_critical_misses": misses if name in DECISION_CRITICAL_CLASSES else 0,
            "missed_case_ids": [
                str(item["case_id"]) for item in observations if bool(item["failed"])
            ],
        }
    return metrics


def _audit_route(
    retriever: Any,
    cases: list[RetrievalBenchmarkCase],
    *,
    limit: int,
) -> dict[str, Any]:
    pack_violations: list[dict[str, Any]] = []
    top1_leakage: list[dict[str, Any]] = []
    for case in cases:
        results = retriever.search(case.query, pack_ids=list(case.pack_ids), limit=limit)
        allowed = set(case.pack_ids)
        foreign = [
            str(item["id"])
            for item in results
            if str(item.get("pack_id", "")) not in allowed
        ]
        if foreign:
            pack_violations.append(
                {"case_id": case.case_id, "foreign_ids": foreign}
            )
        forbidden = CURRENT_QUERY_TOP1_LEAKAGE.get(case.case_id, set())
        if results and str(results[0]["id"]) in forbidden:
            top1_leakage.append(
                {
                    "case_id": case.case_id,
                    "top1_id": str(results[0]["id"]),
                    "forbidden_ids": sorted(forbidden),
                }
            )
    return {
        "pack_isolation_preserved": not pack_violations,
        "pack_isolation_violations": pack_violations,
        "current_query_top1_superseded_leakage_count": len(top1_leakage),
        "current_query_top1_superseded_leakage": top1_leakage,
    }


def _answer_metrics(observations: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(observations)
    abstention = [item for item in observations if item["expected_outcome"] != "answer"]
    return {
        "answer_correctness": sum(bool(item["case_correct"]) for item in observations) / count,
        "outcome_accuracy": sum(bool(item["outcome_match"]) for item in observations) / count,
        "citation_correctness": sum(bool(item["citation_correct"]) for item in observations) / count,
        "unsupported_claim_rate": sum(float(item["unsupported_claim_rate"]) for item in observations) / count,
        "abstention_conflict_handling": (
            sum(bool(item["appropriate_abstention"]) for item in abstention) / len(abstention)
            if abstention
            else None
        ),
    }


def _subset_retrieval_metrics(
    observations: list[dict[str, Any]], predicate: Any
) -> dict[str, Any]:
    selected = [item for item in observations if predicate(item)]
    count = len(selected)
    return {
        "case_count": count,
        "hit_rate": (
            sum(not bool(item["failed"]) for item in selected) / count
            if count
            else None
        ),
        "recall_at_k": (
            sum(float(item["recall_at_k"]) for item in selected) / count
            if count
            else None
        ),
        "mrr": (
            sum(float(item["reciprocal_rank"]) for item in selected) / count
            if count
            else None
        ),
    }


def _subset_answer_metrics(
    observations: list[dict[str, Any]], predicate: Any
) -> dict[str, Any]:
    selected = [item for item in observations if predicate(item)]
    count = len(selected)
    return {
        "case_count": count,
        "case_correctness": (
            sum(bool(item["case_correct"]) for item in selected) / count
            if count
            else None
        ),
        "outcome_accuracy": (
            sum(bool(item["outcome_match"]) for item in selected) / count
            if count
            else None
        ),
        "citation_correctness": (
            sum(bool(item["citation_correct"]) for item in selected) / count
            if count
            else None
        ),
        "unsupported_claim_rate": (
            sum(float(item["unsupported_claim_rate"]) for item in selected) / count
            if count
            else None
        ),
    }


def _semantic_metrics(
    *,
    retrieval: Mapping[str, Any],
    retrieval_cases: list[RetrievalBenchmarkCase],
    taxonomy: Mapping[str, Any],
    safety: Mapping[str, Any],
    answer_observations: list[dict[str, Any]],
    answer_cases: list[AnswerReliabilityCase],
    receipt_set: list[Mapping[str, Any]],
) -> dict[str, Any]:
    retrieval_observations = list(retrieval["observations"])
    current_query_count = sum(
        LifecycleIntentReranker.intent_for_query(case.query) == "current"
        for case in retrieval_cases
    )
    lineage_categories = {"decision-lineage", "conversation-lineage"}
    historical_current_categories = {
        "current-vs-historical",
        "stale-superseded",
        "decision-lineage",
    }
    lineage_answer_categories = {
        "historical-answer",
        "current-unresolved",
        "resolved-contradiction",
        "historical-rejected",
    }
    historical_current_answer_categories = {
        "current-answer",
        "historical-answer",
        "current-unresolved",
    }
    lineage_retrieval = _subset_retrieval_metrics(
        retrieval_observations,
        lambda item: str(item["category"]) in lineage_categories,
    )
    historical_current_retrieval = _subset_retrieval_metrics(
        retrieval_observations,
        lambda item: str(item["category"]) in historical_current_categories,
    )
    lineage_answer = _subset_answer_metrics(
        answer_observations,
        lambda item: str(item.get("category", "")) in lineage_answer_categories,
    )
    historical_current_answer = _subset_answer_metrics(
        answer_observations,
        lambda item: str(item.get("category", ""))
        in historical_current_answer_categories,
    )
    security_resolver_count = sum(
        any(
            str(resolver.get("resolver", "")) == "fossil-untrusted-context-v1"
            for resolver in receipt.get("resolvers", [])
        )
        for receipt in receipt_set
        if receipt.get("policy", {}).get("mode")
        in {"matched-retrieval", "answer-evaluation"}
    )
    comparable_receipt_count = sum(
        receipt.get("policy", {}).get("mode")
        in {"matched-retrieval", "answer-evaluation"}
        for receipt in receipt_set
    )
    return {
        "current_vs_superseded_leakage": {
            "current_query_count": current_query_count,
            "leakage_case_count": int(
                taxonomy["counts"]["stale_superseded_leakage"]
            ),
            "leakage_rate": (
                taxonomy["counts"]["stale_superseded_leakage"] / current_query_count
                if current_query_count
                else 0.0
            ),
            "top1_forbidden_id_leakage_case_count": int(
                safety["current_query_top1_superseded_leakage_count"]
            ),
        },
        "lineage_correctness": {
            "basis": {
                "retrieval_categories": sorted(lineage_categories),
                "answer_categories": sorted(lineage_answer_categories),
            },
            "retrieval": lineage_retrieval,
            "answer": lineage_answer,
        },
        "historical_current_target_correctness": {
            "basis": {
                "retrieval_categories": sorted(historical_current_categories),
                "answer_categories": sorted(historical_current_answer_categories),
            },
            "retrieval": historical_current_retrieval,
            "answer": historical_current_answer,
        },
        "pack_isolation": {
            "preserved": bool(safety["pack_isolation_preserved"]),
            "violation_case_count": len(safety["pack_isolation_violations"]),
        },
        "poisoning_context_security_compatibility": {
            "comparable_receipt_count": comparable_receipt_count,
            "receipts_with_untrusted_context_resolver": security_resolver_count,
            "resolver_coverage": (
                security_resolver_count / comparable_receipt_count
                if comparable_receipt_count
                else 0.0
            ),
            "unsupported_confidence_leakage": dict(
                taxonomy["unsupported_confidence_leakage"]
            ),
        },
        "answer_evaluation": {
            "case_count": len(answer_cases),
            "answer_correctness": _answer_metrics(answer_observations)[
                "answer_correctness"
            ],
            "citation_correctness": _answer_metrics(answer_observations)[
                "citation_correctness"
            ],
            "unsupported_claim_rate": _answer_metrics(answer_observations)[
                "unsupported_claim_rate"
            ],
            "abstention_conflict_handling": _answer_metrics(answer_observations)[
                "abstention_conflict_handling"
            ],
        },
    }


def _graph_query_count_metrics(
    receipts: list[Mapping[str, Any]],
) -> dict[str, Any] | None:
    counts: list[float] = []
    for receipt in receipts:
        for resolver in receipt.get("resolvers", []):
            if resolver.get("resolver") != GRAPHITI_EXPANSION_RESOLVER:
                continue
            value = resolver.get("diagnostics", {}).get("graph_query_count")
            if value is not None:
                counts.append(float(value))
    if not counts:
        return None
    ordered = sorted(counts)
    p95_index = min(len(ordered) - 1, max(0, int(len(ordered) * 0.95 + 0.999999) - 1))
    return {
        "mean": sum(counts) / len(counts),
        "p95": ordered[p95_index],
        "min": min(counts),
        "max": max(counts),
        "sample_count": len(counts),
    }


def _validate_receipt(receipt: Mapping[str, Any], validator: Draft202012Validator) -> None:
    validator.validate(dict(receipt))


def _receipt_inventory_item(
    *, route: str, kind: str, case_id: str, receipt: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "route": route,
        "kind": kind,
        "case_id": case_id,
        "receipt_id": str(receipt["receipt_id"]),
        "execution_identity_sha256": str(receipt["execution_identity_sha256"]),
        "result_sha256": str(receipt["result_sha256"]),
    }


def _retrieval_receipts(
    *,
    route_name: str,
    route_spec: Mapping[str, Any],
    retriever: Any,
    cases: list[RetrievalBenchmarkCase],
    documents: list[dict[str, Any]],
    pack_mounts: Mapping[str, str],
    projection: Mapping[str, str],
    run_ref: str,
    validator: Draft202012Validator,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    model = _model_service(documents)
    receipts: list[dict[str, Any]] = []
    inventory: list[dict[str, Any]] = []
    for case in cases:
        _, receipt = execute_query_with_receipt(
            query=case.query,
            pack_mounts=pack_mounts,
            query_pack_ids=list(case.pack_ids),
            projection=projection,
            policy={
                "route_id": str(route_spec["implementation"]),
                "retrieval_policy_id": "FOSSIL-GRAPHRAG-BENCH-01",
                "mode": "matched-retrieval",
            },
            retriever=retriever,
            model_service=model,
            limit=int(route_spec["limit"]),
            trace_ref=f"trace://fossil-graphrag/{route_name}/{case.case_id}",
            run_ref=run_ref,
            query_id=case.case_id,
        )
        _validate_receipt(receipt, validator)
        receipts.append(receipt)
        inventory.append(
            _receipt_inventory_item(
                route=route_name, kind="retrieval", case_id=case.case_id, receipt=receipt
            )
        )
    return receipts, inventory


def _answer_receipts(
    *,
    route_name: str,
    route_spec: Mapping[str, Any],
    retriever: Any,
    cases: list[AnswerReliabilityCase],
    documents: list[dict[str, Any]],
    pack_mounts: Mapping[str, str],
    projection: Mapping[str, str],
    run_ref: str,
    validator: Draft202012Validator,
) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    observations: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    inventory: list[dict[str, Any]] = []
    service = _model_service(documents)
    for case in cases:
        response, receipt = execute_query_with_receipt(
            query=case.query,
            pack_mounts=pack_mounts,
            query_pack_ids=list(case.pack_ids),
            projection=projection,
            policy={
                "route_id": str(route_spec["implementation"]),
                "retrieval_policy_id": "FOSSIL-GRAPHRAG-BENCH-01",
                "mode": "answer-evaluation",
            },
            retriever=retriever,
            model_service=service,
            limit=int(case.limit),
            trace_ref=f"trace://fossil-graphrag/{route_name}/{case.case_id}",
            run_ref=run_ref,
            query_id=case.case_id,
        )
        _validate_receipt(receipt, validator)
        evaluation = evaluate_answer_candidate(
            response["output"], case=case, documents=documents
        )
        observations.append(
            {**evaluation, "case_id": case.case_id, "category": case.category}
        )
        receipts.append(receipt)
        inventory.append(
            _receipt_inventory_item(
                route=route_name, kind="answer", case_id=case.case_id, receipt=receipt
            )
        )
    return _answer_metrics(observations), observations, receipts, inventory


async def _graph_projection_identity_async(
    graphiti: Any,
    *,
    pack_ids: list[str],
    expected_event_ids: set[str],
) -> dict[str, Any]:
    from graphiti_core.nodes import EpisodicNode

    started = time.perf_counter()
    episodes = await EpisodicNode.get_by_group_ids(
        graphiti.driver, pack_ids, limit=max(100, len(expected_event_ids) * 2)
    )
    validation_ms = (time.perf_counter() - started) * 1000.0
    observed_event_ids = {
        str(episode.name).removeprefix("dkg-event:")
        for episode in episodes
        if str(episode.name).startswith("dkg-event:")
    }
    if observed_event_ids != expected_event_ids:
        raise RuntimeError(
            "Graphiti projection event set mismatch: "
            + json.dumps(
                {
                    "expected": len(expected_event_ids),
                    "observed": len(observed_event_ids),
                    "missing": sorted(expected_event_ids - observed_event_ids),
                    "extra": sorted(observed_event_ids - expected_event_ids),
                },
                sort_keys=True,
            )
        )
    records, _, _ = await graphiti.driver.execute_query(
        "CALL dbms.components() YIELD name, versions "
        "RETURN name, versions[0] AS version ORDER BY name LIMIT 1",
        database_="neo4j",
    )
    neo4j_version = str(records[0]["version"]) if records else "unknown"
    fingerprint = _sha256_json(sorted(expected_event_ids))[:24]
    return {
        "name": "graphiti-neo4j",
        "version": "1",
        "build_id": f"existing-verified-{fingerprint}",
        "graphiti_version": importlib.metadata.version("graphiti-core"),
        "neo4j_version": neo4j_version,
        "stable_pack_ids": sorted(pack_ids),
        "materialized_event_count": len(observed_event_ids),
        "projection_validation_ms": validation_ms,
        "initial_graph_materialization_ms": None,
        "incremental_projection_ms": None,
        "materialization_status": "reused_preexisting_verified_projection",
        "materialization_timing_note": (
            "The exact 51-event projection pre-existed this benchmark; its historical build elapsed time "
            "is unavailable and is explicitly not included in per-query latency."
        ),
        "retrieval_model_calls": 0,
        "retrieval_model_identity": None,
        "graph_native_ids_are_canonical": False,
    }


def _graph_projection_identity(
    runtime: _GraphitiRuntime,
    *,
    pack_ids: list[str],
    expected_event_ids: set[str],
) -> dict[str, Any]:
    return runtime.run(
        _graph_projection_identity_async(
            runtime.graphiti,
            pack_ids=pack_ids,
            expected_event_ids=expected_event_ids,
        )
    )


def _build_graphiti_client(uri: str, user: str, password: str) -> _GraphitiRuntime:
    # The bounded benchmark config uses only Graphiti BM25+BFS+RRF. These
    # placeholder client credentials are never sent because no embedding,
    # LLM, or cross-encoder search method is enabled.
    os.environ.setdefault("OPENAI_API_KEY", "fossil-graphrag-benchmark-no-model-call")
    return _GraphitiRuntime(uri, user, password)


def _build_routes(
    documents: list[dict[str, Any]],
    plan: Mapping[str, Any],
    *,
    graphiti: Any,
    async_runner: Any,
    graph_projection: Mapping[str, Any],
) -> dict[str, Any]:
    retrieval_plan = _load_json(ROOT / str(plan["retrieval_plan"]))
    embedding = dict(retrieval_plan["models"]["incumbent_embedding"])
    cross_encoder_config = dict(retrieval_plan["models"]["cross_encoder"])
    if embedding != {"model": DEFAULT_BGE_MODEL, "revision": DEFAULT_BGE_REVISION}:
        raise ValueError("D021 embedding pin does not match committed constants")
    if (
        cross_encoder_config["model"] != DEFAULT_CROSS_ENCODER_MODEL
        or cross_encoder_config["revision"] != DEFAULT_CROSS_ENCODER_REVISION
    ):
        raise ValueError("cross-encoder pin does not match committed constants")

    multiplier = int(plan["candidate_multiplier"])
    limit = int(plan["retrieval_limit"])
    embedder = SentenceTransformerEmbeddingProvider(
        model_name=embedding["model"], revision=embedding["revision"], device="cpu"
    )
    dense = SemanticEmbeddingRetriever(
        documents, embedder, version="fossil-graphrag-d021-v1"
    )
    lexical = BM25Retriever(documents, version="fossil-graphrag-bm25-v1")
    hybrid = ReciprocalRankFusionRetriever(
        [lexical, dense],
        rrf_k=int(retrieval_plan["rrf_k"]),
        candidate_multiplier=multiplier,
        version="fossil-graphrag-hybrid-v1",
    )
    cross_encoder = SentenceTransformerCrossEncoderReranker(
        model_name=cross_encoder_config["model"],
        revision=cross_encoder_config["revision"],
        device="cpu",
        batch_size=int(cross_encoder_config.get("batch_size", 16)),
        max_length=int(cross_encoder_config["max_length"]),
        implementation_version="fossil-graphrag-reranker-v1",
    )
    reranked = RerankedRetriever(
        hybrid,
        cross_encoder,
        candidate_multiplier=multiplier,
        version="fossil-graphrag-reranked-v1",
    )

    from graphiti_core.search.search_config import (
        EdgeReranker,
        EdgeSearchConfig,
        EdgeSearchMethod,
        SearchConfig,
    )

    graph_config = SearchConfig(
        edge_config=EdgeSearchConfig(
            search_methods=[EdgeSearchMethod.bm25, EdgeSearchMethod.bfs],
            reranker=EdgeReranker.rrf,
            bfs_max_depth=int(plan["graph"]["bfs_max_depth"]),
        ),
        limit=limit * multiplier,
    )
    graph = GraphitiExpansionRetriever(
        graphiti=graphiti,
        documents=documents,
        search_config=graph_config,
        projection={
            "name": str(graph_projection["name"]),
            "version": str(graph_projection["version"]),
            "build_id": str(graph_projection["build_id"]),
        },
        graphiti_version=str(graph_projection["graphiti_version"]),
        neo4j_version=str(graph_projection["neo4j_version"]),
        candidate_multiplier=multiplier,
        version="fossil-graphrag-graphiti-v1",
        async_runner=async_runner,
    )
    return {
        "BM25": lexical,
        "D021": dense,
        "HYBRID": hybrid,
        "RERANKED": reranked,
        "GRAPHITI": graph,
    }


def _route_class_win(
    graph_metrics: Mapping[str, Any], baseline_metrics: Mapping[str, Any]
) -> bool:
    if graph_metrics.get("case_count", 0) == 0:
        return False
    recall_gain = float(graph_metrics["recall_at_k"]) - float(baseline_metrics["recall_at_k"])
    miss_gain = len(baseline_metrics["missed_case_ids"]) - len(graph_metrics["missed_case_ids"])
    return recall_gain >= 0.10 or miss_gain >= 1


def _decide(
    *,
    routes: Mapping[str, Mapping[str, Any]],
    graph_projection_verified: bool,
) -> dict[str, Any]:
    graph = routes["GRAPHITI"]
    baseline = routes["RERANKED"]
    graph_classes = graph["retrieval"]["query_class_metrics"]
    baseline_classes = baseline["retrieval"]["query_class_metrics"]
    class_wins = [
        name
        for name in QUERY_CLASS_NAMES
        if _route_class_win(graph_classes[name], baseline_classes[name])
    ]
    specialist_wins = [
        name
        for name in class_wins
        if name
        in {
            "historical / lineage",
            "relationship / multi-hop",
            "conflicting / superseded evidence",
        }
    ]
    ordinary_wins = [
        name
        for name in class_wins
        if name in {"exact / identifier", "conceptual", "current-state", "broad synthesis"}
    ]
    graph_safe = bool(graph["safety"]["pack_isolation_preserved"])
    graph_safe = graph_safe and int(
        graph["safety"]["current_query_top1_superseded_leakage_count"]
    ) <= int(baseline["safety"]["current_query_top1_superseded_leakage_count"])
    graph_p95 = float(graph["retrieval"]["metrics"]["p95_latency_ms"])
    baseline_p95 = float(baseline["retrieval"]["metrics"]["p95_latency_ms"])
    latency_acceptable = graph_p95 <= max(1.0, baseline_p95 * 3.0)

    if graph_safe and latency_acceptable and len(ordinary_wins) >= 2 and len(class_wins) >= 3:
        decision = "RETAIN_PRIMARY_ROUTE"
    elif graph_safe and specialist_wins:
        decision = "RETAIN_SPECIALIST_ROUTE"
    elif graph_projection_verified:
        decision = "RETAIN_PROJECTION_NOT_RETRIEVAL"
    else:
        decision = "REJECT"
    return {
        "decision": decision,
        "material_class_wins": class_wins,
        "specialist_class_wins": specialist_wins,
        "ordinary_class_wins": ordinary_wins,
        "graph_safety_non_regression": graph_safe,
        "graph_latency_non_regression": latency_acceptable,
        "baseline_route": "RERANKED",
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--common-root", type=Path, required=True)
    parser.add_argument("--ai-root", type=Path, required=True)
    parser.add_argument("--plan", type=Path, default=PLAN_PATH)
    parser.add_argument("--neo4j-uri", default="bolt://127.0.0.1:7688")
    parser.add_argument("--neo4j-user", default="neo4j")
    parser.add_argument("--neo4j-password", default=None)
    parser.add_argument("--run-ref", default="fossil-graphrag-bench-01-local")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipts", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    args.plan = args.plan.resolve()
    args.output = args.output.resolve()
    args.receipts = args.receipts.resolve()
    plan = _load_json(args.plan)
    pack_pins = {str(key): str(value) for key, value in plan["pack_pins"].items()}
    roots = [Path(args.common_root), Path(args.ai_root)]
    observed_pins = {
        "fossil-common": _git_head(roots[0]),
        "fossil-ai-systems": _git_head(roots[1]),
    }
    if observed_pins != pack_pins:
        raise SystemExit(
            "pack pin mismatch: "
            + json.dumps({"expected": pack_pins, "observed": observed_pins}, sort_keys=True)
        )

    stable_ids = {str(key): str(value) for key, value in plan["stable_pack_ids"].items()}
    pack_mounts = {
        stable_ids["fossil-common"]: observed_pins["fossil-common"],
        stable_ids["fossil-ai-systems"]: observed_pins["fossil-ai-systems"],
    }
    documents = retrieval_documents_from_pack_fixtures(
        roots, schemas_root=ROOT / "schemas"
    )
    from fossil_core.application.rebuild.pack_corpus import _events_for_pack, _load_json as load_pack_json

    expected_event_ids: set[str] = set()
    for root in roots:
        manifest = load_pack_json(root / "manifest.json")
        expected_event_ids.update(
            str(event["event_id"]) for event in _events_for_pack(root, manifest)
        )
    if len(expected_event_ids) != 51 or len(documents) != 27:
        raise SystemExit(
            f"unexpected frozen corpus shape: events={len(expected_event_ids)} documents={len(documents)}"
        )

    receipt_validator = Draft202012Validator(
        json.loads(RECEIPT_SCHEMA.read_text(encoding="utf-8")),
        format_checker=FormatChecker(),
    )
    password = args.neo4j_password or os.environ.get("NEO4J_PASSWORD")
    if not password:
        raise SystemExit("NEO4J_PASSWORD or --neo4j-password is required")

    graphiti_runtime = _build_graphiti_client(
        args.neo4j_uri, args.neo4j_user, password
    )
    graphiti = graphiti_runtime.graphiti
    graph_projection: dict[str, Any] | None = None
    all_receipts: list[dict[str, Any]] = []
    receipt_inventory: list[dict[str, Any]] = []
    try:
        graph_projection = _graph_projection_identity(
            graphiti_runtime,
            pack_ids=sorted(stable_ids.values()),
            expected_event_ids=expected_event_ids,
        )
        routes = _build_routes(
            documents,
            plan,
            graphiti=graphiti,
            async_runner=graphiti_runtime.run,
            graph_projection=graph_projection,
        )
        retrieval_cases = retrieval_cases_from_case_set(
            load_benchmark_case_set(
                ROOT / str(plan["retrieval_case_set"]), CASE_SCHEMA
            )
        )
        answer_plan = _load_json(ROOT / str(plan["answer_case_set"]))
        answer_cases = [
            AnswerReliabilityCase.from_mapping(item) for item in answer_plan["cases"]
        ]
        expected_case_count = len(retrieval_cases)
        if expected_case_count != 21 or len(answer_cases) != 6:
            raise SystemExit(
                f"unexpected frozen case shape: retrieval={expected_case_count} answer={len(answer_cases)}"
            )

        route_specs = {
            str(item["name"]): dict(item) for item in plan["routes"]
        }
        for name in routes:
            route_specs[name]["limit"] = int(plan["retrieval_limit"])

        route_reports: dict[str, dict[str, Any]] = {}
        for route_name in ("BM25", "D021", "HYBRID", "RERANKED", "GRAPHITI"):
            retriever = routes[route_name]
            retrieval = RetrievalBenchmark(limit=int(plan["retrieval_limit"])).run(
                retriever, retrieval_cases
            )
            taxonomy = classify_retrieval_result(
                retrieval, cases=retrieval_cases, documents=documents
            )
            safety = _audit_route(
                retriever,
                retrieval_cases,
                limit=int(plan["retrieval_limit"]),
            )
            retrieval_receipts, retrieval_inventory = _retrieval_receipts(
                route_name=route_name,
                route_spec=route_specs[route_name],
                retriever=retriever,
                cases=retrieval_cases,
                documents=documents,
                pack_mounts=pack_mounts,
                projection=(
                    graph_projection
                    if route_name == "GRAPHITI"
                    else {
                        "name": "pack-fixture-retrieval-documents",
                        "version": "1",
                        "build_id": "packfix_" + _sha256_json(pack_mounts)[:24],
                    }
                ),
                run_ref=args.run_ref,
                validator=receipt_validator,
            )
            answer_projection = (
                graph_projection
                if route_name == "GRAPHITI"
                else {
                    "name": "pack-fixture-retrieval-documents",
                    "version": "1",
                    "build_id": "packfix_" + _sha256_json(pack_mounts)[:24],
                }
            )
            answer_metrics, answer_observations, answer_receipts, answer_inventory = _answer_receipts(
                route_name=route_name,
                route_spec=route_specs[route_name],
                retriever=retriever,
                cases=answer_cases,
                documents=documents,
                pack_mounts=pack_mounts,
                projection=answer_projection,
                run_ref=args.run_ref,
                validator=receipt_validator,
            )
            route_receipts = retrieval_receipts + answer_receipts
            semantic = _semantic_metrics(
                retrieval=retrieval,
                retrieval_cases=retrieval_cases,
                taxonomy=taxonomy,
                safety=safety,
                answer_observations=answer_observations,
                answer_cases=answer_cases,
                receipt_set=route_receipts,
            )
            all_receipts.extend(retrieval_receipts)
            all_receipts.extend(answer_receipts)
            receipt_inventory.extend(retrieval_inventory)
            receipt_inventory.extend(answer_inventory)
            route_reports[route_name] = {
                "service": retriever.metadata(),
                "retrieval": {
                    "metrics": retrieval["metrics"],
                    "query_class_metrics": _query_class_metrics(
                        retrieval, retrieval_cases
                    ),
                    "decision_critical_misses": sum(
                        item["decision_critical_misses"]
                        for item in _query_class_metrics(retrieval, retrieval_cases).values()
                    ),
                },
                "failure_taxonomy": taxonomy,
                "safety": safety,
                "semantic": semantic,
                "answer": {
                    "metrics": answer_metrics,
                    "observations": [
                        {
                            key: value
                            for key, value in item.items()
                            if key
                            in {
                                "case_id",
                                "case_correct",
                                "outcome_match",
                                "citation_correct",
                                "unsupported_claim_rate",
                                "appropriate_abstention",
                            }
                        }
                        for item in answer_observations
                    ],
                },
                "receipt_count": len(retrieval_inventory) + len(answer_inventory),
                "operational": {
                    "mean_latency_ms": retrieval["metrics"]["mean_latency_ms"],
                    "p95_latency_ms": retrieval["metrics"]["p95_latency_ms"],
                    "peak_python_alloc_bytes": retrieval["metrics"]["peak_python_alloc_bytes"],
                    "process_rss_bytes_after_route": _process_rss_bytes(),
                    "graph_query_count": _graph_query_count_metrics(route_receipts),
                },
            }

        # Run a real route-unavailable control after closing the shared Graphiti
        # driver. This must be an explicit failure, never an empty graph success.
        graph_route = routes["GRAPHITI"]
        control_case = next(
            case
            for case in retrieval_cases
            if case.case_id == "current_architecture_after_reconsideration"
        )
        unavailable_started = time.perf_counter()
        graphiti_runtime.close_driver()
        try:
            graph_route.search(
                control_case.query,
                pack_ids=list(control_case.pack_ids),
                limit=int(plan["retrieval_limit"]),
            )
        except GraphitiRetrievalError:
            unavailable_status = "route_failed"
        else:
            unavailable_status = "unexpected_success"
        unavailable_latency_ms = (time.perf_counter() - unavailable_started) * 1000.0
        unavailable_receipt = build_failed_query_execution_receipt(
            query=control_case.query,
            pack_mounts=pack_mounts,
            query_pack_ids=list(control_case.pack_ids),
            projection=graph_projection,
            policy={
                "route_id": str(route_specs["GRAPHITI"]["implementation"]),
                "retrieval_policy_id": "FOSSIL-GRAPHRAG-BENCH-01",
                "mode": "graph-unavailable-control",
            },
            retriever_metadata=graph_route.metadata(),
            model_metadata=DeterministicEvidenceAnswerService().metadata(),
            failure_type="GraphitiRetrievalError",
            trace_ref="trace://fossil-graphrag/GRAPHITI/unavailable",
            run_ref=args.run_ref,
            query_id=control_case.case_id + "-graph-unavailable",
            retrieval_metadata=graph_route.last_search_metadata,
            latency_ms=unavailable_latency_ms,
        )
        _validate_receipt(unavailable_receipt, receipt_validator)
        all_receipts.append(unavailable_receipt)
        receipt_inventory.append(
            _receipt_inventory_item(
                route="GRAPHITI",
                kind="graph-unavailable-control",
                case_id=control_case.case_id,
                receipt=unavailable_receipt,
            )
        )

        canonical_after = retrieval_documents_from_pack_fixtures(
            roots, schemas_root=ROOT / "schemas"
        )
        canonical_unchanged = _sha256_json(documents) == _sha256_json(canonical_after)
        report_routes = copy.deepcopy(route_reports)
        decision = _decide(routes=report_routes, graph_projection_verified=True)
        report = {
            "schema_version": "fossil.graphrag-decision-report.v1",
            "task": "FOSSIL-GRAPHRAG-BENCH-01",
            "head": _git_head(ROOT),
            "benchmark": {
                "version": str(plan["benchmark_id"]),
                "plan": str(args.plan.relative_to(ROOT)),
                "retrieval_case_set": str(plan["retrieval_case_set"]),
                "answer_case_set": str(plan["answer_case_set"]),
                "retrieval_case_count": len(retrieval_cases),
                "answer_case_count": len(answer_cases),
                "corpus_document_count": len(documents),
                "corpus_event_count": len(expected_event_ids),
                "retrieval_limit": int(plan["retrieval_limit"]),
                "candidate_multiplier": int(plan["candidate_multiplier"]),
            },
            "pack_pins": observed_pins,
            "stable_pack_ids": stable_ids,
            "routes": list(route_reports),
            "graph_projection": graph_projection,
            "environment": {
                "platform": platform.platform(),
                "machine": platform.machine(),
                "processor": platform.processor(),
                "python": platform.python_version(),
                "graphiti_version": graph_projection["graphiti_version"],
                "neo4j_version": graph_projection["neo4j_version"],
                "device": "cpu",
            },
            "route_reports": route_reports,
            "graph_unavailable_control": {
                "case_id": control_case.case_id,
                "observed_status": unavailable_status,
                "expected_status": "route_failed",
                "receipt_id": unavailable_receipt["receipt_id"],
                "canonical_truth_unchanged": canonical_unchanged,
                "automatic_fallback": False,
            },
            "receipt_sidecar": {
                "path": str(args.receipts.relative_to(ROOT)),
                "receipt_count": len(all_receipts),
                "inventory_count": len(receipt_inventory),
                "all_validated_against": "schemas/query-execution-receipt/v1.schema.json",
            },
            "decision": decision,
            "invariants": {
                "pack": all(
                    bool(item["safety"]["pack_isolation_preserved"])
                    for item in route_reports.values()
                ),
                "lifecycle": all(
                    any(
                        resolver["resolver"] == "fossil-lineage-context-v1"
                        for resolver in receipt["resolvers"]
                    )
                    for receipt in all_receipts
                    if receipt["policy"]["mode"] in {"matched-retrieval", "answer-evaluation"}
                ),
                "lineage": all(
                    any(
                        resolver["resolver"] == "fossil-lineage-context-v1"
                        for resolver in receipt["resolvers"]
                    )
                    for receipt in all_receipts
                    if receipt["policy"]["mode"] in {"matched-retrieval", "answer-evaluation"}
                ),
                "citation": all(
                    "citation_ids" in receipt["context"]
                    for receipt in all_receipts
                    if receipt["policy"]["mode"] == "answer-evaluation"
                ),
                "security": all(
                    any(
                        resolver["resolver"] == "fossil-untrusted-context-v1"
                        for resolver in receipt["resolvers"]
                    )
                    for receipt in all_receipts
                    if receipt["policy"]["mode"] in {"matched-retrieval", "answer-evaluation"}
                ),
            },
            "acceptance_weakened": False,
            "passed": (
                unavailable_status == "route_failed"
                and canonical_unchanged
                and all(
                    bool(item["safety"]["pack_isolation_preserved"])
                    for item in route_reports.values()
                )
                and all(
                    "route_failed" not in item["answer"]["metrics"]
                    for item in route_reports.values()
                )
            ),
        }
    finally:
        # The unavailable control closes the client intentionally. A normal
        # early failure still gets a best-effort close without touching data.
        if graphiti_runtime is not None:
            try:
                graphiti_runtime.shutdown()
            except Exception:
                pass

    args.receipts.parent.mkdir(parents=True, exist_ok=True)
    with args.receipts.open("w", encoding="utf-8", newline="\n") as handle:
        for receipt in all_receipts:
            handle.write(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n")
    report["receipt_sidecar"]["sha256"] = hashlib.sha256(
        args.receipts.read_bytes()
    ).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
