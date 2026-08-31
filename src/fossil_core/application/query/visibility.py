from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


VISIBILITY_POLICY = "fossil-retrieval-visibility-v1"


class VisibilityDenied(ValueError):
    """Raised when a caller cannot observe a document or citation."""


@dataclass(frozen=True)
class CallerVisibility:
    """The caller-scoped observation boundary for rebuildable retrieval projections."""

    principal_id: str
    readable_pack_ids: frozenset[str]
    allowed_sensitivity: frozenset[str]

    def __post_init__(self) -> None:
        if not self.principal_id:
            raise ValueError("caller visibility requires principal_id")
        if not self.readable_pack_ids:
            raise ValueError("caller visibility requires readable packs")
        if not self.allowed_sensitivity:
            raise ValueError("caller visibility requires allowed sensitivity levels")


class RetrievalVisibilityPolicy:
    """Apply authorization before indexing and again at every result boundary.

    This policy is deliberately independent of BM25, embeddings, fusion, rerankers,
    Graphiti, and answer services. Missing security metadata fails closed.
    """

    version = VISIBILITY_POLICY

    @staticmethod
    def denial_reason(document: Mapping[str, Any], caller: CallerVisibility) -> str | None:
        pack_id = str(document.get("pack_id", ""))
        if not pack_id or pack_id not in caller.readable_pack_ids:
            return "pack_not_readable"
        acl = document.get("acl")
        if not isinstance(acl, (list, tuple, set, frozenset)) or not acl:
            return "missing_acl"
        principals = {str(item) for item in acl}
        if "*" not in principals and caller.principal_id not in principals:
            return "acl_denied"
        sensitivity = str(document.get("sensitivity", ""))
        if not sensitivity or sensitivity not in caller.allowed_sensitivity:
            return "sensitivity_denied"
        if bool(document.get("suppressed", False)):
            return "suppressed"
        if bool(document.get("redacted", False)):
            return "redacted"
        return None

    def require_visible(self, document: Mapping[str, Any], caller: CallerVisibility) -> None:
        reason = self.denial_reason(document, caller)
        if reason is not None:
            raise VisibilityDenied(reason)

    def visible_documents(
        self,
        documents: Iterable[Mapping[str, Any]],
        caller: CallerVisibility,
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        visible: list[dict[str, Any]] = []
        denied: dict[str, int] = {}
        for document in documents:
            reason = self.denial_reason(document, caller)
            if reason is None:
                visible.append(copy.deepcopy(dict(document)))
            else:
                denied[reason] = denied.get(reason, 0) + 1
        return visible, dict(sorted(denied.items()))

    def require_visible_candidates(
        self,
        candidates: Iterable[Mapping[str, Any]],
        caller: CallerVisibility,
    ) -> list[dict[str, Any]]:
        safe: list[dict[str, Any]] = []
        for candidate in candidates:
            self.require_visible(candidate, caller)
            safe.append(copy.deepcopy(dict(candidate)))
        return safe

    def read(
        self,
        document_id: str,
        documents: Iterable[Mapping[str, Any]],
        caller: CallerVisibility,
    ) -> dict[str, Any]:
        for document in documents:
            if str(document.get("id", "")) == str(document_id):
                self.require_visible(document, caller)
                return copy.deepcopy(dict(document))
        raise VisibilityDenied("unknown_or_denied_id")

    def authorize_citation(
        self,
        citation: Mapping[str, Any],
        *,
        document_id: str,
        documents: Iterable[Mapping[str, Any]],
        caller: CallerVisibility,
    ) -> dict[str, Any]:
        document = self.read(document_id, documents, caller)
        expected = document.get("citation")
        if not isinstance(expected, Mapping) or dict(expected) != dict(citation):
            raise VisibilityDenied("citation_not_authorized_for_document")
        return copy.deepcopy(dict(citation))

    def export_document(
        self,
        document: Mapping[str, Any],
        caller: CallerVisibility,
    ) -> dict[str, Any] | None:
        if self.denial_reason(document, caller) is not None:
            return None
        return copy.deepcopy(dict(document))

    def export_documents(
        self,
        documents: Iterable[Mapping[str, Any]],
        caller: CallerVisibility,
    ) -> list[dict[str, Any]]:
        return [
            exported
            for document in documents
            if (exported := self.export_document(document, caller)) is not None
        ]


class SecurityFilteredRetriever:
    """Fail-closed defense-in-depth wrapper for any Retriever implementation."""

    def __init__(self, retriever: Any, *, policy: RetrievalVisibilityPolicy, caller: CallerVisibility):
        self.retriever = retriever
        self.policy = policy
        self.caller = caller

    def metadata(self) -> dict[str, Any]:
        metadata = copy.deepcopy(dict(self.retriever.metadata()))
        runtime = dict(metadata.get("runtime", {}))
        runtime["visibility_policy"] = self.policy.version
        runtime["visibility_principal"] = self.caller.principal_id
        metadata["runtime"] = runtime
        return metadata

    def search(self, query: str, *, pack_ids: list[str], limit: int = 20) -> list[dict[str, Any]]:
        requested = {str(item) for item in pack_ids}
        if not requested <= self.caller.readable_pack_ids:
            raise VisibilityDenied("requested_pack_not_readable")
        candidates = self.retriever.search(query, pack_ids=list(pack_ids), limit=limit)
        return self.policy.require_visible_candidates(candidates, self.caller)


__all__ = [
    "CallerVisibility",
    "RetrievalVisibilityPolicy",
    "SecurityFilteredRetriever",
    "VISIBILITY_POLICY",
    "VisibilityDenied",
]
