"""Eval, not a unit test: runs the full live pipeline (classifier, retrieval,
generation) for each golden prompt. Each generation takes roughly 130-180s and
costs real tokens. Run explicitly with `pytest evals/`, not part of the
default `pytest` run.

Guards the section contract, not SQL quality. The defect this exists to catch:
the model emitted the full CREATE PROCEDURE under ROLLBACK_AUTO and left
MAIN_SCRIPT a 98-240 char fragment, while the old validator still reported
`sql_valid: true`.
"""

from concurrent.futures import ThreadPoolExecutor

import pytest

from app.rag.generator import generate_stored_procedure
from app.rag.validator import _PROCEDURE_DECLARATION_RE

# Same four prompts as the retrieval eval's golden set.
GOLDEN_PROMPTS = {
    "process_payroll": "Process payroll for a given pay period",
    "onboard_employee": "Onboard a new employee and record their starting salary",
    "approve_timesheet": "Approve a timesheet and recalculate overtime pay",
    "terminate_employee": "Terminate an employee and reverse their pending payroll entry",
}


@pytest.fixture(scope="module")
def generations() -> dict[str, dict]:
    """Generates all four at once: sequentially this eval would take ~11 min."""
    with ThreadPoolExecutor(max_workers=len(GOLDEN_PROMPTS)) as pool:
        futures = {case: pool.submit(generate_stored_procedure, prompt) for case, prompt in GOLDEN_PROMPTS.items()}
        return {case: future.result() for case, future in futures.items()}


@pytest.mark.eval
@pytest.mark.parametrize("case", GOLDEN_PROMPTS)
def test_main_procedure_lands_in_main_script(generations, case):
    sections = generations[case]["sections"]

    assert _PROCEDURE_DECLARATION_RE.search(sections["main_script"]), (
        f"{case}: MAIN_SCRIPT has no procedure declaration ({len(sections['main_script'])} chars)"
    )
    assert not _PROCEDURE_DECLARATION_RE.search(sections["rollback_auto"]), (
        f"{case}: ROLLBACK_AUTO declares a procedure ({len(sections['rollback_auto'])} chars)"
    )
    assert "EXCEPTION" in sections["rollback_auto"].upper(), f"{case}: ROLLBACK_AUTO has no EXCEPTION handler"


@pytest.mark.eval
@pytest.mark.parametrize("case", GOLDEN_PROMPTS)
def test_generation_passes_validator(generations, case):
    result = generations[case]
    section_sizes = {name: len(body) for name, body in result["sections"].items()}

    assert result["sql_valid"], f"{case}: {result['sql_issues']} section sizes={section_sizes}"
