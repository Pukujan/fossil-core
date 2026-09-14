from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fossil_core.application.ingest.shared_chat_capture import (
    SharedChatCaptureError,
    bind_shared_chat_capture_source,
    validate_shared_chat_capture_receipt,
)
from fossil_core.artifact_store import ArtifactStore
from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from scripts.ingest_shared_chat_reconstructions import ingest_manifest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "examples" / "shared-chat-ingestion" / "2026-08-14.json"
RECEIPT_SCHEMA = ROOT / "schemas" / "shared-chat-capture" / "receipt-v1.schema.json"
SOURCE_PATH = ROOT / "docs" / "recovery" / "2026-08-14-shared-chat-llm-bias-evaluation.md"


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
            "discovered_node_count": 2,
            "accounted_node_count": 2,
            "message_node_count": 1,
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


def _complete_receipt(external_ref: str) -> dict:
    receipt = _incomplete_receipt(external_ref)
    receipt["capture_id"] = "capture_complete_001"
    receipt["completeness"] = "complete"
    receipt["continuation"] = {
        "state": "not_present",
        "mechanism": None,
        "attempts": [],
        "termination_reason": "source_terminal",
    }
    return receipt


def _single_conversation_manifest(tmp_path: Path, receipt: dict) -> Path:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["import_id"] = "shared-chat-completeness-test"
    manifest["conversations"] = manifest["conversations"][:1]
    manifest["conversations"][0]["capture_receipt"] = receipt
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


def _receipt_bound_to_fixture_source(receipt: dict) -> dict:
    source_bytes = SOURCE_PATH.read_bytes()
    receipt["source"]["sha256"] = hashlib.sha256(source_bytes).hexdigest()
    receipt["source"]["byte_count"] = len(source_bytes)
    return receipt


def test_capture_receipt_schema_allows_accounted_graph_to_remain_incomplete() -> None:
    schema = json.loads(RECEIPT_SCHEMA.read_text(encoding="utf-8"))
    receipt = _incomplete_receipt("https://chatgpt.com/share/example")

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(receipt)

    # Exhausting the graph exposed in one response is not proof that an
    # unresolved provider continuation contains no additional material.
    assert receipt["graph"]["discovered_node_ids"] == receipt["graph"]["accounted_node_ids"]
    assert receipt["graph"]["discovered_node_count"] == receipt["graph"]["accounted_node_count"] == 2
    assert receipt["continuation"]["state"] == "unresolved"
    assert receipt["completeness"] == "incomplete"


def test_capture_receipt_schema_rejects_complete_with_unresolved_continuation() -> None:
    schema = json.loads(RECEIPT_SCHEMA.read_text(encoding="utf-8"))
    receipt = _incomplete_receipt("https://chatgpt.com/share/example")
    receipt["completeness"] = "complete"

    with pytest.raises(ValidationError):
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(receipt)


def test_shared_chat_import_preserves_incomplete_capture_before_refusing_promotion(
    tmp_path: Path,
) -> None:
    receipt = _receipt_bound_to_fixture_source(_incomplete_receipt(
        "https://chatgpt.com/share/6a7f2a03-f38c-83ea-b364-402c11090417?ogimg=plain"
    ))
    manifest_path = _single_conversation_manifest(tmp_path, receipt)
    output_root = tmp_path / "output"

    with pytest.raises(ValueError, match="capture|completeness|incomplete"):
        ingest_manifest(manifest_path, output_root, repo_root=ROOT)

    assert not list((output_root / "events").rglob("evt_*.json"))
    assert not list((output_root / "conversations").rglob("conv_*.json"))
    receipts = list((output_root / "capture-receipts").glob("*.json"))
    assert len(receipts) == 1
    stored_receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
    artifact_id = stored_receipt["source"]["artifact_id"]
    assert artifact_id.startswith("art_")
    assert ArtifactStore(output_root / "artifacts").read_bytes(artifact_id) == SOURCE_PATH.read_bytes()
    assert stored_receipt["completeness"] == "incomplete"


