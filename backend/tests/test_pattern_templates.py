"""The pattern corpus is retrieved into every generation prompt as "house
style for each section", so a template that breaks the section contract
teaches the model to break it too. That is how the full procedure ended up
under ROLLBACK_AUTO: its template used to be a complete CREATE PROCEDURE."""

import re

import pytest

from app.config import PATTERN_CATEGORIES, get_settings
from app.rag.section_parser import SECTION_ORDER
from app.rag.validator import _PROCEDURE_DECLARATION_RE, PROCEDURE_SECTIONS, validate_script


def _template(category: str) -> str:
    return (get_settings().sp_templates_dir / f"{category}.sql").read_text()


def test_every_section_has_exactly_one_pattern_category():
    assert PATTERN_CATEGORIES == SECTION_ORDER


@pytest.mark.parametrize("category", PATTERN_CATEGORIES)
def test_template_declares_procedure_only_where_allowed(category):
    assert bool(_PROCEDURE_DECLARATION_RE.search(_template(category))) == (category in PROCEDURE_SECTIONS)


def test_templates_assemble_into_a_valid_script():
    """The seven templates, in order, are one complete worked example."""
    script = "\n".join(f"-- SECTION: {category.upper()}\n{_template(category)}" for category in PATTERN_CATEGORIES)

    valid, issues = validate_script(script)

    assert valid is True, issues


def test_templates_do_not_reuse_a_golden_eval_prompt():
    """If the worked example were one of the golden prompts, the generation
    eval would measure copying, not generating."""
    procedure_names = set(re.findall(r"PROCEDURE\s+(\w+)", " ".join(map(_template, PATTERN_CATEGORIES))))

    assert not procedure_names & {"process_payroll_run", "onboard_employee", "approve_timesheet", "terminate_employee"}
