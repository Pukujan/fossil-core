"""Ingest a reconstructed Study OS shared-chat capture into a personal pack.

The input is a captured readable projection, not an original ChatGPT export.
This importer therefore keeps every imported message and derived claim marked as
``reconstructed`` and records the public share URL and capture hashes as
provenance. It intentionally creates proposal events only; it never promotes
the imported methodology into a shared or architectural pack.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from fossil_core.artifact_store import ArtifactStore
from fossil_core.conversation import ConversationLineage, ConversationStore
from fossil_core.event_store import DurableEventStore
from fossil_core.io import publish_immutable
from fossil_core.source import SourceSnapshotStore


HEADER = re.compile(
    r"^## \[message:(?P<number>\d+)\] role=(?P<role>\S+) "
    r"content_type=(?P<content_type>\S+) node_id=(?P<node_id>\S+)$",
    re.MULTILINE,
)


def _canonical(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def _publish_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _canonical(value)
    if publish_immutable(path, data):
        return
    existing = json.loads(path.read_text(encoding="utf-8"))
    if _canonical(existing) != data:
        raise RuntimeError(f"immutable output conflict: {path}")


def _publish_text(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if publish_immutable(path, data):
        return
    if path.read_bytes() != data:
        raise RuntimeError(f"immutable output conflict: {path}")


def _stable_id(prefix: str, *parts: str, length: int = 24) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:length]}"


def _parse_projection(text: str) -> list[dict[str, Any]]:
    matches = list(HEADER.finditer(text))
    if not matches:
        raise ValueError("capture projection contains no message headers")

    records: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        block_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[match.start() : block_end]
        body = text[match.end() : block_end].strip()
        if not body:
            raise ValueError(f"message {match.group('number')} has empty content")
        records.append(
            {
                "number": match.group("number"),
                "role": match.group("role"),
                "content_type": match.group("content_type"),
                "node_id": match.group("node_id"),
                "block": block,
                "text": body,
            }
        )
    return records


def _actor(role: str) -> dict[str, str | None]:
    role_map = {"user": "human", "assistant": "assistant", "tool": "tool"}
    normalized = role_map.get(role, "other")
    return {
        "actor_id": {
            "human": "shared-chat-user",
            "assistant": "chatgpt-share-rendered-response",
            "tool": "chatgpt-share-tool-output",
        }.get(normalized, f"shared-chat-{normalized}"),
        "role": normalized,
        "provider": "ChatGPT share page" if normalized in {"assistant", "tool"} else None,
        "model_id": None,
        "run_id": None,
        "tool_id": None,
    }


def _write_artifact_index(output_root: Path) -> None:
    manifests = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((output_root / "artifacts" / "manifests").glob("*/*.json"))
    ]
    data = b"".join(_canonical(item) for item in manifests)
    _publish_text(output_root / "artifacts" / "manifest.jsonl", data)


def _message_map(
    records: list[dict[str, Any]],
    *,
    conversation_id: str,
    source: dict[str, Any],
    conversation_store: ConversationStore,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, tuple[str, str, tuple[int, int]]]]:
    messages: list[dict[str, Any]] = []
    spans: list[dict[str, Any]] = []
    by_number: dict[str, tuple[str, str, tuple[int, int]]] = {}
    previous_message_id: str | None = None
    for sequence, record in enumerate(records):
        span = conversation_store.span_for_text(source, record["block"])
        message_id = _stable_id(
            "msg", conversation_id, record["number"], record["node_id"]
        )
        messages.append(
            {
                "message_id": message_id,
                "sequence": sequence,
                "parent_message_id": previous_message_id,
                "occurred_at": None,
                "actor": _actor(record["role"]),
                "evidence_status": "reconstructed",
                "text": record["text"],
                "source_span_refs": [span["span_id"]],
            }
        )
        spans.append(span)
        by_number[record["number"]] = (
            message_id,
            span["span_id"],
            (span["byte_start"], span["byte_end"]),
        )
        previous_message_id = message_id
    return messages, spans, by_number


def _build_lineage(
    spec: dict[str, Any],
    *,
    conversation_id: str,
    by_number: dict[str, tuple[str, str, tuple[int, int]]],
) -> dict[str, Any]:
    nodes = []
    for node_spec in spec["lineage"]["nodes"]:
        number = str(node_spec["message_number"])
        try:
            message_id, span_id, _ = by_number[number]
        except KeyError as exc:
            raise ValueError(f"lineage references missing message {number}") from exc
        nodes.append(
            {
                "node_id": node_spec["node_id"],
                "kind": node_spec["kind"],
                "label": node_spec["label"],
                "text": node_spec["text"],
                "evidence_status": "reconstructed",
                "position_state": node_spec["position_state"],
                "source_message_refs": [message_id],
                "source_span_refs": [span_id],
            }
        )
    lineage = {
        "schema_version": "fossil.conversation-lineage.v1",
        "lineage_id": spec["lineage"]["lineage_id"],
        "conversation_id": conversation_id,
        "nodes": nodes,
        "edges": copy.deepcopy(spec["lineage"]["edges"]),
        "current_conclusion_refs": copy.deepcopy(
            spec["lineage"]["current_conclusion_refs"]
        ),
    }
    return lineage


def _claim_event(
    *,
    event_store: DurableEventStore,
    pack_id: str,
    claim: dict[str, Any],
    citation: dict[str, Any],
    artifact_id: str,
    snapshot_id: str,
    observed_at: str,
    import_id: str,
) -> dict[str, Any]:
    return event_store.commit(
        {
            "schema_version": "dkg.event.v1",
            "event_type": "claim.proposed",
            "occurred_at": observed_at,
            "recorded_at": observed_at,
            "pack_id": pack_id,
            "actor": {
                "actor_type": "importer",
                "actor_id": "study-os-shared-capture-importer",
                "harness_version": "study-os-shared-capture-v1",
                "skill_id": "skill_research-ingestion",
                "skill_version": "1.0.0",
            },
            "subject_refs": [claim["claim_id"]],
            "caused_by_event_ids": [],
            "correlation_id": import_id,
            "idempotency_key": claim["idempotency_key"],
            "evidence_refs": [artifact_id],
            "source_snapshot_refs": [snapshot_id],
            "payload": {"claim_text": claim["text"], "citation": citation},
            "provenance": {
                "method": "reconstructed_shared_chat_study_methodology_import",
                "prompt_or_policy_ref": "skills/research-ingestion/manifest.json",
            },
        }
    )


def ingest_capture(
    manifest_path: Path,
    output_root: Path,
    *,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    manifest_path = Path(manifest_path)
    repo_root = Path(repo_root or manifest_path.parents[2])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    capture_meta = json.loads(
        (repo_root / manifest["capture_manifest_path"]).read_text(encoding="utf-8")
    )
    if capture_meta["evidence_status"] != "reconstructed":
        raise ValueError("Study OS shared capture must remain reconstructed")
    if capture_meta["source_url"] != manifest["external_ref"]:
        raise ValueError("capture manifest and import manifest disagree on source URL")

    source_path = repo_root / manifest["source_path"]
    source_text = source_path.read_text(encoding="utf-8")
    records = _parse_projection(source_text)
    expected_count = int(capture_meta["projected_message_count"])
    if len(records) != expected_count:
        raise ValueError(
            f"projection message count mismatch: expected {expected_count}, got {len(records)}"
        )

    output_root = Path(output_root)
    artifact_store = ArtifactStore(output_root / "artifacts")
    conversation_store = ConversationStore(
        output_root / "conversations",
        artifact_store,
        repo_root / "schemas" / "conversation" / "v1.schema.json",
    )
    event_store = DurableEventStore(
        output_root / "events", repo_root / "schemas" / "events" / "v1.schema.json"
    )
    source_store = SourceSnapshotStore(
        output_root / "sources",
        artifact_store,
        repo_root / "schemas" / "source-snapshot" / "v1.schema.json",
        repo_root / "schemas" / "citation" / "v1.schema.json",
    )

    source_bytes = source_text.encode("utf-8")
    source = conversation_store.add_source(
        source_bytes,
        evidence_status="reconstructed",
        media_type="text/markdown",
        label=manifest["source_label"],
        external_ref=manifest["external_ref"],
    )
    messages, spans, by_number = _message_map(
        records,
        conversation_id=manifest["conversation_id"],
        source=source,
        conversation_store=conversation_store,
    )
    envelope = conversation_store.commit(
        {
            "schema_version": "fossil.conversation.v1",
            "conversation_id": manifest["conversation_id"],
            "source_status": "reconstructed",
            "title": manifest["title"],
            "reconstruction_basis_refs": manifest["reconstruction_basis_refs"],
            "sources": [source],
            "spans": spans,
            "messages": messages,
        }
    )

    lineage = _build_lineage(
        manifest,
        conversation_id=manifest["conversation_id"],
        by_number=by_number,
    )
    ConversationLineage(
        lineage,
        schema_path=repo_root / "schemas" / "conversation-lineage" / "v1.schema.json",
        conversation_store=conversation_store,
        envelope=envelope,
    )
    _publish_json(output_root / "lineages" / f"{lineage['lineage_id']}.json", lineage)

    snapshot = source_store.put_snapshot(
        source_bytes,
        locator={"url": manifest["external_ref"]},
        retrieved_at=manifest["observed_at"],
        source_role="local",
        quality={
            "authority": None,
            "directness": None,
            "independence": None,
            "reproducibility": None,
            "timeliness": None,
            "notes": (
                "Local readable projection reconstructed from a public shared-page payload; "
                "not an original export and not authority for accepted knowledge."
            ),
        },
        media_type="text/markdown",
    )

    ingested = conversation_store.build_ingested_event(
        envelope,
        pack_id=manifest["pack_id"],
        actor_id="study-os-shared-capture-importer",
        occurred_at=manifest["observed_at"],
        recorded_at=manifest["observed_at"],
    )
    ingested["actor"] = {
        "actor_type": "importer",
        "actor_id": "study-os-shared-capture-importer",
        "harness_version": "study-os-shared-capture-v1",
        "skill_id": "skill_research-ingestion",
        "skill_version": "1.0.0",
    }
    ingested["correlation_id"] = manifest["import_id"]
    ingested["payload_schema"] = "schemas/conversation/v1.schema.json"
    ingested["payload"]["lineage_id"] = lineage["lineage_id"]
    ingested["provenance"] = {
        "method": "reconstructed_shared_chat_import",
        "prompt_or_policy_ref": "skills/research-ingestion/manifest.json",
        "benchmark_ref": lineage["lineage_id"],
    }
    ingested_event = event_store.commit(ingested)

    claim_events = []
    for claim in manifest["claims"]:
        number = str(claim["message_number"])
        _, _, (byte_start, byte_end) = by_number[number]
        citation = source_store.create_citation(
            snapshot["snapshot_id"], byte_start=byte_start, byte_end=byte_end
        )
        claim_events.append(
            _claim_event(
                event_store=event_store,
                pack_id=manifest["pack_id"],
                claim=claim,
                citation=citation,
                artifact_id=source["artifact_id"],
                snapshot_id=snapshot["snapshot_id"],
                observed_at=manifest["observed_at"],
                import_id=manifest["import_id"],
            )
        )

    _write_artifact_index(output_root)
    receipt = {
        "schema_version": "fossil.study-os-import-receipt.v1",
        "import_id": manifest["import_id"],
        "pack_id": manifest["pack_id"],
        "conversation_id": envelope["conversation_id"],
        "lineage_id": lineage["lineage_id"],
        "source_status": envelope["source_status"],
        "source_artifact_id": source["artifact_id"],
        "source_snapshot_id": snapshot["snapshot_id"],
        "conversation_ingested_event_id": ingested_event["event_id"],
        "claim_proposal_event_ids": [event["event_id"] for event in claim_events],
        "serialized_node_count": capture_meta["serialized_node_count"],
        "projected_message_count": len(messages),
        "projected_role_counts": capture_meta["projected_roles"],
        "raw_capture_hashes": {
            "route_stream_sha256": capture_meta["raw_route_stream"]["sha256"],
            "decoded_payload_sha256": capture_meta["decoded_payload"]["sha256"],
            "readable_projection_sha256": capture_meta["readable_projection"]["sha256"],
        },
        "promotion_status": "not_promoted",
    }
    _publish_json(output_root / "import-receipt.json", receipt)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(ingest_capture(args.manifest, args.output), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