def test_incomplete_capture_evidence_replay_is_idempotent(tmp_path: Path) -> None:
    receipt = _receipt_bound_to_fixture_source(_incomplete_receipt("fixture://replay"))
    manifest_path = _single_conversation_manifest(tmp_path, receipt)
    output_root = tmp_path / "output"

    with pytest.raises(ValueError, match="incomplete"):
        ingest_manifest(manifest_path, output_root, repo_root=ROOT)
    with pytest.raises(ValueError, match="incomplete"):
        ingest_manifest(manifest_path, output_root, repo_root=ROOT)

    assert len(list((output_root / "capture-receipts").glob("*.json"))) == 1
    assert len(list((output_root / "artifacts" / "manifests").rglob("*.json"))) == 1


def test_conflicting_replay_cannot_replace_preserved_capture_receipt(
    tmp_path: Path,
) -> None:
    receipt = _receipt_bound_to_fixture_source(_incomplete_receipt("fixture://conflict"))
    manifest_path = _single_conversation_manifest(tmp_path, receipt)
    output_root = tmp_path / "output"

    with pytest.raises(ValueError, match="incomplete"):
        ingest_manifest(manifest_path, output_root, repo_root=ROOT)

    conflicting = _receipt_bound_to_fixture_source(
        _incomplete_receipt("fixture://conflict")
    )
    conflicting["continuation"]["attempts"][0]["status_code"] = 429
    conflicting_manifest = _single_conversation_manifest(tmp_path, conflicting)
    with pytest.raises(RuntimeError, match="immutable output conflict"):
        ingest_manifest(conflicting_manifest, output_root, repo_root=ROOT)

    assert len(list((output_root / "capture-receipts").glob("*.json"))) == 1


def test_shared_chat_import_refuses_unknown_capture_before_writing(tmp_path: Path) -> None:
    receipt = _receipt_bound_to_fixture_source(_incomplete_receipt(
        "https://chatgpt.com/share/6a7f2a03-f38c-83ea-b364-402c11090417?ogimg=plain"
    ))
    receipt["capture_id"] = "capture_unknown_001"
    receipt["completeness"] = "unknown"
    receipt["continuation"]["termination_reason"] = "unknown"
    manifest_path = _single_conversation_manifest(tmp_path, receipt)
    output_root = tmp_path / "output"

    with pytest.raises(ValueError, match="capture|completeness|unknown"):
        ingest_manifest(manifest_path, output_root, repo_root=ROOT)

    assert not list((output_root / "events").rglob("evt_*.json"))
    assert not list((output_root / "conversations").rglob("conv_*.json"))
    assert len(list((output_root / "capture-receipts").glob("*.json"))) == 1


def test_shared_chat_import_accepts_mechanically_complete_capture(tmp_path: Path) -> None:
    external_ref = "https://chatgpt.com/share/6a7f2a03-f38c-83ea-b364-402c11090417?ogimg=plain"
    receipt = _receipt_bound_to_fixture_source(_complete_receipt(external_ref))
    manifest_path = _single_conversation_manifest(tmp_path, receipt)
    output_root = tmp_path / "output"

    results = ingest_manifest(manifest_path, output_root, repo_root=ROOT)

    assert len(results) == 1
    assert len(list((output_root / "events").rglob("evt_*.json"))) == 1
    assert len(list((output_root / "conversations").rglob("conv_*.json"))) == 1
    stored_receipt = json.loads(
        next((output_root / "capture-receipts").glob("*.json")).read_text(encoding="utf-8")
    )
    assert stored_receipt["source"]["artifact_id"] == results[0]["artifact_ids"][0]


def test_shared_chat_import_refuses_complete_receipt_when_source_hash_differs(
    tmp_path: Path,
) -> None:
    receipt = _receipt_bound_to_fixture_source(_complete_receipt("fixture://hash-mismatch"))
    receipt["source"]["sha256"] = "b" * 64
    manifest_path = _single_conversation_manifest(tmp_path, receipt)
    output_root = tmp_path / "output"

    with pytest.raises(ValueError, match="sha256"):
        ingest_manifest(manifest_path, output_root, repo_root=ROOT)

    assert not list((output_root / "capture-receipts").rglob("*.json"))
    assert not list((output_root / "events").rglob("evt_*.json"))
    assert not list((output_root / "conversations").rglob("conv_*.json"))


