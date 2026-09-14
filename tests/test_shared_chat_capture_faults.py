from __future__ import annotations

from pathlib import Path

import pytest
from fossil_core.application.ingest.shared_chat_capture import (
    SharedChatCaptureError,
    _active_branch,
    build_shared_chat_capture_receipt,
    require_complete_shared_chat_capture,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schemas" / "shared-chat-capture" / "receipt-v1.schema.json"


def _source() -> dict:
    return {
        "external_ref": "fixture://shared-chat-fault",
        "artifact_id": None,
        "sha256": "d" * 64,
        "byte_count": 128,
        "captured_at": "2026-09-14T15:00:00Z",
    }


def _nodes(*, message_present: bool = True) -> dict[str, dict]:
    return {
        "root": {
            "parent_id": None,
            "child_ids": ["message"],
            "message_present": False,
        },
        "message": {
            "parent_id": "root",
            "child_ids": [],
            "message_present": message_present,
        },
    }


def _fault_continuation(outcome: str, *, status_code: int | None = None) -> dict:
    attempt = {"ref": "/continue", "outcome": outcome}
    if status_code is not None:
        attempt["status_code"] = status_code
    return {
        "state": "unresolved",
        "mechanism": "/continue",
        "attempts": [attempt],
        "termination_reason": {
            "http_error": "http_error",
            "timeout": "timeout",
            "connection_error": "connection_error",
            "malformed": "malformed",
            "cycle": "cycle",
        }[outcome],
    }


@pytest.mark.parametrize(
    ("name", "continuation"),
    [
        ("http_403", _fault_continuation("http_error", status_code=403)),
        ("http_429", _fault_continuation("http_error", status_code=429)),
        ("http_500", _fault_continuation("http_error", status_code=500)),
        ("timeout", _fault_continuation("timeout")),
        ("connection_reset", _fault_continuation("connection_error")),
        ("malformed_payload", _fault_continuation("malformed")),
        ("truncated_payload", _fault_continuation("malformed")),
        (
            "continuation_loop",
            {
                "state": "unresolved",
                "mechanism": "/continue",
                "attempts": [
                    {"ref": "/continue?a", "outcome": "cycle"},
                    {"ref": "/continue?b", "outcome": "cycle"},
                ],
                "termination_reason": "cycle",
            },
        ),
        (
            "a_b_a_cycle",
            {
                "state": "unresolved",
                "mechanism": "/continue",
                "attempts": [
                    {"ref": "/continue?a", "outcome": "success"},
                    {"ref": "/continue?b", "outcome": "success"},
                    {"ref": "/continue?a", "outcome": "cycle"},
                ],
                "termination_reason": "cycle",
            },
        ),
        (
            "same_page_replayed_indefinitely",
            {
                "state": "unresolved",
                "mechanism": "/continue",
                "attempts": [
                    {"ref": "/continue?same", "outcome": "success"},
                    {"ref": "/continue?same", "outcome": "cycle"},
                ],
                "termination_reason": "cycle",
            },
        ),
    ],
)
def test_acquisition_faults_never_produce_complete_capture(
    name: str, continuation: dict
) -> None:
    receipt = build_shared_chat_capture_receipt(
        capture_id=f"capture_fault_{name}",
        provider="fixture",
        source=_source(),
        fidelity="verbatim",
        nodes=_nodes(),
        current_node_id="message",
        continuation=continuation,
        schema_path=SCHEMA,
        adapter_version="fault-fixture-v1",
    )

    assert receipt["completeness"] == "incomplete"
    with pytest.raises(SharedChatCaptureError, match="refused|incomplete"):
        require_complete_shared_chat_capture(receipt, schema_path=SCHEMA)


def test_zero_message_structure_is_not_complete_despite_successful_transport() -> None:
    receipt = build_shared_chat_capture_receipt(
        capture_id="capture_fault_zero_messages",
        provider="fixture",
        source=_source(),
        fidelity="verbatim",
        nodes={
            "root": {
                "parent_id": None,
                "child_ids": [],
                "message_present": False,
            }
        },
        current_node_id="root",
        continuation={
            "state": "not_present",
            "mechanism": None,
            "attempts": [],
            "termination_reason": "source_terminal",
        },
        schema_path=SCHEMA,
        adapter_version="fault-fixture-v1",
    )

    assert receipt["completeness"] == "incomplete"


def test_error_attempt_cannot_be_relabelled_as_resolved() -> None:
    receipt = build_shared_chat_capture_receipt(
        capture_id="capture_fault_relabelled_error",
        provider="fixture",
        source=_source(),
        fidelity="verbatim",
        nodes=_nodes(),
        current_node_id="message",
        continuation={
            "state": "resolved",
            "mechanism": "/continue",
            "attempts": [
                {"ref": "/continue", "outcome": "http_error", "status_code": 503}
            ],
            "termination_reason": "continuation_exhausted",
        },
        schema_path=SCHEMA,
        adapter_version="fault-fixture-v1",
    )

    assert receipt["completeness"] == "incomplete"


def test_successfully_resolved_continuation_is_complete() -> None:
    receipt = build_shared_chat_capture_receipt(
        capture_id="capture_resolved_success",
        provider="fixture",
        source=_source(),
        fidelity="verbatim",
        nodes=_nodes(),
        current_node_id="message",
        continuation={
            "state": "resolved",
            "mechanism": "/continue",
            "attempts": [{"ref": "/continue", "outcome": "success"}],
            "termination_reason": "continuation_exhausted",
        },
        schema_path=SCHEMA,
        adapter_version="fault-fixture-v1",
    )

    assert receipt["completeness"] == "complete"
    assert require_complete_shared_chat_capture(receipt, schema_path=SCHEMA) == receipt


def test_not_present_continuation_requires_source_terminal_reason() -> None:
    receipt = build_shared_chat_capture_receipt(
        capture_id="capture_not_present_wrong_reason",
        provider="fixture",
        source=_source(),
        fidelity="verbatim",
        nodes=_nodes(),
        current_node_id="message",
        continuation={
            "state": "not_present",
            "mechanism": None,
            "attempts": [],
            "termination_reason": "continuation_exhausted",
        },
        schema_path=SCHEMA,
        adapter_version="fault-fixture-v1",
    )

    assert receipt["completeness"] == "incomplete"


def test_unresolved_state_cannot_use_successful_terminal_metadata() -> None:
    receipt = build_shared_chat_capture_receipt(
        capture_id="capture_unresolved_terminal_metadata",
        provider="fixture",
        source=_source(),
        fidelity="verbatim",
        nodes=_nodes(),
        current_node_id="message",
        continuation={
            "state": "unresolved",
            "mechanism": "/continue",
            "attempts": [{"ref": "/continue", "outcome": "success"}],
            "termination_reason": "continuation_exhausted",
        },
        schema_path=SCHEMA,
        adapter_version="fault-fixture-v1",
    )

    assert receipt["completeness"] == "incomplete"


def test_missing_current_node_is_recorded_as_unresolved() -> None:
    receipt = build_shared_chat_capture_receipt(
        capture_id="capture_missing_current",
        provider="fixture",
        source=_source(),
        fidelity="verbatim",
        nodes=_nodes(),
        current_node_id="missing-current",
        continuation={
            "state": "not_present",
            "mechanism": None,
            "attempts": [],
            "termination_reason": "source_terminal",
        },
        schema_path=SCHEMA,
        adapter_version="fault-fixture-v1",
    )

    assert receipt["completeness"] == "incomplete"
    assert {
        "from_node_id": "message",
        "relation": "other",
        "target_node_id": "missing-current",
    } in receipt["graph"]["unresolved_refs"]


def test_a_parent_cycle_is_not_complete_when_current_node_is_not_supplied() -> None:
    receipt = build_shared_chat_capture_receipt(
        capture_id="capture_fault_parent_cycle",
        provider="fixture",
        source=_source(),
        fidelity="verbatim",
        nodes={
            "a": {"parent_id": "b", "child_ids": ["b"], "message_present": True},
            "b": {"parent_id": "a", "child_ids": ["a"], "message_present": True},
        },
        current_node_id=None,
        continuation={
            "state": "not_present",
            "mechanism": None,
            "attempts": [],
            "termination_reason": "source_terminal",
        },
        schema_path=SCHEMA,
        adapter_version="fault-fixture-v1",
    )

    assert receipt["completeness"] == "incomplete"


def test_a_disconnected_parent_cycle_is_not_hidden_by_a_valid_root() -> None:
    receipt = build_shared_chat_capture_receipt(
        capture_id="capture_fault_disconnected_parent_cycle",
        provider="fixture",
        source=_source(),
        fidelity="verbatim",
        nodes={
            "root": {"parent_id": None, "child_ids": ["message"], "message_present": False},
            "message": {"parent_id": "root", "child_ids": [], "message_present": True},
            "cycle_a": {
                "parent_id": "cycle_b",
                "child_ids": ["cycle_b"],
                "message_present": True,
            },
            "cycle_b": {
                "parent_id": "cycle_a",
                "child_ids": ["cycle_a"],
                "message_present": True,
            },
            "cycle_c": {
                "parent_id": "cycle_d",
                "child_ids": ["cycle_d"],
                "message_present": True,
            },
            "cycle_d": {
                "parent_id": "cycle_c",
                "child_ids": ["cycle_c"],
                "message_present": True,
            },
        },
        current_node_id="message",
        continuation={
            "state": "not_present",
            "mechanism": None,
            "attempts": [],
            "termination_reason": "source_terminal",
        },
        schema_path=SCHEMA,
        adapter_version="fault-fixture-v1",
    )

    assert receipt["completeness"] == "incomplete"
    assert any(
        ref["from_node_id"] == ref["target_node_id"] == "cycle_a"
        for ref in receipt["graph"]["unresolved_refs"]
    )
    assert any(
        ref["from_node_id"] == ref["target_node_id"] == "cycle_c"
        for ref in receipt["graph"]["unresolved_refs"]
    )


def test_missing_parent_reference_is_accounted() -> None:
    receipt = build_shared_chat_capture_receipt(
        capture_id="capture_fault_missing_parent",
        provider="fixture",
        source=_source(),
        fidelity="verbatim",
        nodes={
            "root": {"parent_id": None, "child_ids": ["message"], "message_present": False},
            "message": {
                "parent_id": "missing-parent",
                "child_ids": [],
                "message_present": True,
            },
        },
        current_node_id="message",
        continuation={
            "state": "not_present",
            "mechanism": None,
            "attempts": [],
            "termination_reason": "source_terminal",
        },
        schema_path=SCHEMA,
        adapter_version="fault-fixture-v1",
    )

    assert {
        "from_node_id": "message",
        "relation": "parent",
        "target_node_id": "missing-parent",
    } in receipt["graph"]["unresolved_refs"]


def test_bidirectional_parent_child_mismatch_is_accounted() -> None:
    receipt = build_shared_chat_capture_receipt(
        capture_id="capture_fault_mismatched_parent_child",
        provider="fixture",
        source=_source(),
        fidelity="verbatim",
        nodes={
            "root": {"parent_id": None, "child_ids": ["message"], "message_present": False},
            "other": {"parent_id": None, "child_ids": [], "message_present": False},
            "message": {
                "parent_id": "other",
                "child_ids": [],
                "message_present": True,
            },
        },
        current_node_id="message",
        continuation={
            "state": "not_present",
            "mechanism": None,
            "attempts": [],
            "termination_reason": "source_terminal",
        },
        schema_path=SCHEMA,
        adapter_version="fault-fixture-v1",
    )

    assert receipt["completeness"] == "incomplete"
    assert {
        "from_node_id": "root",
        "relation": "other",
        "target_node_id": "message",
    } in receipt["graph"]["unresolved_refs"]
    assert {
        "from_node_id": "message",
        "relation": "other",
        "target_node_id": "other",
    } in receipt["graph"]["unresolved_refs"]


def test_multiple_missing_child_references_are_all_recorded() -> None:
    receipt = build_shared_chat_capture_receipt(
        capture_id="capture_fault_multiple_missing_children",
        provider="fixture",
        source=_source(),
        fidelity="verbatim",
        nodes={
            "root": {
                "parent_id": None,
                "child_ids": ["missing-a", "missing-b"],
                "message_present": True,
            }
        },
        current_node_id="root",
        continuation={
            "state": "not_present",
            "mechanism": None,
            "attempts": [],
            "termination_reason": "source_terminal",
        },
        schema_path=SCHEMA,
        adapter_version="fault-fixture-v1",
    )

    targets = {
        ref["target_node_id"]
        for ref in receipt["graph"]["unresolved_refs"]
        if ref["relation"] == "child"
    }
    assert targets == {"missing-a", "missing-b"}


def test_missing_message_flag_does_not_create_usable_message() -> None:
    receipt = build_shared_chat_capture_receipt(
        capture_id="capture_fault_missing_message_flag",
        provider="fixture",
        source=_source(),
        fidelity="verbatim",
        nodes={
            "root": {
                "parent_id": None,
                "child_ids": [],
            }
        },
        current_node_id="root",
        continuation={
            "state": "not_present",
            "mechanism": None,
            "attempts": [],
            "termination_reason": "source_terminal",
        },
        schema_path=SCHEMA,
        adapter_version="fault-fixture-v1",
    )

    assert receipt["graph"]["message_node_ids"] == []
    assert receipt["completeness"] == "incomplete"


def test_missing_child_ids_default_to_no_children() -> None:
    receipt = build_shared_chat_capture_receipt(
        capture_id="capture_missing_child_ids",
        provider="fixture",
        source=_source(),
        fidelity="verbatim",
        nodes={"root": {"parent_id": None, "message_present": True}},
        current_node_id="root",
        continuation={
            "state": "not_present",
            "mechanism": None,
            "attempts": [],
            "termination_reason": "source_terminal",
        },
        schema_path=SCHEMA,
        adapter_version="fault-fixture-v1",
    )

    assert receipt["completeness"] == "complete"


def test_missing_parent_child_list_is_still_a_bidirectional_mismatch() -> None:
    receipt = build_shared_chat_capture_receipt(
        capture_id="capture_missing_parent_child_list",
        provider="fixture",
        source=_source(),
        fidelity="verbatim",
        nodes={
            "root": {"parent_id": None, "message_present": False},
            "message": {
                "parent_id": "root",
                "child_ids": [],
                "message_present": True,
            },
        },
        current_node_id="message",
        continuation={
            "state": "not_present",
            "mechanism": None,
            "attempts": [],
            "termination_reason": "source_terminal",
        },
        schema_path=SCHEMA,
        adapter_version="fault-fixture-v1",
    )

    assert receipt["completeness"] == "incomplete"
    assert {
        "from_node_id": "message",
        "relation": "other",
        "target_node_id": "root",
    } in receipt["graph"]["unresolved_refs"]


def test_active_branch_walker_reports_parent_cycle_independently() -> None:
    active, unresolved = _active_branch(
        {
            "a": {"parent_id": "b", "child_ids": [], "message_present": True},
            "b": {"parent_id": "a", "child_ids": [], "message_present": True},
        },
        "a",
    )

    assert active == ["b", "a"]
    assert unresolved == [
        {"from_node_id": "a", "relation": "other", "target_node_id": "a"}
    ]
