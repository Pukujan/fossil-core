from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given, strategies as st
from jsonschema import ValidationError

from fossil_core.application.ingest.shared_chat_capture import (
    SharedChatCaptureError,
    validate_shared_chat_capture_receipt,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schemas" / "shared-chat-capture" / "receipt-v1.schema.json"
NODE_IDS = st.lists(
    st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789_", min_size=1, max_size=16),
    min_size=1,
    max_size=40,
    unique=True,
)


def _complete_receipt(node_ids: list[str]) -> dict:
    current = node_ids[-1]
    return {
        "schema_version": "fossil.shared-chat-capture-receipt.v1",
        "capture_id": "capture_property_fixture",
        "provider": "fixture",
        "adapter_version": "property-v1",
        "source": {
            "external_ref": "fixture://conversation",
            "artifact_id": None,
            "sha256": "b" * 64,
            "byte_count": 1,
            "captured_at": "2026-09-14T14:00:00Z",
        },
        "fidelity": "verbatim",
        "completeness": "complete",
        "graph": {
            "discovered_node_ids": list(node_ids),
            "accounted_node_ids": list(node_ids),
            "message_node_ids": list(node_ids),
            "root_node_ids": [node_ids[0]],
            "current_node_id": current,
            "active_branch_node_ids": list(node_ids),
            "non_active_exposed_node_ids": [],
            "unresolved_refs": [],
        },
        "continuation": {
            "state": "not_present",
            "mechanism": None,
            "attempts": [],
            "termination_reason": "source_terminal",
        },
    }


@given(NODE_IDS)
def test_complete_capture_requires_accounting_for_every_discovered_node(
    node_ids: list[str],
) -> None:
    receipt = _complete_receipt(node_ids)
    validated = validate_shared_chat_capture_receipt(receipt, schema_path=SCHEMA)
    assert set(validated["graph"]["accounted_node_ids"]) == set(node_ids)

    if len(node_ids) > 1:
        damaged = _complete_receipt(node_ids)
        damaged["graph"]["accounted_node_ids"] = node_ids[:-1]
        with pytest.raises(SharedChatCaptureError, match="every discovered node"):
            validate_shared_chat_capture_receipt(damaged, schema_path=SCHEMA)


@given(NODE_IDS)
def test_representation_order_does_not_change_complete_accounting(
    node_ids: list[str],
) -> None:
    forward = _complete_receipt(node_ids)
    reversed_receipt = _complete_receipt(list(reversed(node_ids)))
    # Keep the same semantic root/current identities while changing mapping order.
    reversed_receipt["graph"]["root_node_ids"] = [node_ids[0]]
    reversed_receipt["graph"]["current_node_id"] = node_ids[-1]

    validated_forward = validate_shared_chat_capture_receipt(forward, schema_path=SCHEMA)
    validated_reversed = validate_shared_chat_capture_receipt(
        reversed_receipt, schema_path=SCHEMA
    )

    assert set(validated_forward["graph"]["discovered_node_ids"]) == set(
        validated_reversed["graph"]["discovered_node_ids"]
    )
    assert validated_forward["completeness"] == validated_reversed["completeness"] == "complete"


@given(NODE_IDS)
def test_unresolved_continuation_cannot_be_relabelled_complete(
    node_ids: list[str],
) -> None:
    receipt = _complete_receipt(node_ids)
    receipt["continuation"] = {
        "state": "unresolved",
        "mechanism": "/continue",
        "attempts": [
            {"ref": "/continue", "outcome": "http_error", "status_code": 403}
        ],
        "termination_reason": "http_error",
    }

    with pytest.raises((ValidationError, SharedChatCaptureError)):
        validate_shared_chat_capture_receipt(receipt, schema_path=SCHEMA)


@given(NODE_IDS)
def test_exposed_node_partition_cannot_overlap_active_and_non_active(
    node_ids: list[str],
) -> None:
    receipt = _complete_receipt(node_ids)
    receipt["graph"]["non_active_exposed_node_ids"] = [node_ids[0]]

    with pytest.raises(SharedChatCaptureError, match="must be disjoint"):
        validate_shared_chat_capture_receipt(receipt, schema_path=SCHEMA)