def test_shared_chat_import_refuses_complete_receipt_when_byte_count_differs(
    tmp_path: Path,
) -> None:
    receipt = _receipt_bound_to_fixture_source(_complete_receipt("fixture://byte-mismatch"))
    receipt["source"]["byte_count"] += 1
    manifest_path = _single_conversation_manifest(tmp_path, receipt)
    output_root = tmp_path / "output"

    with pytest.raises(ValueError, match="byte_count"):
        ingest_manifest(manifest_path, output_root, repo_root=ROOT)

    assert not list((output_root / "capture-receipts").rglob("*.json"))
    assert not list((output_root / "events").rglob("evt_*.json"))
    assert not list((output_root / "conversations").rglob("conv_*.json"))


def test_shared_chat_import_refuses_declared_artifact_identity_mismatch(
    tmp_path: Path,
) -> None:
    receipt = _receipt_bound_to_fixture_source(_complete_receipt("fixture://artifact-mismatch"))
    receipt["source"]["artifact_id"] = "art_" + ("d" * 32)
    manifest_path = _single_conversation_manifest(tmp_path, receipt)
    output_root = tmp_path / "output"

    with pytest.raises(ValueError, match="artifact"):
        ingest_manifest(manifest_path, output_root, repo_root=ROOT)

    assert not list((output_root / "capture-receipts").rglob("*.json"))
    assert not list((output_root / "events").rglob("evt_*.json"))
    assert not list((output_root / "conversations").rglob("conv_*.json"))


def test_capture_source_binding_records_the_stored_artifact_identity() -> None:
    source_bytes = b"capture source bytes"
    receipt = _complete_receipt("fixture://binding")
    receipt["source"]["sha256"] = hashlib.sha256(source_bytes).hexdigest()
    receipt["source"]["byte_count"] = len(source_bytes)

    bound = bind_shared_chat_capture_source(
        receipt,
        source_bytes,
        schema_path=RECEIPT_SCHEMA,
    )

    assert bound["source"]["artifact_id"] == "art_" + hashlib.sha256(source_bytes).hexdigest()[:32]

    rebound = bind_shared_chat_capture_source(
        bound,
        source_bytes,
        artifact_id=bound["source"]["artifact_id"],
        schema_path=RECEIPT_SCHEMA,
    )
    assert rebound["source"]["artifact_id"] == bound["source"]["artifact_id"]


def test_capture_source_binding_rejects_wrong_stored_artifact_identity() -> None:
    source_bytes = b"capture source bytes"
    receipt = _complete_receipt("fixture://stored-artifact-mismatch")
    receipt["source"]["sha256"] = hashlib.sha256(source_bytes).hexdigest()
    receipt["source"]["byte_count"] = len(source_bytes)

    with pytest.raises(ValueError, match="stored capture artifact"):
        bind_shared_chat_capture_source(
            receipt,
            source_bytes,
            artifact_id="art_" + ("d" * 32),
            schema_path=RECEIPT_SCHEMA,
        )


def test_capture_receipt_format_is_validated_before_source_binding() -> None:
    source_bytes = b"capture source bytes"
    receipt = _complete_receipt("fixture://bad-date")
    receipt["source"]["sha256"] = hashlib.sha256(source_bytes).hexdigest()
    receipt["source"]["byte_count"] = len(source_bytes)
    receipt["source"]["captured_at"] = "not-a-date"

    with pytest.raises(ValidationError):
        validate_shared_chat_capture_receipt(receipt, schema_path=RECEIPT_SCHEMA)


def test_capture_receipt_validator_rejects_current_node_outside_discovered_graph() -> None:
    receipt = _complete_receipt("fixture://missing-current-validation")
    receipt["graph"]["current_node_id"] = "missing-current"

    with pytest.raises(SharedChatCaptureError, match="current_node_id"):
        validate_shared_chat_capture_receipt(receipt, schema_path=RECEIPT_SCHEMA)


def test_capture_receipt_validator_does_not_alias_nested_input() -> None:
    receipt = _complete_receipt("fixture://validator-copy")
    validated = validate_shared_chat_capture_receipt(receipt, schema_path=RECEIPT_SCHEMA)

    validated["graph"]["discovered_node_ids"].append("late-node")
    assert receipt["graph"]["discovered_node_ids"] == ["node_root", "node_message"]
