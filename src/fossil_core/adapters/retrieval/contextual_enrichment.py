from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from typing import Any


CONTEXTUALIZER_MODEL = "fossil-deterministic-source-contextualizer-v1"
CONTEXTUALIZER_PROVIDER = "fossil"
CONTEXTUALIZER_RUNTIME = "local-deterministic-source-only"
CONTEXTUALIZER_PROMPT = """Given the source document and this specific chunk, produce a short factual context that situates the chunk within the document for retrieval purposes. Identify only information supported by the supplied source, such as subject, document/section role, time period, entity, decision context, or relationship needed to understand what the chunk refers to. Do not answer a user query. Do not add unsupported facts. Do not change lifecycle or authority status. Return only the short retrieval context."""
CONTEXTUALIZER_PROMPT_HASH = hashlib.sha256(
    CONTEXTUALIZER_PROMPT.encode("utf-8")
).hexdigest()
GENERATION_PARAMETERS = {
    "temperature": 0.0,
    "query_specific": False,
    "source_only": True,
}

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_text(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    )


def _source_payload(document: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(document.get("id", "")),
        "pack_id": str(document.get("pack_id", "")),
        "text": str(document.get("text", "")),
        "document_type": str(document.get("document_type", "")),
        "current_state": str(document.get("current_state", "")),
        "state_history": [str(value) for value in document.get("state_history", [])],
        "relation_type": str(document.get("relation_type", "")),
        "source_ref": str(document.get("source_ref", "")),
        "target_ref": str(document.get("target_ref", "")),
    }


def _pack_label(pack_id: str) -> str:
    if pack_id == "pack_269099f7b2ba43b7a99b9427d64092de":
        return "common"
    if pack_id == "pack_f024177f89a5442db84171c3dd7f58e5":
        return "ai-systems"
    return pack_id


def _topic_tokens(text: str, *, limit: int = 18) -> str:
    values = [match.group(0).lower() for match in _TOKEN_RE.finditer(text)]
    return " ".join(values[:limit])


def generate_context(document: Mapping[str, Any]) -> str:
    """Generate deterministic, source-only retrieval context for one document."""

    payload = _source_payload(document)
    kind = payload["document_type"] or "source document"
    pack = _pack_label(payload["pack_id"])
    state = payload["current_state"] or "unspecified"
    history = " -> ".join(payload["state_history"]) or "not supplied"
    topic = _topic_tokens(payload["text"])
    if kind == "relation":
        relation = payload["relation_type"] or "unspecified"
        relationship = (
            f"It records the {relation} relationship from canonical object "
            f"{payload['source_ref'] or 'unspecified'} to "
            f"{payload['target_ref'] or 'unspecified'}."
        )
    else:
        relationship = "It is a source-backed claim document, not a query answer."
    return (
        f"This FOSSIL {kind} belongs to the {pack} knowledge pack. "
        f"Its lifecycle state is {state}, with recorded history {history}. "
        f"{relationship} The source-derived topic terms are: {topic}. "
        "This generated prefix is a non-authoritative retrieval hint; the exact source chunk below remains the answer material."
    )


def contextual_projection_id(
    documents: Iterable[Mapping[str, Any]], pack_revisions: Mapping[str, str]
) -> str:
    seeds = [
        {
            "source": _source_payload(document),
            "pack_revision": str(pack_revisions[str(document.get("pack_id", ""))]),
        }
        for document in sorted(documents, key=lambda item: str(item.get("id", "")))
    ]
    fingerprint = _sha256_json(
        {
            "contextualizer_model": CONTEXTUALIZER_MODEL,
            "prompt_hash": CONTEXTUALIZER_PROMPT_HASH,
            "generation_parameters": GENERATION_PARAMETERS,
            "seeds": seeds,
        }
    )[:24]
    return f"contextual-source-{fingerprint}"


