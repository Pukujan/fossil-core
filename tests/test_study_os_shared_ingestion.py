from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fossil_core.artifact_store import ArtifactStore
from fossil_core.event_store import DurableEventStore
from scripts.ingest_shared_chat_reconstructions import ingest_manifest


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "examples" / "shared-chat-ingestion" / "2026-09-03-study-os-6a996a5d.json"
PACK = ROOT / "examples" / "packs" / "study-os-personal"


def test_newer_study_os_shared_page_ingests_as_reconstructed_and_idempotent(tmp_path):
    first = ingest_manifest(MANIFEST, tmp_path / "run", repo_root=ROOT)
    second = ingest_manifest(MANIFEST, tmp_path / "run", repo_root=ROOT)

    assert first == second
    assert first[0]["conversation_id"] == "conv_study_os_shared_6a996a5d178483ea"
    assert first[0]["lineage_id"] == "lin_study_os_continue_workflow_6a996a5d178483ea"

    envelope = json.loads(Path(first[0]["conversation_path"]).read_text(encoding="utf-8"))
    assert envelope["source_status"] == "reconstructed"
    assert len(envelope["messages"]) == 9
    assert all(message["evidence_status"] == "reconstructed" for message in envelope["messages"])
    assert "thats not the human in the loop i watned i wanted to check it directly" in envelope["messages"][5]["text"]

    artifact_store = ArtifactStore(tmp_path / "run" / "artifacts")
    assert artifact_store.verify(first[0]["artifact_ids"][0])
    event_store = DurableEventStore(
        tmp_path / "run" / "events", ROOT / "schemas" / "events" / "v1.schema.json"
    )
    events = list(event_store.iter_events())
    assert len(events) == 1
    assert events[0]["payload"]["source_status"] == "reconstructed"


def test_study_os_pack_retains_sanitized_capture_hash_and_proposal_status():
    pack = json.loads((PACK / "manifest.json").read_text(encoding="utf-8"))
    capture = PACK / pack["source_capture"]
    digest = hashlib.sha256(capture.read_bytes()).hexdigest()

    assert pack["pack_id"] == "pack_study_os_personal_6a996a5d178483ea"
    assert pack["evidence_status"] == "reconstructed"
    assert pack["promotion_status"] == "not_promoted"
    assert pack["source_capture_sha256"] == digest
    assert "sidebar" not in capture.read_text(encoding="utf-8").lower()
