from __future__ import annotations

import json
from pathlib import Path

from fossil_core.artifact_store import ArtifactStore
from fossil_core.conversation import ConversationStore
from fossil_core.event_store import DurableEventStore
from fossil_core.pack_fixture import validate_pack_fixtures
from scripts.ingest_study_os_shared_capture import ingest_capture


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "examples" / "shared-chat-ingestion" / "2026-08-29-study-os.json"
PACK = ROOT / "examples" / "packs" / "study-os-personal"


def test_study_os_capture_ingests_complete_reconstructed_projection_idempotently(tmp_path):
    first = ingest_capture(MANIFEST, tmp_path / "pack", repo_root=ROOT)
    second = ingest_capture(MANIFEST, tmp_path / "pack", repo_root=ROOT)

    assert first == second
    assert first["source_status"] == "reconstructed"
    assert first["serialized_node_count"] == 951
    assert first["projected_message_count"] == 703
    assert len(first["claim_proposal_event_ids"]) == 8

    conversation_path = (
        tmp_path
        / "pack"
        / "conversations"
        / "st"
        / "conv_study_os_shared_6a90f29fc14c83e.json"
    )
    assert conversation_path.is_file()
    envelope = json.loads(conversation_path.read_text(encoding="utf-8"))
    assert envelope["source_status"] == "reconstructed"
    assert len(envelope["messages"]) == 703
    assert all(message["evidence_status"] == "reconstructed" for message in envelope["messages"])
    assert envelope["sources"][0]["external_ref"].startswith("https://chatgpt.com/share/")

    artifact_store = ArtifactStore(tmp_path / "pack" / "artifacts")
    assert artifact_store.verify(first["source_artifact_id"])
    assert artifact_store.read_bytes(first["source_artifact_id"]) == (
        ROOT / "docs" / "recovery" / "2026-08-29-study-os-shared-conversation.md"
    ).read_bytes()

    event_store = DurableEventStore(
        tmp_path / "pack" / "events", ROOT / "schemas" / "events" / "v1.schema.json"
    )
    events = list(event_store.iter_events())
    assert len(events) == 9
    assert {event["event_type"] for event in events} == {
        "conversation.ingested",
        "claim.proposed",
    }
    assert all(event["pack_id"] == first["pack_id"] for event in events)
    assert all(
        event["provenance"]["method"]
        in {
            "reconstructed_shared_chat_import",
            "reconstructed_shared_chat_study_methodology_import",
        }
        for event in events
    )


def test_study_os_pack_fixture_validates_and_keeps_claims_proposal_only():
    audit = validate_pack_fixtures([PACK], schemas_root=ROOT / "schemas")

    assert audit.pack_ids == ("pack_study_os_personal_6a90f29fc14c83e",)
    assert audit.artifact_count == 1
    assert audit.snapshot_count == 1
    assert audit.event_count == 9
    assert audit.citation_count == 8
    assert audit.claim_count == 8
    assert all(
        event["event_type"] == "claim.proposed"
        for event in DurableEventStore(
            PACK / "events", ROOT / "schemas" / "events" / "v1.schema.json"
        ).iter_events()
        if event["event_type"] != "conversation.ingested"
    )
