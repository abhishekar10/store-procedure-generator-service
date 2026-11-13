"""Best-effort structural sanity checks for a generated PL/pgSQL script.

Not a real SQL parser. It checks that parens and dollar-quoted blocks
balance, that all seven expected sections came back non-empty, and that the
main procedure actually landed in MAIN_SCRIPT -- enough to flag the kinds of
malformed output an LLM occasionally produces."""

import re

from app.rag.section_parser import SECTION_ORDER, parse_sections

_PROCEDURE_DECLARATION_RE = re.compile(r"CREATE\s+(?:OR\s+REPLACE\s+)?PROCEDURE\b", re.IGNORECASE)

# Sections allowed to declare a procedure. MAIN_SCRIPT holds the main one;
# ROLLBACK_MANUAL holds a separate, explicitly-invoked rollback procedure.
_PROCEDURE_SECTIONS = {"main_script", "rollback_manual"}

# MAIN_SCRIPT shorter than this fraction of the largest other section means
# the procedure body went somewhere else. The observed failure was 2-6%
# (a 98-240 char MAIN_SCRIPT beside a 4-5 KB ROLLBACK_AUTO); 25% leaves room
# for a legitimately large TEMP_TABLES or ROLLBACK_MANUAL.
_MAIN_SCRIPT_MIN_RATIO = 0.25


def validate_script(script: str) -> tuple[bool, list[str]]:
    issues = []

    if not script.strip():
        return False, ["Script is empty."]

    if script.count("(") != script.count(")"):
        issues.append("Unbalanced parentheses.")

    if script.count("$$") % 2 != 0:
        issues.append("Unbalanced $$ dollar-quoted blocks.")

    sections = {name: body.strip() for name, body in parse_sections(script).items()}
    missing = [name for name in SECTION_ORDER if not sections[name]]
    if missing:
        issues.append(f"Missing or empty sections: {', '.join(missing)}.")

    misplaced = [
        name.upper()
        for name in SECTION_ORDER
        if name not in _PROCEDURE_SECTIONS and _PROCEDURE_DECLARATION_RE.search(sections[name])
    ]
    if misplaced:
        issues.append(f"CREATE PROCEDURE appears outside MAIN_SCRIPT in: {', '.join(misplaced)}.")

    main_script = sections["main_script"]
    if main_script:
        if not _PROCEDURE_DECLARATION_RE.search(main_script):
            issues.append("MAIN_SCRIPT does not declare a procedure.")

        largest_name = max((name for name in SECTION_ORDER if name != "main_script"), key=lambda n: len(sections[n]))
        largest_len = len(sections[largest_name])
        if len(main_script) < largest_len * _MAIN_SCRIPT_MIN_RATIO:
            issues.append(
                f"MAIN_SCRIPT is dwarfed by {largest_name.upper()} ({len(main_script)} vs {largest_len} chars)."
            )

    return len(issues) == 0, issues
