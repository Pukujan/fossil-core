from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

SHARED_CHAT_CAPTURE_RECEIPT_VERSION = "fossil.shared-chat-capture-receipt.v1"


class SharedChatCaptureError(ValueError):
    """A shared-chat capture cannot satisfy the completeness contract."""


def _ids(graph: Mapping[str, Any], field: str) -> set[str]:
    return {str(value) for value in graph[field]}


def _continuation_is_terminal_success(continuation: Mapping[str, Any]) -> bool:
    """Return whether continuation accounting proves a successful terminal state."""

    state = continuation.get("state")
    termination_reason = continuation.get("termination_reason")
    attempts = list(continuation.get("attempts", []))
    if state == "not_present":
        return termination_reason == "source_terminal" and not attempts
    if state == "resolved":
        return (
            termination_reason == "continuation_exhausted"
            and bool(attempts)
            and all(attempt.get("outcome") == "success" for attempt in attempts)
        )
    return False


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

    expected_counts = {
        "discovered_node_count": len(discovered),
        "accounted_node_count": len(accounted),
        "message_node_count": len(messages),
    }
    for field, expected in expected_counts.items():
        if int(graph[field]) != expected:
            raise SharedChatCaptureError(
                f"capture {field} must equal the number of corresponding node IDs"
            )

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
    if active | non_active != discovered:
        raise SharedChatCaptureError(
            "capture active and non-active exposed node sets must partition discovered nodes"
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
        if not messages:
            raise SharedChatCaptureError(
                "complete capture requires at least one message-bearing node"
            )
        if not roots:
            raise SharedChatCaptureError(
                "complete capture requires at least one root node"
            )
        if accounted != discovered:
            raise SharedChatCaptureError(
                "complete capture requires every discovered node to be accounted for"
            )
        if graph["unresolved_refs"]:
            raise SharedChatCaptureError(
                "complete capture cannot contain unresolved graph references"
            )
        if not _continuation_is_terminal_success(candidate["continuation"]):
            raise SharedChatCaptureError(
                "complete capture requires successful terminal continuation accounting"
            )

    return candidate


def _graph_unresolved_refs(nodes: Mapping[str, Mapping[str, Any]]) -> list[dict[str, str]]:
    discovered = set(nodes)
    unresolved: list[dict[str, str]] = []
    for node_id in sorted(discovered):
        node = nodes[node_id]
        parent_id = node.get("parent_id")
        if parent_id is not None:
            parent_id = str(parent_id)
            if parent_id not in discovered:
                unresolved.append(
                    {
                        "from_node_id": node_id,
                        "relation": "parent",
                        "target_node_id": parent_id,
                    }
                )
            else:
                parent_children = {str(child) for child in nodes[parent_id].get("child_ids", [])}
                if node_id not in parent_children:
                    unresolved.append(
                        {
                            "from_node_id": node_id,
                            "relation": "other",
                            "target_node_id": parent_id,
                        }
                    )

        for child_id_raw in node.get("child_ids", []):
            child_id = str(child_id_raw)
            if child_id not in discovered:
                unresolved.append(
                    {
                        "from_node_id": node_id,
                        "relation": "child",
                        "target_node_id": child_id,
                    }
                )
                continue
            child_parent = nodes[child_id].get("parent_id")
            if child_parent is None or str(child_parent) != node_id:
                unresolved.append(
                    {
                        "from_node_id": node_id,
                        "relation": "other",
                        "target_node_id": child_id,
                    }
                )

    # Parent/child pairs can be internally consistent while still forming a
    # cycle with no root. Such a graph cannot be exhausted by a finite
    # conversation traversal and must not be treated as complete.
    for start in sorted(discovered):
        visited: set[str] = set()
        cursor: str | None = start
        steps = 0
        while cursor is not None and cursor in nodes:
            if cursor in visited:
                unresolved.append(
                    {
                        "from_node_id": cursor,
                        "relation": "other",
                        "target_node_id": cursor,
                    }
                )
                break
            if steps >= len(nodes):
                unresolved.append(
                    {
                        "from_node_id": cursor,
                        "relation": "other",
                        "target_node_id": cursor,
                    }
                )
                break
            visited.add(cursor)
            steps += 1
            parent = nodes[cursor].get("parent_id")
            cursor = None if parent is None else str(parent)
    return unresolved


def _active_branch(
    nodes: Mapping[str, Mapping[str, Any]], current_node_id: str | None
) -> tuple[list[str], list[dict[str, str]]]:
    if current_node_id is None:
        return [], []
    if current_node_id not in nodes:
        if not nodes:
            return [], []
        return [], [
            {
                "from_node_id": min(nodes),
                "relation": "other",
                "target_node_id": current_node_id,
            }
        ]

    reverse_path: list[str] = []
    visited: set[str] = set()
    cursor: str | None = current_node_id
    unresolved: list[dict[str, str]] = []
    steps = 0
    while cursor is not None:
        if cursor in visited:
            unresolved.append(
                {
                    "from_node_id": cursor,
                    "relation": "other",
                    "target_node_id": cursor,
                }
            )
            break
        if steps >= len(nodes):
            unresolved.append(
                {
                    "from_node_id": cursor,
                    "relation": "other",
                    "target_node_id": cursor,
                }
            )
            break
        visited.add(cursor)
        steps += 1
        reverse_path.append(cursor)
        parent = nodes[cursor].get("parent_id")
        if parent is None:
            break
        parent_id = str(parent)
        if parent_id not in nodes:
            # The graph-level reference checker records the missing target.
            break
        cursor = parent_id
    return list(reversed(reverse_path)), unresolved


def build_shared_chat_capture_receipt(
    *,
    capture_id: str,
    provider: str,
    source: Mapping[str, Any],
    fidelity: str,
    nodes: Mapping[str, Mapping[str, Any]],
    current_node_id: str | None,
    continuation: Mapping[str, Any],
    schema_path: Path,
    adapter_version: str | None = None,
) -> dict[str, Any]:
    """Derive a receipt from parsed provider-neutral conversation graph records.

    ``nodes`` is an adapter boundary, not durable authority. Each key is a
    provider-exposed node identity. A node may provide ``parent_id``,
    ``child_ids`` and ``message_present``. This function independently derives
    graph accounting, active/non-active partitioning, unresolved references and
    the completeness status before returning a schema-valid receipt.
    """

    normalized_nodes: dict[str, dict[str, Any]] = {
        str(node_id): copy.deepcopy(dict(node)) for node_id, node in nodes.items()
    }
    discovered = set(normalized_nodes)
    roots = sorted(
        node_id
        for node_id, node in normalized_nodes.items()
        if node.get("parent_id") is None
    )
    message_nodes = sorted(
        node_id
        for node_id, node in normalized_nodes.items()
        if bool(node.get("message_present", False))
    )

    unresolved = _graph_unresolved_refs(normalized_nodes)
    requested_current = None if current_node_id is None else str(current_node_id)
    active, active_unresolved = _active_branch(normalized_nodes, requested_current)
    unresolved.extend(active_unresolved)
    unresolved = sorted(
        unresolved,
        key=lambda item: (
            item["from_node_id"],
            item["relation"],
            item["target_node_id"],
        ),
    )
    active_set = set(active)
    non_active = sorted(discovered - active_set)
    recorded_current = requested_current if requested_current in discovered else None

    continuation_copy = copy.deepcopy(dict(continuation))
    complete = (
        bool(discovered)
        and bool(message_nodes)
        and bool(roots)
        and not unresolved
        and _continuation_is_terminal_success(continuation_copy)
    )

    discovered_ids = sorted(discovered)
    receipt = {
        "schema_version": SHARED_CHAT_CAPTURE_RECEIPT_VERSION,
        "capture_id": capture_id,
        "provider": provider,
        "adapter_version": adapter_version,
        "source": copy.deepcopy(dict(source)),
        "fidelity": fidelity,
        "completeness": "complete" if complete else "incomplete",
        "graph": {
            "discovered_node_count": len(discovered_ids),
            "accounted_node_count": len(discovered_ids),
            "message_node_count": len(message_nodes),
            "discovered_node_ids": discovered_ids,
            "accounted_node_ids": discovered_ids,
            "message_node_ids": message_nodes,
            "root_node_ids": roots,
            "current_node_id": recorded_current,
            "active_branch_node_ids": active,
            "non_active_exposed_node_ids": non_active,
            "unresolved_refs": unresolved,
        },
        "continuation": continuation_copy,
    }
    return validate_shared_chat_capture_receipt(receipt, schema_path=schema_path)


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
