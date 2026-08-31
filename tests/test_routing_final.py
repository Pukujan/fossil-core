from __future__ import annotations

from scripts.run_fossil_routing_final import QUERY_CLASSES, classify_query


def test_query_classifier_is_small_deterministic_and_covers_declared_classes():
    queries = {
        "Which pack contains the model claim?": "exact-identifier",
        "Why does durable evidence matter?": "conceptual",
        "What is the current accepted architecture?": "current-latest",
        "What was the former SQLite conclusion and its supersession relation?": "lineage-history",
        "Retrieve the former conclusion, current architecture, and their relation.": "lineage-history",
        "What source citation snapshot supports this answer?": "direct-source-read",
    }
    assert set(QUERY_CLASSES) == {
        "exact-identifier",
        "conceptual",
        "current-latest",
        "lineage-history",
        "broad-synthesis",
        "direct-source-read",
    }
    assert {classify_query(query) for query in queries} <= set(QUERY_CLASSES)
    assert all(classify_query(query) == expected for query, expected in queries.items())


def test_classifier_has_no_runtime_or_model_input():
    assert classify_query("What is the current role of Graphiti?") == "current-latest"
    assert classify_query("What is the current role of Graphiti?") == "current-latest"
