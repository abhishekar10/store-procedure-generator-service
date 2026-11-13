"""The pattern corpus is retrieved into every generation prompt as "house
style for each section", so a template that breaks the section contract
teaches the model to break it too. That is how the full procedure ended up
under ROLLBACK_AUTO: its template was a complete CREATE PROCEDURE."""

import pytest

from app.config import PATTERN_CATEGORIES, get_settings
from app.rag.section_parser import SECTION_ORDER
from app.rag.validator import _PROCEDURE_DECLARATION_RE, _PROCEDURE_SECTIONS


def test_every_section_has_exactly_one_pattern_category():
    assert PATTERN_CATEGORIES == SECTION_ORDER


@pytest.mark.parametrize("category", PATTERN_CATEGORIES)
def test_template_obeys_section_procedure_contract(category):
    template = (get_settings().sp_templates_dir / f"{category}.sql").read_text()

    declares_procedure = bool(_PROCEDURE_DECLARATION_RE.search(template))

    assert declares_procedure == (category in _PROCEDURE_SECTIONS)
