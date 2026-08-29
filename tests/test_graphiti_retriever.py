from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from fossil_core.adapters.graph.graphiti_retriever import (
    GRAPHITI_EXPANSION_RESOLVER,
    GraphitiExpansionRetriever,
    GraphitiProjectionBoundaryError,
    GraphitiRetrievalError,
)
from fossil_core.application.query.receipt import (
    build_failed_query_execution_receipt,
    execute_query_with_receipt,
)
from fossil_core.answer_eval import DeterministicEvidenceAnswerService
from fossil_core.answer_pipeline import LineageResolvedModelService
from fossil_core.context_security import UntrustedContextModelService


PACK = "pack_f024177f89a5442db84171c3dd7f58e5"
OTHER_PACK = "pack_269099f7b2ba43b7a99b9427d64092de"


class FakeGraphiti:
    def __init__(self, result=None, error: Exception | None = None):
        self.result = result
        self.error = error
        self.calls: list[dict] = []
        self.driver = object()

    async def search_(self, query, *, config, group_ids):
        self.calls.append({"query": query, "config": config, "group_ids": group_ids})
        if self.error:
            raise self.error
        return self.result


async def references(uuids: list[str]):
    return {
        "episode-current": {"event_id": "evt-current", "group_id": PACK},
        "episode-relation": {"event_id": "evt-relation", "group_id": PACK},
    }


def document(identifier: str, event_id: str, kind: str = "claim"):
    return {
        "id": identifier,
        "pack_id": PACK,
        "text": identifier,
        "document_type": kind,
        "proposed_event_id": event_id,
        "current_state": "supported",
    }


def cited_document(identifier: str, event_id: str):
    value = document(identifier, event_id)
    value["citation"] = {
        "schema_version": "fossil.citation.v1",
        "citation_id": f"cite_{identifier}",
        "snapshot_id": "snap_fixture",
        "artifact_id": "art_fixture",
        "byte_start": 0,
        "byte_end": 1,
        "passage_hash": {"algorithm": "sha256", "digest": "a" * 64},
    }
    return value


def retriever(graphiti):
    return GraphitiExpansionRetriever(
        graphiti=graphiti,
        documents=[
            document("clm_current", "evt-current"),
            document("rel_relation", "evt-relation", "relation"),
        ],
        search_config="bounded-edge-bfs",
        projection={
            "name": "graphiti-neo4j",
            "version": "1",
            "build_id": "graph-build",
        },
        graphiti_version="0.29.3",
        neo4j_version="5.26.30",
        episode_reference_resolver=references,
    )


def test_graph_results_resolve_to_canonical_ids_and_preserve_scope():
    graph = FakeGraphiti(
        SimpleNamespace(
            edges=[
                SimpleNamespace(
                    uuid="graph-edge-1",
                    group_id=PACK,
                    episodes=["episode-current"],
                ),
                SimpleNamespace(
                    uuid="graph-edge-2",
                    group_id=PACK,
                    episodes=["episode-relation"],
                ),
            ],
            edge_reranker_scores=[2.0, 1.0],
        )
    )
    service = retriever(graph)

    results = service.search("architecture relationship", pack_ids=[PACK], limit=2)

    assert [item["id"] for item in results] == ["clm_current", "rel_relation"]
    assert all(item["id"] not in {"graph-edge-1", "episode-current"} for item in results)
    assert graph.calls[0]["group_ids"] == [PACK]
    assert service.last_search_metadata["resolved_canonical_ids"] == [
        "clm_current",
        "rel_relation",
    ]
    assert results[0]["graph_expansion"]["resolver"] == GRAPHITI_EXPANSION_RESOLVER


def test_graph_scope_escape_fails_closed():
    graph = FakeGraphiti(
        SimpleNamespace(
            edges=[
                SimpleNamespace(
                    uuid="foreign-edge",
                    group_id=OTHER_PACK,
                    episodes=["episode-current"],
                )
            ],
            edge_reranker_scores=[1.0],
        )
    )

    with pytest.raises(GraphitiProjectionBoundaryError):
        retriever(graph).search("scope", pack_ids=[PACK], limit=1)