def build_context_records(
    documents: Iterable[Mapping[str, Any]],
    *,
    pack_revisions: Mapping[str, str],
    build_id: str | None = None,
) -> list[dict[str, Any]]:
    document_list = [copy.deepcopy(dict(document)) for document in documents]
    projection_id = build_id or contextual_projection_id(document_list, pack_revisions)
    records: list[dict[str, Any]] = []
    for document in sorted(document_list, key=lambda item: str(item.get("id", ""))):
        payload = _source_payload(document)
        generated_context = generate_context(document)
        source_hash = _sha256_json(payload)
        original_chunk_hash = _sha256_text(payload["text"])
        enriched_representation = (
            f"<context>\n{generated_context}\n</context>\n\n"
            f"<source_chunk>\n{payload['text']}\n</source_chunk>"
        )
        records.append(
            {
                "record_type": "contextual_enrichment",
                "canonical_chunk_id": payload["id"],
                "canonical_source_id": payload["id"],
                "source_hash": source_hash,
                "original_chunk_hash": original_chunk_hash,
                "pack_id": payload["pack_id"],
                "pack_revision": str(pack_revisions[payload["pack_id"]]),
                "enrichment_prompt_hash": CONTEXTUALIZER_PROMPT_HASH,
                "contextualizer_requested_model": CONTEXTUALIZER_MODEL,
                "contextualizer_actual_model": CONTEXTUALIZER_MODEL,
                "provider_runtime_identity": {
                    "provider": CONTEXTUALIZER_PROVIDER,
                    "runtime": CONTEXTUALIZER_RUNTIME,
                },
                "generation_parameters": dict(GENERATION_PARAMETERS),
                "generated_context": generated_context,
                "generated_context_hash": _sha256_text(generated_context),
                "enriched_representation_hash": _sha256_text(enriched_representation),
                "build_id": projection_id,
                "original_text": payload["text"],
                "enriched_representation": enriched_representation,
            }
        )
    return records


def contextual_documents(
    documents: Iterable[Mapping[str, Any]], records: Iterable[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    by_id = {str(record["canonical_chunk_id"]): dict(record) for record in records}
    enriched: list[dict[str, Any]] = []
    for source in documents:
        document = copy.deepcopy(dict(source))
        record = by_id[str(document["id"])]
        document["source_text"] = str(document["text"])
        document["text"] = str(record["enriched_representation"])
        document["contextual_enrichment"] = {
            "resolver": "fossil-contextual-retrieval-v1",
            "build_id": str(record["build_id"]),
            "source_hash": str(record["source_hash"]),
            "generated_context_hash": str(record["generated_context_hash"]),
            "enriched_representation_hash": str(
                record["enriched_representation_hash"]
            ),
            "generated_context_is_non_authoritative": True,
        }
        enriched.append(document)
    return enriched


def audit_context_records(
    documents: Iterable[Mapping[str, Any]],
    records: Iterable[Mapping[str, Any]],
    *,
    pack_revisions: Mapping[str, str],
) -> dict[str, Any]:
    document_by_id = {str(document["id"]): dict(document) for document in documents}
    record_list = [dict(record) for record in records]
    supported = 0
    unsupported = 0
    cross_pack = 0
    lifecycle_override = 0
    source_identity_corruption = 0
    query_answer_leakage = 0
    for record in record_list:
        identifier = str(record.get("canonical_chunk_id", ""))
        document = document_by_id.get(identifier)
        if document is None:
            unsupported += 1
            continue
        regenerated = generate_context(document)
        expected = build_context_records(
            [document], pack_revisions=pack_revisions, build_id=str(record["build_id"])
        )[0]
        if str(record.get("generated_context", "")) == regenerated and all(
            record.get(key) == expected.get(key)
            for key in (
                "source_hash",
                "original_chunk_hash",
                "generated_context_hash",
                "enriched_representation_hash",
                "enriched_representation",
            )
        ):
            supported += 1
        else:
            unsupported += 1
        if str(record.get("pack_id", "")) != str(document.get("pack_id", "")):
            cross_pack += 1
        if (
            str(document.get("current_state", "")) not in str(record["generated_context"])
            or "lifecycle state" not in str(record["generated_context"])
        ):
            lifecycle_override += 1
        if str(record.get("canonical_source_id", "")) != identifier:
            source_identity_corruption += 1
        if "query" in record or "question" in record or "answer" in record:
            query_answer_leakage += 1
    total = len(record_list)
    return {
        "context_count": total,
        "supported_context_count": supported,
        "unsupported_context_count": unsupported,
        "cross_pack_violation_count": cross_pack,
        "lifecycle_override_count": lifecycle_override,
        "source_identity_corruption_count": source_identity_corruption,
        "query_answer_leakage_count": query_answer_leakage,
        "passed": (
            total == len(document_by_id)
            and supported == total
            and unsupported == 0
            and cross_pack == 0
            and lifecycle_override == 0
            and source_identity_corruption == 0
            and query_answer_leakage == 0
        ),
    }


__all__ = [
    "CONTEXTUALIZER_MODEL",
    "CONTEXTUALIZER_PROVIDER",
    "CONTEXTUALIZER_RUNTIME",
    "CONTEXTUALIZER_PROMPT",
    "CONTEXTUALIZER_PROMPT_HASH",
    "GENERATION_PARAMETERS",
    "audit_context_records",
    "build_context_records",
    "contextual_documents",
    "contextual_projection_id",
    "generate_context",
]
