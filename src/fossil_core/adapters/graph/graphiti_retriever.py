from __future__ import annotations

import asyncio
import copy
import json
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from fossil_core.services import ServiceMetadata


GRAPHITI_EXPANSION_RESOLVER = "fossil-graphiti-expansion-v1"

EpisodeReferenceResolver = Callable[
    [list[str]], Awaitable[Mapping[str, Mapping[str, str]]]
]
AsyncRunner = Callable[[Awaitable[Any]], Any]


class GraphitiRetrievalError(RuntimeError):
    """A graph candidate route failed; callers must not silently fall back."""


class GraphitiProjectionBoundaryError(GraphitiRetrievalError):
    """Graph output could not be proven to remain inside the requested scope."""


def _value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(key, default)
    return getattr(item, key, default)


class GraphitiExpansionRetriever:
    """Expose bounded Graphiti edge search as a FOSSIL retriever.

    Graphiti and Neo4j are used only as a projection. A Graphiti edge is never a
    FOSSIL identity: its episode references are resolved through Graphiti's
    supported episodic-node API, then through the durable event-to-document map
    supplied by the validated FOSSIL projection. Unknown or out-of-scope graph
    output cannot become retrieval context.
    """

    def __init__(
        self,
        *,
        graphiti: Any,
        documents: Sequence[Mapping[str, Any]],
        search_config: Any,
        projection: Mapping[str, str],
        graphiti_version: str,
        neo4j_version: str,
        candidate_multiplier: int = 4,
        version: str = "1",
        episode_reference_resolver: EpisodeReferenceResolver | None = None,
        async_runner: AsyncRunner | None = None,
    ) -> None:
        if candidate_multiplier < 1:
            raise ValueError("graph candidate multiplier must be positive")
        projection_value = {
            key: str(projection.get(key, ""))
            for key in ("name", "version", "build_id")
        }
        if not all(projection_value.values()):
            raise ValueError("graph retriever requires a complete projection identity")
        self.graphiti = graphiti
        self.documents = [copy.deepcopy(dict(document)) for document in documents]
        self.search_config = search_config
        self.projection = projection_value
        self.graphiti_version = str(graphiti_version)
        self.neo4j_version = str(neo4j_version)
        self.candidate_multiplier = int(candidate_multiplier)
        self.version = str(version)
        self.episode_reference_resolver = episode_reference_resolver
        self.async_runner = async_runner
        self.last_search_metadata: dict[str, Any] = {}

        self._documents_by_event: dict[str, list[dict[str, Any]]] = {}
        for document in self.documents:
            event_id = str(document.get("proposed_event_id", ""))
            if event_id:
                self._documents_by_event.setdefault(event_id, []).append(
                    copy.deepcopy(document)
                )

    def metadata(self) -> dict[str, Any]:
        return ServiceMetadata(
            kind="retriever",
            provider="graphiti-core",
            provider_version=self.graphiti_version,
            implementation="graphiti-neo4j-bounded-expansion",
            implementation_version=self.version,
            model_id=None,
            local=True,
            estimated_cost_per_call_usd=0.0,
            runtime={
                "neo4j_version": self.neo4j_version,
                "projection": json.dumps(
                    self.projection, sort_keys=True, separators=(",", ":")
                ),
                "search_config": str(self.search_config),
                "search_api": "Graphiti.search_",
                "search_scope": "group_ids=FOSSIL_pack_ids",
                "graph_expansion_resolver": GRAPHITI_EXPANSION_RESOLVER,
                "graph_native_ids_are_canonical": "false",
                "candidate_multiplier": str(self.candidate_multiplier),
            },
        ).as_dict()

    async def _resolve_episode_references(
        self, episode_uuids: list[str]
    ) -> Mapping[str, Mapping[str, str]]:
        if self.episode_reference_resolver is not None:
            return await self.episode_reference_resolver(episode_uuids)

        try:
            from graphiti_core.nodes import EpisodicNode

            episodes = await EpisodicNode.get_by_uuids(
                self.graphiti.driver, episode_uuids
            )
        except Exception as exc:  # pragma: no cover - exercised by live control
            raise GraphitiRetrievalError(
                "Graphiti episodic-node resolution failed"
            ) from exc

        references: dict[str, dict[str, str]] = {}
        for episode in episodes:
            name = str(_value(episode, "name", ""))
            prefix = "dkg-event:"
            if not name.startswith(prefix):
                continue
            event_id = name.removeprefix(prefix)
            if not event_id:
                continue
            references[str(_value(episode, "uuid", ""))] = {
                "event_id": event_id,
                "group_id": str(_value(episode, "group_id", "")),
            }
        return references

    async def search_async(
        self,
        query: str,
        *,
        pack_ids: list[str],
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        if not str(query).strip():
            raise ValueError("graph retrieval query must be non-empty")
        if not pack_ids:
            raise ValueError("graph retrieval requires at least one pack")
        if limit < 1:
            raise ValueError("graph retrieval limit must be positive")

        allowed_packs = {str(pack_id) for pack_id in pack_ids}
        graph_limit = max(int(limit), int(limit) * self.candidate_multiplier)
        self.last_search_metadata = {
            "resolver": GRAPHITI_EXPANSION_RESOLVER,
            "status": "started",
            "requested_pack_ids": sorted(allowed_packs),
            "graph_search_limit": graph_limit,
        }

        try:
            search_result = await self.graphiti.search_(
                str(query),
                config=self.search_config,
                group_ids=list(pack_ids),
            )
        except Exception as exc:  # pragma: no cover - exercised by live control
            self.last_search_metadata = {
                "resolver": GRAPHITI_EXPANSION_RESOLVER,
                "status": "failed",
                "requested_pack_ids": sorted(allowed_packs),
                "graph_search_limit": graph_limit,
                "error_type": type(exc).__name__,
            }
            raise GraphitiRetrievalError("Graphiti/Neo4j graph search failed") from exc

        edges_value = _value(search_result, "edges", None)
        if edges_value is None and isinstance(search_result, list):
            edges = list(search_result)
        else:
            edges = list(edges_value or [])
        edge_scores = list(_value(search_result, "edge_reranker_scores", []) or [])

        episode_uuids: list[str] = []
        edge_records: list[dict[str, Any]] = []
        for edge_rank, edge in enumerate(edges, start=1):
            group_id = str(_value(edge, "group_id", ""))
            if group_id not in allowed_packs:
                self.last_search_metadata = {
                    "resolver": GRAPHITI_EXPANSION_RESOLVER,
                    "status": "failed",
                    "requested_pack_ids": sorted(allowed_packs),
                    "error_type": "GraphitiProjectionBoundaryError",
                    "reason": "graph result escaped requested pack scope",
                }
                raise GraphitiProjectionBoundaryError(
                    "Graphiti result escaped the requested pack scope"
                )

            score_value = edge_scores[edge_rank - 1] if edge_rank <= len(edge_scores) else None
            try:
                score = float(score_value)
            except (TypeError, ValueError):
                score = 0.0
            if not math.isfinite(score):
                score = 0.0
            if not edge_scores:
                score = 1.0 / edge_rank

            edge_episode_uuids = [
                str(identifier)
                for identifier in (_value(edge, "episodes", []) or [])
                if str(identifier)
            ]
            episode_uuids.extend(edge_episode_uuids)
            edge_records.append(
                {
                    "edge_uuid": str(_value(edge, "uuid", "")),
                    "group_id": group_id,
                    "graph_rank": edge_rank,
                    "graph_score": score,
                    "episode_uuids": sorted(set(edge_episode_uuids)),
                }
            )

        unique_episode_uuids = list(dict.fromkeys(episode_uuids))
        try:
            episode_references = await self._resolve_episode_references(
                unique_episode_uuids
            )
        except GraphitiRetrievalError:
            self.last_search_metadata.update(
                {"status": "failed", "error_type": "EpisodeResolutionError"}
            )
            raise

        selected: dict[str, dict[str, Any]] = {}
        resolved_edge_count = 0
        resolved_canonical_ids: set[str] = set()
        for edge_record in edge_records:
            canonical_for_edge: list[str] = []
            for episode_uuid in edge_record["episode_uuids"]:
                reference = episode_references.get(episode_uuid)
                if reference is None:
                    continue
                reference_group = str(reference.get("group_id", ""))
                if reference_group not in allowed_packs:
                    raise GraphitiProjectionBoundaryError(
                        "Graphiti episode resolution escaped the requested pack scope"
                    )
                event_id = str(reference.get("event_id", ""))
                for document in self._documents_by_event.get(event_id, []):
                    document_id = str(document["id"])
                    document_pack = str(document.get("pack_id", ""))
                    if document_pack not in allowed_packs:
                        raise GraphitiProjectionBoundaryError(
                            "canonical document resolution escaped the requested pack scope"
                        )
                    canonical_for_edge.append(document_id)
                    resolved_canonical_ids.add(document_id)
                    current = selected.get(document_id)
                    candidate = {
                        "document": document,
                        "score": float(edge_record["graph_score"]),
                        "graph_rank": int(edge_record["graph_rank"]),
                        "edge_uuid": str(edge_record["edge_uuid"]),
                        "episode_uuids": list(edge_record["episode_uuids"]),
                    }
                    if current is None or (
                        candidate["score"], -candidate["graph_rank"]
                    ) > (current["score"], -current["graph_rank"]):
                        selected[document_id] = candidate
            if canonical_for_edge:
                resolved_edge_count += 1

        ordered = sorted(
            selected.values(),
            key=lambda item: (
                -float(item["score"]),
                int(item["graph_rank"]),
                str(item["document"]["id"]),
            ),
        )[: int(limit)]
        service = self.metadata()
        results: list[dict[str, Any]] = []
        for rank, item in enumerate(ordered, start=1):
            result = copy.deepcopy(item["document"])
            result["retrieval"] = {
                "score": float(item["score"]),
                "rank": rank,
                "service": service,
            }
            result["graph_expansion"] = {
                "resolver": GRAPHITI_EXPANSION_RESOLVER,
                "graph_rank": int(item["graph_rank"]),
                "graph_score": float(item["score"]),
                "edge_uuid": str(item["edge_uuid"]),
                "episode_uuids": list(item["episode_uuids"]),
                "canonical_id": str(item["document"]["id"]),
            }
            results.append(result)

        self.last_search_metadata = {
            "resolver": GRAPHITI_EXPANSION_RESOLVER,
            "status": "ok",
            "requested_pack_ids": sorted(allowed_packs),
            "graph_search_limit": graph_limit,
            "graph_edges_returned": len(edges),
            "graph_edges_resolved_to_canonical": resolved_edge_count,
            "graph_query_count": 2 if unique_episode_uuids else 1,
            "edge_uuids": [str(item["edge_uuid"]) for item in edge_records],
            "episode_uuids": unique_episode_uuids,
            "resolved_canonical_ids": sorted(resolved_canonical_ids),
            "returned_canonical_ids": [str(item["id"]) for item in results],
        }
        return results

    def search(
        self,
        query: str,
        *,
        pack_ids: list[str],
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        awaitable = self.search_async(query, pack_ids=pack_ids, limit=limit)
        if self.async_runner is not None:
            try:
                return self.async_runner(awaitable)
            except GraphitiRetrievalError:
                raise
            except Exception as exc:
                close = getattr(awaitable, "close", None)
                if close is not None:
                    close()
                self.last_search_metadata = {
                    "resolver": GRAPHITI_EXPANSION_RESOLVER,
                    "status": "failed",
                    "requested_pack_ids": sorted(str(pack_id) for pack_id in pack_ids),
                    "error_type": type(exc).__name__,
                }
                raise GraphitiRetrievalError(
                    "Graphiti async runner failed"
                ) from exc
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(awaitable)
        close = getattr(awaitable, "close", None)
        if close is not None:
            close()
        raise RuntimeError(
            "sync Graphiti retriever API called inside an event loop; use search_async"
        )


__all__ = [
    "GRAPHITI_EXPANSION_RESOLVER",
    "GraphitiExpansionRetriever",
    "GraphitiProjectionBoundaryError",
    "GraphitiRetrievalError",
]
