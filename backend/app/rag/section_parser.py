"""Parses a generated stored procedure script into its labeled sections."""

import re

SECTION_ORDER = [
    "pre_validation",
    "temp_tables",
    "backup",
    "main_script",
    "post_validation",
    "rollback_auto",
    "rollback_manual",
]

_MARKER_PATTERN = re.compile(r"--\s*SECTION:\s*([A-Z_]+)\s*\n", re.IGNORECASE)


def parse_sections(script: str) -> dict[str, str]:
    """Splits a script containing `-- SECTION: NAME` markers into a dict
    keyed by lowercase section name. A section with no marker present in the
    script comes back as an empty string; unrecognized markers are ignored."""
    sections = {name: "" for name in SECTION_ORDER}

    markers = list(_MARKER_PATTERN.finditer(script))
    for i, match in enumerate(markers):
        section_name = match.group(1).strip().lower()
        if section_name not in sections:
            continue
        start = match.end()
        end = markers[i + 1].start() if i + 1 < len(markers) else len(script)
        sections[section_name] = script[start:end].strip()

    return sections