def test_graph_failure_is_explicit_and_does_not_return_empty_success():
    graph = FakeGraphiti(error=ConnectionError("neo4j unavailable"))

    with pytest.raises(GraphitiRetrievalError):
        retriever(graph).search("architecture", pack_ids=[PACK], limit=1)


def test_async_api_rejects_sync_call_inside_event_loop():
    graph = FakeGraphiti(
        SimpleNamespace(edges=[], edge_reranker_scores=[])
    )
    service = retriever(graph)

    async def run():
        with pytest.raises(RuntimeError, match="inside an event loop"):
            service.search("architecture", pack_ids=[PACK], limit=1)

    asyncio.run(run())


def test_graph_expansion_metadata_is_recorded_in_v1_receipt():
    graph = FakeGraphiti(
        SimpleNamespace(
            edges=[
                SimpleNamespace(
                    uuid="graph-edge-1",
                    group_id=PACK,
                    episodes=["episode-current"],
                )
            ],
            edge_reranker_scores=[1.0],
        )
    )
    service = GraphitiExpansionRetriever(
        graphiti=graph,
        documents=[cited_document("clm_current", "evt-current")],
        search_config="bounded-edge-bfs",
        projection={"name": "graphiti-neo4j", "version": "1", "build_id": "build"},
        graphiti_version="0.29.3",
        neo4j_version="5.26.30",
        episode_reference_resolver=references,
    )
    model = UntrustedContextModelService(
        LineageResolvedModelService(
            DeterministicEvidenceAnswerService(),
            documents=[cited_document("clm_current", "evt-current")],
        ),
        documents=[cited_document("clm_current", "evt-current")],
    )

    _, receipt = execute_query_with_receipt(
        query="architecture",
        pack_mounts={PACK: "revision"},
        query_pack_ids=[PACK],
        projection={"name": "graphiti-neo4j", "version": "1", "build_id": "build"},
        policy={"route_id": "graph", "retrieval_policy_id": "D021-evaluation", "mode": "test"},
        retriever=service,
        model_service=model,
        limit=1,
        trace_ref="trace://graph",
        run_ref="test",
    )

    assert receipt["retrieval"]["candidates"][0]["id"] == "clm_current"
    assert receipt["resolvers"][0]["resolver"] == GRAPHITI_EXPANSION_RESOLVER
    assert receipt["resolvers"][0]["resolved_ids"] == ["clm_current"]
    assert receipt["resolvers"][0]["diagnostics"]["edge_uuids"] == ["graph-edge-1"]
    schema = json.loads(
        (Path(__file__).parents[1] / "schemas" / "query-execution-receipt" / "v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(receipt)


def test_failed_graph_route_receipt_is_explicit_and_schema_valid():
    graph_metadata = retriever(FakeGraphiti()).metadata()
    model_metadata = DeterministicEvidenceAnswerService().metadata()
    receipt = build_failed_query_execution_receipt(
        query="architecture",
        pack_mounts={PACK: "revision"},
        query_pack_ids=[PACK],
        projection={"name": "graphiti-neo4j", "version": "1", "build_id": "build"},
        policy={"route_id": "graph", "retrieval_policy_id": "D021-evaluation", "mode": "test"},
        retriever_metadata=graph_metadata,
        model_metadata=model_metadata,
        failure_type="GraphitiRetrievalError",
        trace_ref="trace://graph-failure",
        run_ref="test",
        retrieval_metadata={
            "resolver": GRAPHITI_EXPANSION_RESOLVER,
            "status": "failed",
            "error_type": "GraphitiRetrievalError",
            "resolved_canonical_ids": [],
            "returned_canonical_ids": [],
        },
    )

    assert receipt["result"]["outcome"] == "route_failed"
    assert receipt["result"]["abstained"] is True
    assert receipt["retrieval"]["candidate_count"] == 0
    assert receipt["resolvers"][0]["resolver"] == GRAPHITI_EXPANSION_RESOLVER
    schema = json.loads(
        (Path(__file__).parents[1] / "schemas" / "query-execution-receipt" / "v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(receipt)
