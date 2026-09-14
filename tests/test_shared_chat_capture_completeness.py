from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from scripts.ingest_shared_chat_reconstructions import ingest_manifest


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "examples" / "shared-chat-ingestion" / "2026-08-14.json"
RECEIPT_SCHEMA = ROOT / "schemas" / "shared-chat-capture" / "receipt-v1.schema.json"


def _incomplete_receipt(external_ref: str) -> dict:
    """A fully-accounted exposed graph with an unresolved provider continuation."""

    return {
        "schema_version": "fossil.shared-chat-capture-receipt.v1",
        "capture_id": "capture_red_incomplete_001",
        "provider": "chatgpt-share",
        "adapter_version": "red-fixture-v1",
        "source": {
            "external_ref": external_ref,
            "artifact_id": None,
            "sha256": "a" * 64,
            "byte_count": 1234,
            "captured_at": "2026-09-14T13:34:45Z",
        },
        "fidelity": "reconstructed",
        "completeness": "incomplete",
        "graph": {
            "discovered_node_ids": ["node_root", "node_message"],
            "accounted_node_ids": ["node_root", "node_message"],
            "message_node_ids": ["node_message"],
            "root_node_ids": ["node_root"],
            "current_node_id": "node_message",
            "active_branch_node_ids": ["node_root", "node_message"],
            "non_active_exposed_node_ids": [],
            "unresolved_refs": [],
        },
        "continuation": {
            "state": "unresolved",
            "mechanism": "/continue",
            "attempts": [
                {
                    "ref": "/continue",
                    "outcome": "http_error",
                    "status_code": 403,
                }
            ],
            "termination_reason": "http_error",
        },
    }


def test_capture_receipt_schema_allows_accounted_graph_to_remain_incomplete() -> None:
    schema = json.loads(RECEIPT_SCHEMA.read_text(encoding="utf-8"))
    receipt = _incomplete_receipt("https://chatgpt.com/share/example")

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(receipt)

    # Exhausting the graph exposed in one response is not proof that an
    # unresolved provider continuation contains no additional material.
    assert receipt["graph"]["discovered_node_ids"] == receipt["graph"]["accounted_node_ids"]
    assert receipt["continuation"]["state"] == "unresolved"
    assert receipt["completeness"] == "incomplete"


def test_shared_chat_import_refuses_explicit_incomplete_capture_before_writing(
    tmp_path: Path,
) -> None:
    """RED for #247: current baseline silently ignores capture completeness."""

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["import_id"] = "shared-chat-completeness-red"
    manifest["conversations"] = manifest["conversations"][:1]
    conversation = manifest["conversations"][0]
    conversation["capture_receipt"] = _incomplete_receipt(conversation["external_ref"])

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output_root = tmp_path / "output"

    with pytest.raises(ValueError, match="capture|completeness|incomplete"):
        ingest_manifest(manifest_path, output_root, repo_root=ROOT)

    assert not list((output_root / "events").rglob("evt_*.json"))
    assert not list((output_root / "conversations").rglob("conv_*.json"))
