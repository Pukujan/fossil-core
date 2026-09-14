from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from fossil_core.application.ingest.shared_chat_capture import (
    build_shared_chat_capture_receipt,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schemas" / "shared-chat-capture" / "receipt-v1.schema.json"


def _source() -> dict:
    return {
        "external_ref": "fixture://shared-chat",
        "artifact_id": None,
        "sha256": "c" * 64,
        "byte_count": 999,
        "captured_at": "2026-09-14T14:00:00Z",
    }


def _terminal_continuation() -> dict:
    return {
        "state": "not_present",
        "mechanism": None,
        "attempts": [],
        "termination_reason": "source_terminal",
    }


def _unresolved_continuation() -> dict:
    return {
        "state": "unresolved",
        "mechanism": "/continue",
        "attempts": [
            {"ref": "/continue", "outcome": "http_error", "status_code": 403}
        ],
        "termination_reason": "http_error",
    }


def _linear_nodes(count: int) -> dict[str, dict]:
    nodes: dict[str, dict] = {}
    for index in range(count):
        node_id = f"node_{index:04d}"
        parent_id = None if index == 0 else f"node_{index - 1:04d}"
        child_ids = [] if index == count - 1 else [f"node_{index + 1:04d}"]
        nodes[node_id] = {
            "parent_id": parent_id,
            "child_ids": child_ids,
            "message_present": index != 0,
        }
    return nodes


def _reference_active_branch(nodes: dict[str, dict], current_node_id: str) -> list[str]:
    """Deliberately tiny test-only walker independent from production code."""

    reverse_path: list[str] = []
    seen: set[str] = set()
    cursor: str | None = current_node_id
    while cursor is not None:
        assert cursor in nodes
        assert cursor not in seen
        seen.add(cursor)
        reverse_path.append(cursor)
        parent = nodes[cursor].get("parent_id")
        cursor = None if parent is None else str(parent)
    return list(reversed(reverse_path))


def _build(nodes: dict[str, dict], current: str, continuation: dict | None = None) -> dict:
    return build_shared_chat_capture_receipt(
        capture_id="capture_accounting_fixture",
        provider="fixture",
        source=_source(),
        fidelity="verbatim",
        nodes=nodes,
        current_node_id=current,
        continuation=continuation or _terminal_continuation(),
        schema_path=SCHEMA,
        adapter_version="fixture-v1",
    )


def test_long_fixture_accounts_for_every_node_not_a_viewport_subset() -> None:
    # Deliberately much larger than a first-screen/browser-control sample.
    nodes = _linear_nodes(600)
    receipt = _build(nodes, "node_0599")

    assert receipt["completeness"] == "complete"
    assert len(receipt["graph"]["discovered_node_ids"]) == 600
    assert len(receipt["graph"]["accounted_node_ids"]) == 600
    assert len(receipt["graph"]["message_node_ids"]) == 599
    assert receipt["graph"]["root_node_ids"] == ["node_0000"]
    assert receipt["graph"]["current_node_id"] == "node_0599"
    assert receipt["graph"]["active_branch_node_ids"] == _reference_active_branch(
        nodes, "node_0599"
    )


def test_receipt_does_not_alias_nested_adapter_inputs() -> None:
    nodes = _linear_nodes(3)
    continuation = _terminal_continuation()
    source = _source()
    receipt = build_shared_chat_capture_receipt(
        capture_id="capture_copy_fixture",
        provider="fixture",
        source=source,
        fidelity="verbatim",
        nodes=nodes,
        current_node_id="node_0002",
        continuation=continuation,
        schema_path=SCHEMA,
        adapter_version="fixture-v1",
    )

    continuation["attempts"].append({"ref": "/late", "outcome": "success"})
    source["external_ref"] = "fixture://mutated-after-build"
    nodes["node_0001"]["child_ids"].append("late-node")

    assert receipt["continuation"]["attempts"] == []
    assert receipt["source"]["external_ref"] == "fixture://shared-chat"


def test_differential_reference_walker_matches_branch_accounting() -> None:
    nodes = _linear_nodes(6)
    nodes["node_0002"]["child_ids"].append("branch_0003")
    nodes["branch_0003"] = {
        "parent_id": "node_0002",
        "child_ids": [],
        "message_present": True,
    }

    receipt = _build(nodes, "node_0005")

    assert receipt["completeness"] == "complete"
    assert receipt["graph"]["active_branch_node_ids"] == _reference_active_branch(
        nodes, "node_0005"
    )
    assert receipt["graph"]["non_active_exposed_node_ids"] == ["branch_0003"]


def test_mapping_order_is_metamorphically_irrelevant() -> None:
    nodes = _linear_nodes(25)
    reversed_nodes = OrderedDict(reversed(list(nodes.items())))

    forward = _build(nodes, "node_0024")
    reversed_receipt = _build(dict(reversed_nodes), "node_0024")

    assert forward["completeness"] == reversed_receipt["completeness"] == "complete"
    assert forward["graph"] == reversed_receipt["graph"]


def test_removing_required_reachable_node_can_only_reduce_completeness() -> None:
    nodes = _linear_nodes(10)
    complete = _build(nodes, "node_0009")
    damaged = {key: value for key, value in nodes.items() if key != "node_0005"}

    incomplete = _build(damaged, "node_0009")

    assert complete["completeness"] == "complete"
    assert incomplete["completeness"] == "incomplete"
    assert incomplete["graph"]["unresolved_refs"]


def test_fully_accounted_graph_with_unresolved_continuation_is_still_incomplete() -> None:
    nodes = _linear_nodes(20)
    receipt = _build(nodes, "node_0019", continuation=_unresolved_continuation())

    assert set(receipt["graph"]["discovered_node_ids"]) == set(
        receipt["graph"]["accounted_node_ids"]
    )
    assert receipt["graph"]["unresolved_refs"] == []
    assert receipt["continuation"]["state"] == "unresolved"
    assert receipt["completeness"] == "incomplete"


def test_missing_child_reference_fails_closed_even_with_terminal_transport() -> None:
    nodes = _linear_nodes(4)
    nodes["node_0003"]["child_ids"] = ["node_missing"]

    receipt = _build(nodes, "node_0003")

    assert receipt["completeness"] == "incomplete"
    assert {
        "from_node_id": "node_0003",
        "relation": "child",
        "target_node_id": "node_missing",
    } in receipt["graph"]["unresolved_refs"]
