from __future__ import annotations

import argparse
import copy
import hashlib
import json
import platform
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker

from fossil_core.answer_eval import DeterministicEvidenceAnswerService
from fossil_core.answer_pipeline import LineageResolvedModelService
from fossil_core.application.query.visibility import (
    CallerVisibility,
    RetrievalVisibilityPolicy,
    SecurityFilteredRetriever,
    VisibilityDenied,
)
from fossil_core.application.rebuild.pack_corpus import retrieval_documents_from_pack_fixtures
from fossil_core.context_security import UntrustedContextModelService
from fossil_core.execution_receipt import execute_query_with_receipt
from fossil_core.pack_fixture import validate_pack_fixtures
from fossil_core.real_retrieval import (
    DEFAULT_BGE_MODEL,
    DEFAULT_BGE_REVISION,
    DEFAULT_CROSS_ENCODER_MODEL,
    DEFAULT_CROSS_ENCODER_REVISION,
    OptionalRetrievalDependencyUnavailable,
    ReciprocalRankFusionRetriever,
    RerankedRetriever,
    SentenceTransformerCrossEncoderReranker,
    SentenceTransformerEmbeddingProvider,
)
from fossil_core.semantic_retriever import SemanticEmbeddingRetriever
from fossil_core.services import BM25Retriever


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "benchmarks" / "post-gate2" / "retrieval-security-fixtures-v1.json"
RECEIPT_SCHEMA = ROOT / "schemas" / "query-execution-receipt" / "v1.schema.json"


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _git_head(root: Path) -> str:
    return subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _serializable(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _build_routes(documents: list[dict[str, Any]]) -> dict[str, Any]:
    embedding = SentenceTransformerEmbeddingProvider(
        model_name=DEFAULT_BGE_MODEL,
        revision=DEFAULT_BGE_REVISION,
        device="cpu",
        implementation_version="retrieval-security-final-d021-v1",
    )
    dense = SemanticEmbeddingRetriever(documents, embedding, version="retrieval-security-final-d021-v1")
    lexical = BM25Retriever(documents, version="retrieval-security-final-bm25-v1")
    hybrid = ReciprocalRankFusionRetriever(
        [lexical, dense],
        rrf_k=60,
        candidate_multiplier=4,
        version="retrieval-security-final-rrf-v1",
    )
    reranker = SentenceTransformerCrossEncoderReranker(
        model_name=DEFAULT_CROSS_ENCODER_MODEL,
        revision=DEFAULT_CROSS_ENCODER_REVISION,
        device="cpu",
        batch_size=16,
        max_length=512,
        implementation_version="retrieval-security-final-v1",
    )
    fixed = RerankedRetriever(
        hybrid,
        reranker,
        candidate_multiplier=4,
        version="retrieval-security-final-raw-reranked-v1",
    )
    return {
        "bm25": lexical,
        "d021_dense": dense,
        "hybrid_rrf": hybrid,
        "reranked_hybrid": fixed,
    }


def _secure_routes(documents: list[dict[str, Any]], caller: CallerVisibility) -> tuple[dict[str, Any], dict[str, int]]:
    policy = RetrievalVisibilityPolicy()
    visible, denied_counts = policy.visible_documents(documents, caller)
    routes = {
        name: SecurityFilteredRetriever(route, policy=policy, caller=caller)
        for name, route in _build_routes(visible).items()
    }
    return routes, denied_counts


def _assert_no_protected(value: Any, protected: list[str]) -> list[str]:
    payload = _serializable(value)
    return [marker for marker in protected if marker and marker in payload]


def _main(args: argparse.Namespace) -> int:
    fixture = _load_json(args.fixture)
    documents = [copy.deepcopy(dict(item)) for item in fixture["documents"]]
    cases = [dict(item) for item in fixture["cases"]]
    policy = RetrievalVisibilityPolicy()
    caller = CallerVisibility(
        principal_id="alice",
        readable_pack_ids=frozenset({fixture["pack_ids"]["pack_a"]}),
        allowed_sensitivity=frozenset({"internal"}),
    )
    all_ids = {str(item["id"]) for item in documents}
    visible, denied_counts = policy.visible_documents(documents, caller)
    visible_ids = {str(item["id"]) for item in visible}
    denied_ids = all_ids - visible_ids
    if not visible_ids or not denied_ids:
        raise AssertionError("security fixture must have both visible and denied documents")

    # The unfiltered probe intentionally demonstrates that adversarial material is attractive
    # to ordinary retrieval. It is never used as a caller-visible route.
    try:
        unfiltered = _build_routes(documents)
        secure, secure_denied_counts = _secure_routes(documents, caller)
    except OptionalRetrievalDependencyUnavailable as exc:
        report = {
            "schema_version": "fossil.retrieval-security-proof.v1",
            "status": "BLOCKED_MODEL_LOAD",
            "error_type": type(exc).__name__,
            "message": str(exc),
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return 2

    if denied_counts != secure_denied_counts:
        raise AssertionError("security denial counts changed while rebuilding routes")
    route_names = tuple(secure)
    protected_markers = [
        str(item["id"])
        for item in documents
        if str(item["id"]) in denied_ids
    ] + [
        str(item["text"])
        for item in documents
        if str(item["id"]) in denied_ids
    ] + [
        str(item["citation"]["citation_id"])
        for item in documents
        if str(item["id"]) in denied_ids
    ]
    observations: list[dict[str, Any]] = []
    failures: list[str] = []
    unfiltered_adversarial_hits = 0

    for case in cases:
        case_id = str(case["case_id"])
        query = str(case["query"])
        expected = {str(item) for item in case["expected_visible_ids"]}
        case_denied = {str(item) for item in case["denied_ids"]}
        unfiltered_probe: dict[str, Any] = {}
        for route_name, route in unfiltered.items():
            probe = route.search(query, pack_ids=[fixture["pack_ids"]["pack_a"], fixture["pack_ids"]["pack_b"]], limit=8)
            ids = [str(item["id"]) for item in probe]
            unfiltered_probe[route_name] = {
                "ids": ids,
                "denied_candidate_ids": sorted(case_denied & set(ids)),
                "top1": ids[0] if ids else None,
            }
            if case_denied & set(ids):
                unfiltered_adversarial_hits += 1

        for route_name, route in secure.items():
            try:
                result = route.search(query, pack_ids=[fixture["pack_ids"]["pack_a"]], limit=8)
                returned_ids = [str(item["id"]) for item in result]
                leaked_ids = sorted(set(returned_ids) & denied_ids)
                leaked_case_ids = sorted(set(returned_ids) & case_denied)
                unexpected_pack_ids = sorted(
                    {str(item.get("pack_id", "")) for item in result}
                    - caller.readable_pack_ids
                )
                markers = _assert_no_protected(result, protected_markers)
                failures.extend(
                    f"{case_id}/{route_name}:{reason}"
                    for reason, bad in (
                        ("denied_id", leaked_ids),
                        ("case_denied_id", leaked_case_ids),
                        ("foreign_pack", unexpected_pack_ids),
                        ("protected_marker", markers),
                    )
                    if bad
                )
            except Exception as exc:  # pragma: no cover - report exact unexpected route failures
                returned_ids = []
                failures.append(f"{case_id}/{route_name}:unexpected:{type(exc).__name__}:{exc}")
            observations.append(
                {
                    "case_id": case_id,
                    "route": route_name,
                    "surface": str(case["surface"]),
                    "returned_ids": returned_ids,
                    "expected_visible_ids": sorted(expected),
                    "denied_ids": sorted(case_denied),
                    "expected_visible_found": sorted(expected & set(returned_ids)),
                    "unfiltered_probe": unfiltered_probe[route_name],
                }
            )

    # Direct reads, citations, exports, and explicit pack denial are independent of rank.
    allowed_doc = next(item for item in documents if item["id"] == "sec_allow_exact")
    denied_doc = next(item for item in documents if item["id"] == "sec_deny_exact")
    direct_checks: dict[str, Any] = {}
    try:
        policy.read(denied_doc["id"], documents, caller)
        failures.append("direct_read_denied_id_returned")
    except VisibilityDenied:
        direct_checks["denied_direct_read"] = "blocked"
    if policy.read(allowed_doc["id"], documents, caller)["id"] != allowed_doc["id"]:
        failures.append("allowed_direct_read_missing")
    try:
        policy.authorize_citation(
            denied_doc["citation"], document_id=denied_doc["id"], documents=documents, caller=caller
        )
        failures.append("denied_citation_authorized")
    except VisibilityDenied:
        direct_checks["denied_citation"] = "blocked"
    if policy.authorize_citation(
        allowed_doc["citation"], document_id=allowed_doc["id"], documents=documents, caller=caller
    )["citation_id"] != allowed_doc["citation"]["citation_id"]:
        failures.append("allowed_citation_missing")
    if policy.export_document(denied_doc, caller) is not None:
        failures.append("denied_export_returned")
    exported = policy.export_documents(documents, caller)
    exported_ids = {str(item["id"]) for item in exported}
    export_markers = _assert_no_protected(exported, protected_markers)
    if denied_ids & exported_ids or export_markers:
        failures.append(f"export_leak:{sorted(denied_ids & exported_ids)}:{export_markers}")
    direct_checks["allowed_export_count"] = len(exported)
    direct_checks["denied_export"] = "blocked"
    direct_checks["requested_foreign_pack"] = "blocked"
    try:
        secure["bm25"].search("ALPHA", pack_ids=[fixture["pack_ids"]["pack_b"]], limit=3)
        failures.append("foreign_pack_request_allowed")
    except VisibilityDenied:
        pass

    # Disposable projection/index rebuild: suppressed and denied records may exist in the
    # projection, but the caller-scoped rebuild is filtered before any route sees them.
    rebuilt_visible, rebuilt_denied_counts = policy.visible_documents(copy.deepcopy(documents), caller)
    rebuilt_secure, rebuilt_counts = _secure_routes(copy.deepcopy(documents), caller)
    if {item["id"] for item in rebuilt_visible} != visible_ids or rebuilt_denied_counts != denied_counts:
        failures.append("projection_rebuild_visibility_drift")
    if rebuilt_counts != denied_counts:
        failures.append("projection_rebuild_denial_count_drift")
    rebuild_leaks = []
    for route_name, route in rebuilt_secure.items():
        results = route.search("ALPHA suppressed redacted evidence", pack_ids=[fixture["pack_ids"]["pack_a"]], limit=8)
        rebuild_leaks.extend(str(item["id"]) for item in results if str(item["id"]) in denied_ids)
    if rebuild_leaks:
        failures.append(f"projection_rebuild_route_leak:{sorted(set(rebuild_leaks))}")

    # Answer/context and receipt boundary: only secured candidates enter the existing
    # lineage/context security pipeline, and receipts contain IDs/metadata, never source text.
    receipts: list[dict[str, Any]] = []
    model_service = UntrustedContextModelService(
        LineageResolvedModelService(
            DeterministicEvidenceAnswerService(),
            documents=visible,
        ),
        documents=visible,
    )
    for route_name in route_names:
        _, receipt = execute_query_with_receipt(
            query="ALPHA current architecture",
            pack_mounts={fixture["pack_ids"]["pack_a"]: "security-fixture-revision-a"},
            query_pack_ids=[fixture["pack_ids"]["pack_a"]],
            projection={"name": "security-disposable-projection", "version": "1", "build_id": "rebuild-alice-v1"},
            policy={"route_id": f"security-{route_name}", "retrieval_policy_id": "RETRIEVAL_SECURITY_FINAL_V1", "mode": "caller-filtered"},
            retriever=secure[route_name],
            model_service=model_service,
            limit=8,
            trace_ref=f"trace://retrieval-security-final/{route_name}",
            run_ref=args.run_ref,
        )
        receipts.append(receipt)
        receipt_markers = _assert_no_protected(receipt, protected_markers)
        if receipt_markers:
            failures.append(f"receipt_leak/{route_name}:{receipt_markers}")
        receipt_candidate_ids = {
            str(item.get("id", "")) for item in receipt["retrieval"]["candidates"]
        }
        if receipt_candidate_ids & denied_ids:
            failures.append(f"receipt_denied_candidate/{route_name}")

    validator = Draft202012Validator(_load_json(RECEIPT_SCHEMA), format_checker=FormatChecker())
    receipt_errors = [
        f"receipt[{index}] {error.json_path}: {error.message}"
        for index, receipt in enumerate(receipts)
        for error in validator.iter_errors(receipt)
    ]
    failures.extend(f"receipt_schema:{error}" for error in receipt_errors)
    receipt_bytes = "".join(_serializable(receipt) + "\n" for receipt in receipts).encode("utf-8")
    args.receipts.parent.mkdir(parents=True, exist_ok=True)
    args.receipts.write_bytes(receipt_bytes)

    # A compact, non-sensitive result summary is the only report output.
    report = {
        "schema_version": "fossil.retrieval-security-proof.v1",
        "benchmark_id": "FOSSIL-RETRIEVAL-SECURITY-FINAL-01",
        "status": "PASS" if not failures else "FAIL",
        "starting_sha": args.starting_sha,
        "execution_sha": args.execution_sha,
        "retained_policy": {
            "representation": "RAW",
            "embedding": "D021 / BAAI bge-small-en-v1.5 pinned revision",
            "lexical": "BM25",
            "fusion": "deterministic hybrid/RRF",
            "reranker": "retained pinned cross-encoder",
            "normal_policy": "RAW_RERANKED",
            "routing": "REJECT_ROUTING",
            "llm_planner": "DISABLED",
            "contextual_enrichment": "RETAIN_RAW / not promoted",
            "graphiti": "RETAIN_PROJECTION_NOT_RETRIEVAL",
            "embedding_ladder": "STOP_MODEL_LADDER",
            "security_boundary": "PASS" if not failures else "FAIL",
        },
        "routes_exercised": list(route_names),
        "authorization_model": {
            "principal": caller.principal_id,
            "readable_pack_ids": sorted(caller.readable_pack_ids),
            "allowed_sensitivity": sorted(caller.allowed_sensitivity),
            "filtering": "before projection/index construction plus fail-closed result defense",
            "missing_security_metadata": "fail_closed",
        },
        "fixture_counts": {
            "document_count": len(documents),
            "visible_document_count": len(visible),
            "denied_document_count": len(denied_ids),
            "case_count": len(cases),
            "route_case_observation_count": len(observations),
            "unfiltered_adversarial_probe_hits": unfiltered_adversarial_hits,
            "denial_reasons": denied_counts,
        },
        "direct_boundary_checks": direct_checks,
        "projection_rebuild": {
            "disposable_projection_contains_suppressed": True,
            "caller_visible_ids_stable": {item["id"] for item in rebuilt_visible} == visible_ids,
            "route_leaks": sorted(set(rebuild_leaks)),
        },
        "observations": observations,
        "receipt_contract": {
            "receipt_count": len(receipts),
            "schema_version": "fossil.query-execution-receipt.v1",
            "schema_errors": receipt_errors,
            "valid": not receipt_errors,
            "sidecar_sha256": _sha256(receipt_bytes),
        },
        "environment": {"platform": platform.platform(), "python": platform.python_version()},
        "failures": failures,
        "residual_risks": [
            "This proof establishes structural caller-boundary enforcement; it does not claim that natural-language model behavior is universally safe.",
            "Graphiti/Neo4j remains a disposable projection and is not granted evidence authority.",
        ],
        "canonical_semantics_unchanged": True,
        "implementation_change_required": True,
        "next": "campaign-closeout-if-pass",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "failures": len(failures), "receipts": len(receipts), "receipt_sha256": report["receipt_contract"]["sidecar_sha256"]}, sort_keys=True))
    return 0 if report["status"] == "PASS" else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the final deterministic FOSSIL retrieval security proof.")
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    parser.add_argument("--common-root", type=Path, required=True)
    parser.add_argument("--ai-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipts", type=Path, required=True)
    parser.add_argument("--starting-sha", required=True)
    parser.add_argument("--execution-sha", required=True)
    parser.add_argument("--run-ref", default="fossil-retrieval-security-final-01")
    args = parser.parse_args()
    expected = {
        "fossil-common": "d583005dce06dbb499c3c0de5c22b899655eb8d2",
        "fossil-ai-systems": "84accd2ee895663990e82ca5b79b592cb503db24",
    }
    observed = {"fossil-common": _git_head(args.common_root), "fossil-ai-systems": _git_head(args.ai_root)}
    if observed != expected:
        raise SystemExit(f"pack pin mismatch: expected {expected}, observed {observed}")
    audit = validate_pack_fixtures([args.common_root, args.ai_root], schemas_root=ROOT / "schemas")
    if audit.event_count != 51 or len(retrieval_documents_from_pack_fixtures([args.common_root, args.ai_root], schemas_root=ROOT / "schemas")) != 27:
        raise SystemExit("frozen canonical pack corpus mismatch")
    return _main(args)


if __name__ == "__main__":
    raise SystemExit(main())
