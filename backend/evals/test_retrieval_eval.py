"""Eval, not a unit test: queries the live Chroma vector store (embeddings
already ingested via `python -m app.rag.ingest`) for every case. Run
explicitly with `pytest evals/`, not part of the default `pytest` run.

Thresholds here are deliberately not "100% recall" -- retrieval precision on
this schema is a known limitation: all 9 tables
live in one HR/payroll domain, so embedding distances cluster tightly and a
borderline-relevant table occasionally edges out a more relevant one. These
thresholds are canaries for a real regression (e.g. retrieval breaking and
returning nothing relevant), not a claim that retrieval is perfect.
"""

import pytest

from app.rag.retriever import retrieve_schema_chunks

# Candidate prompts from planning/03a-reference-schema-design.md, narrowed to
# each prompt's one or two most essential tables -- sized to be achievable
# within top_k=5, not the full aspirational table list.
CASES = [
    pytest.param(
        "Process payroll for a given pay period",
        {"payroll_runs", "payroll_entries"},
        id="process_payroll",
    ),
    pytest.param(
        "Onboard a new employee and record their starting salary",
        {"employees", "salary_history"},
        id="onboard_employee",
    ),
    pytest.param(
        "Approve a timesheet and recalculate overtime pay",
        {"timesheets"},
        id="approve_timesheet",
    ),
    pytest.param(
        "Terminate an employee and reverse their pending payroll entry",
        {"employees", "payroll_entries"},
        id="terminate_employee",
    ),
]


def _recall(retrieved_tables: set[str], expected_tables: set[str]) -> float:
    return len(retrieved_tables & expected_tables) / len(expected_tables)


@pytest.mark.eval
@pytest.mark.parametrize("prompt, expected_tables", CASES)
def test_retrieval_recall_meets_minimum_bar(prompt, expected_tables):
    chunks = retrieve_schema_chunks(prompt, top_k=5)
    retrieved_tables = {chunk.metadata["table_name"] for chunk in chunks}

    recall = _recall(retrieved_tables, expected_tables)

    assert recall >= 0.5, (
        f"prompt={prompt!r} expected at least half of {expected_tables} in top-5, "
        f"got {retrieved_tables} (recall={recall:.2f})"
    )


@pytest.mark.eval
def test_aggregate_retrieval_recall_across_golden_set():
    """A stricter aggregate check across the whole golden set -- one weak
    prompt shouldn't fail the suite (see the per-case test above for that),
    but the average across all of them dropping meaningfully below what was
    measured during development would indicate a real regression."""
    recalls = []
    for case in CASES:
        prompt, expected_tables = case.values
        chunks = retrieve_schema_chunks(prompt, top_k=5)
        retrieved_tables = {chunk.metadata["table_name"] for chunk in chunks}
        recalls.append(_recall(retrieved_tables, expected_tables))

    aggregate_recall = sum(recalls) / len(recalls)
    assert aggregate_recall >= 0.6, f"aggregate recall dropped to {aggregate_recall:.2f} across {CASES}"
