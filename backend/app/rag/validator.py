"""Best-effort structural sanity checks for a generated PL/pgSQL script.

Not a real SQL parser. The expected shape is ONE main procedure whose body
spans the sections, with the `-- SECTION:` markers as comments inside it:

    PRE_VALIDATION   CREATE OR REPLACE PROCEDURE ... AS $$ DECLARE ... BEGIN <guards>
    TEMP_TABLES      statements inside the body
    BACKUP           statements inside the body
    MAIN_SCRIPT      statements inside the body
    POST_VALIDATION  statements inside the body
    ROLLBACK_AUTO    EXCEPTION WHEN OTHERS THEN ... END; $$;
    ROLLBACK_MANUAL  a separate CREATE OR REPLACE PROCEDURE rollback_...

These checks catch output that breaks that shape -- notably the observed
defect where the whole procedure landed under ROLLBACK_AUTO -- not genuine
PL/pgSQL correctness."""

import re

from app.rag.section_parser import SECTION_ORDER, parse_sections

_PROCEDURE_DECLARATION_RE = re.compile(r"CREATE\s+(?:OR\s+REPLACE\s+)?PROCEDURE\b", re.IGNORECASE)
_EXCEPTION_RE = re.compile(r"\bEXCEPTION\b", re.IGNORECASE)

# Sections allowed to declare a procedure: PRE_VALIDATION opens the main
# one; ROLLBACK_MANUAL holds a separate, explicitly-invoked rollback procedure.
PROCEDURE_SECTIONS = {"pre_validation", "rollback_manual"}

# Sections that sit inside the main procedure's dollar-quoted body. A bare
# `$$` in any of them (typically a pasted `DO $$ ... $$` block) would end the
# body early.
BODY_SECTIONS = ["temp_tables", "backup", "main_script", "post_validation"]


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
        if name not in PROCEDURE_SECTIONS and _PROCEDURE_DECLARATION_RE.search(sections[name])
    ]
    if misplaced:
        issues.append(f"CREATE PROCEDURE appears in a section that must not declare one: {', '.join(misplaced)}.")

    if sections["pre_validation"] and not _PROCEDURE_DECLARATION_RE.search(sections["pre_validation"]):
        issues.append("PRE_VALIDATION does not open the main procedure.")

    rollback_auto = sections["rollback_auto"]
    if rollback_auto and not (_EXCEPTION_RE.search(rollback_auto) and "$$" in rollback_auto):
        issues.append("ROLLBACK_AUTO does not close the procedure with an EXCEPTION handler and $$.")

    quoted = [name.upper() for name in BODY_SECTIONS if "$$" in sections[name]]
    if quoted:
        issues.append(f"$$ inside the procedure body would end it early, in: {', '.join(quoted)}.")

    return len(issues) == 0, issues
