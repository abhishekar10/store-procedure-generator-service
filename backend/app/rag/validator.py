"""Best-effort structural sanity checks for a generated PL/pgSQL script.

Not a real SQL parser -- checks balance of parens/dollar-quoted blocks and
that all seven expected sections came back non-empty, which is enough to
flag the kinds of malformed output an LLM occasionally produces."""

from app.rag.section_parser import SECTION_ORDER, parse_sections


def validate_script(script: str) -> tuple[bool, list[str]]:
    issues = []

    if not script.strip():
        return False, ["Script is empty."]

    if script.count("(") != script.count(")"):
        issues.append("Unbalanced parentheses.")

    if script.count("$$") % 2 != 0:
        issues.append("Unbalanced $$ dollar-quoted blocks.")

    sections = parse_sections(script)
    missing = [name for name in SECTION_ORDER if not sections[name].strip()]
    if missing:
        issues.append(f"Missing or empty sections: {', '.join(missing)}.")

    return len(issues) == 0, issues
