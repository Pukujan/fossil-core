from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker


SHARED_CHAT_CAPTURE_RECEIPT_VERSION = "fossil.shared-chat-capture-receipt.v1"


class SharedChatCaptureError(ValueError):
    """A shared-chat capture cannot satisfy the completeness contract."""


def _ids(graph: Mapping[str, Any], field: str) -> set[str]:
    return {str(value) for value in graph[field]}


def validate_shared_chat_capture_receipt(
    receipt: Mapping[str, Any], *, schema_path: Path
) -> dict[str, Any]:
    """Validate schema plus cross-field graph/accounting invariants.

    JSON Schema freezes the public receipt shape. These checks cover set/count
    relations that JSON Schema cannot express without provider-specific logic.
    No model, retrieval score, viewport count, or transport success participates
    in the completeness decision.
    """

    candidate = copy.deepcopy(dict(receipt))
    schema_path = Path(schema_path)
    import json

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(candidate)

    graph = candidate["graph"]
    discovered = _ids(graph, "discovered_node_ids")
    accounted = _ids(graph, "accounted_node_ids")
    messages = _ids(graph, "message_node_ids")
    roots = _ids(graph, "root_node_ids")
    active = _ids(graph, "active_branch_node_ids")
    non_active = _ids(graph, "non_active_exposed_node_ids")

    if not accounted <= discovered:
        raise SharedChatCaptureError(
            "capture accounted_node_ids must be a subset of discovered_node_ids"
        )
    if not messages <= discovered:
        raise SharedChatCaptureError(
            "capture message_node_ids must be a subset of discovered_node_ids"
        )
    if not roots <= discovered:
        raise SharedChatCaptureError(
            "capture root_node_ids must be a subset of discovered_node_ids"
        )
    if not active <= discovered:
        raise SharedChatCaptureError(
            "capture active_branch_node_ids must be a subset of discovered_node_ids"
        )
    if not non_active <= discovered:
        raise SharedChatCaptureError(
            "capture non_active_exposed_node_ids must be a subset of discovered_node_ids"
        )
    if active & non_active:
        raise SharedChatCaptureError(
            "capture active and non-active exposed node sets must be disjoint"
        )

    current_node_id = graph["current_node_id"]
    if current_node_id is not None and str(current_node_id) not in discovered:
        raise SharedChatCaptureError(
            "capture current_node_id must reference a discovered node"
        )

    for unresolved in graph["unresolved_refs"]:
        if str(unresolved["from_node_id"]) not in discovered:
            raise SharedChatCaptureError(
                "capture unresolved reference source must be a discovered node"
            )

    if candidate["completeness"] == "complete":
        if not discovered:
            raise SharedChatCaptureError(
                "complete capture requires at least one discovered node"
            )
        if accounted != discovered:
            raise SharedChatCaptureError(
                "complete capture requires every discovered node to be accounted for"
            )
        if graph["unresolved_refs"]:
            raise SharedChatCaptureError(
                "complete capture cannot contain unresolved graph references"
            )
        if candidate["continuation"]["state"] == "unresolved":
            raise SharedChatCaptureError(
                "complete capture cannot contain an unresolved continuation"
            )

    return candidate


def require_complete_shared_chat_capture(
    receipt: Mapping[str, Any], *, schema_path: Path
) -> dict[str, Any]:
    """Validate a receipt and fail closed unless completeness is proven."""

    candidate = validate_shared_chat_capture_receipt(receipt, schema_path=schema_path)
    completeness = str(candidate["completeness"])
    if completeness != "complete":
        raise SharedChatCaptureError(
            "shared-chat capture completeness is "
            f"{completeness}; complete conversation ingestion is refused"
        )
    return candidate
