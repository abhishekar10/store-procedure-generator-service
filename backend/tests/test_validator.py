from app.rag.validator import validate_script

VALID_SCRIPT = "\n".join(
    f"-- SECTION: {name.upper()}\nDO $$ BEGIN NULL; END $$;"
    for name in [
        "pre_validation",
        "temp_tables",
        "backup",
        "main_script",
        "post_validation",
        "rollback_auto",
        "rollback_manual",
    ]
)


def test_valid_script_passes_with_no_issues():
    valid, issues = validate_script(VALID_SCRIPT)

    assert valid is True
    assert issues == []


def test_empty_script_is_invalid():
    valid, issues = validate_script("")

    assert valid is False
    assert issues == ["Script is empty."]


def test_unbalanced_parentheses_flagged():
    script = VALID_SCRIPT.replace("BEGIN NULL;", "BEGIN foo(1, 2;")

    valid, issues = validate_script(script)

    assert valid is False
    assert any("parentheses" in issue for issue in issues)


def test_unbalanced_dollar_quotes_flagged():
    script = VALID_SCRIPT.replace("DO $$ BEGIN NULL; END $$;", "DO $$ BEGIN NULL; END;", 1)

    valid, issues = validate_script(script)

    assert valid is False
    assert any("$$" in issue for issue in issues)


def test_missing_section_flagged_by_name():
    script = "-- SECTION: PRE_VALIDATION\nonly one section\n"

    valid, issues = validate_script(script)

    assert valid is False
    missing_issue = next(issue for issue in issues if issue.startswith("Missing or empty sections"))
    assert "temp_tables" in missing_issue
    assert "rollback_manual" in missing_issue
    assert "pre_validation" not in missing_issue
