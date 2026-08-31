from __future__ import annotations

import copy

import pytest

from fossil_core.application.query.visibility import (
    CallerVisibility,
    RetrievalVisibilityPolicy,
    SecurityFilteredRetriever,
    VisibilityDenied,
)


def _doc(identifier: str, **overrides):
    value = {
        "id": identifier,
        "pack_id": "pack_a",
        "text": f"text for {identifier}",
        "acl": ["alice"],
        "sensitivity": "internal",
        "suppressed": False,
        "redacted": False,
        "citation": {"citation_id": f"cite_{identifier}"},
    }
    value.update(overrides)
    return value


@pytest.fixture
def caller():
    return CallerVisibility(
        principal_id="alice",
        readable_pack_ids=frozenset({"pack_a"}),
        allowed_sensitivity=frozenset({"internal"}),
    )


def test_visibility_filters_acl_sensitivity_suppression_redaction_and_pack(caller):
    documents = [
        _doc("allow"),
        _doc("acl", acl=["bob"]),
        _doc("sensitivity", sensitivity="restricted"),
        _doc("suppressed", suppressed=True),
        _doc("redacted", redacted=True),
        _doc("foreign", pack_id="pack_b"),
        _doc("missing-acl", acl=[]),
    ]
    visible, denied = RetrievalVisibilityPolicy().visible_documents(documents, caller)
    assert [item["id"] for item in visible] == ["allow"]
    assert denied == {
        "acl_denied": 1,
        "missing_acl": 1,
        "pack_not_readable": 1,
        "redacted": 1,
        "sensitivity_denied": 1,
        "suppressed": 1,
    }


def test_direct_read_citation_and_export_fail_closed(caller):
    policy = RetrievalVisibilityPolicy()
    allowed = _doc("allowed")
    denied = _doc("denied", acl=["bob"])
    documents = [allowed, denied]

    assert policy.read("allowed", documents, caller)["id"] == "allowed"
    with pytest.raises(VisibilityDenied):
        policy.read("denied", documents, caller)
    with pytest.raises(VisibilityDenied):
        policy.authorize_citation(
            denied["citation"], document_id="denied", documents=documents, caller=caller
        )
    assert policy.authorize_citation(
        allowed["citation"], document_id="allowed", documents=documents, caller=caller
    ) == allowed["citation"]
    assert policy.export_document(denied, caller) is None
    assert [item["id"] for item in policy.export_documents(documents, caller)] == ["allowed"]


def test_fail_closed_result_boundary_rejects_leaky_retriever(caller):
    denied = _doc("denied", acl=["bob"])

    class Leaky:
        def metadata(self):
            return {"implementation": "leaky-fixture"}

        def search(self, query, *, pack_ids, limit):
            return [copy.deepcopy(denied)]

    secured = SecurityFilteredRetriever(
        Leaky(), policy=RetrievalVisibilityPolicy(), caller=caller
    )
    with pytest.raises(VisibilityDenied):
        secured.search("secret", pack_ids=["pack_a"], limit=1)
    with pytest.raises(VisibilityDenied):
        secured.search("secret", pack_ids=["pack_b"], limit=1)


def test_projection_rebuild_filters_before_index_and_preserves_visible_ids(caller):
    policy = RetrievalVisibilityPolicy()
    projection = [_doc("allowed"), _doc("suppressed", suppressed=True), _doc("foreign", pack_id="pack_b")]
    first, first_denied = policy.visible_documents(projection, caller)
    rebuilt, rebuilt_denied = policy.visible_documents(copy.deepcopy(projection), caller)
    assert [item["id"] for item in first] == ["allowed"]
    assert [item["id"] for item in rebuilt] == ["allowed"]
    assert first_denied == rebuilt_denied
    assert all(item["id"] not in {"suppressed", "foreign"} for item in rebuilt)
