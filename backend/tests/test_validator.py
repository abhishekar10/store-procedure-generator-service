from app.rag.validator import validate_script

MAIN_PROCEDURE = (
    "CREATE OR REPLACE PROCEDURE process_payroll_run(p_id INTEGER) LANGUAGE plpgsql AS $$ "
    "BEGIN UPDATE payroll_runs SET status = 'processing' WHERE payroll_run_id = p_id; "
    "EXCEPTION WHEN OTHERS THEN RAISE; END; $$;"
)


def _script(**overrides: str) -> str:
    bodies = {
        "pre_validation": "DO $$ BEGIN NULL; END $$;",
        "temp_tables": "DO $$ BEGIN NULL; END $$;",
        "backup": "DO $$ BEGIN NULL; END $$;",
        "main_script": MAIN_PROCEDURE,
        "post_validation": "DO $$ BEGIN NULL; END $$;",
        "rollback_auto": "DO $$ BEGIN NULL; END $$;",
        "rollback_manual": "DO $$ BEGIN NULL; END $$;",
        **overrides,
    }
    return "\n".join(f"-- SECTION: {name.upper()}\n{body}" for name, body in bodies.items())


VALID_SCRIPT = _script()


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


def test_procedure_in_rollback_auto_flagged():
    script = _script(rollback_auto=MAIN_PROCEDURE)

    valid, issues = validate_script(script)

    assert valid is False
    assert any("outside MAIN_SCRIPT in: ROLLBACK_AUTO" in issue for issue in issues)


def test_separate_rollback_procedure_in_rollback_manual_allowed():
    rollback_procedure = (
        "CREATE OR REPLACE PROCEDURE rollback_payroll_run(p_id INTEGER) LANGUAGE plpgsql AS $$ "
        "BEGIN DELETE FROM payroll_entries WHERE payroll_run_id = p_id; END; $$;"
    )

    valid, issues = validate_script(_script(rollback_manual=rollback_procedure))

    assert valid is True, issues


def test_main_script_without_procedure_flagged():
    valid, issues = validate_script(_script(main_script="UPDATE payroll_runs SET status = 'done';"))

    assert valid is False
    assert "MAIN_SCRIPT does not declare a procedure." in issues


def test_observed_defect_main_script_dwarfed_by_rollback_auto():
    """The real failure: a short fragment in MAIN_SCRIPT while the full
    procedure landed under ROLLBACK_AUTO."""
    body = "UPDATE payroll_entries SET status = 'calculated' WHERE payroll_run_id = p_id; " * 50
    misplaced_procedure = (
        "CREATE OR REPLACE PROCEDURE process_payroll_run(p_id INTEGER) LANGUAGE plpgsql AS $$ "
        f"BEGIN {body} EXCEPTION WHEN OTHERS THEN RAISE; END; $$;"
    )
    script = _script(main_script="UPDATE payroll_runs SET status = 'done';", rollback_auto=misplaced_procedure)

    valid, issues = validate_script(script)

    assert valid is False
    assert any(issue.startswith("MAIN_SCRIPT is dwarfed by ROLLBACK_AUTO") for issue in issues)
    assert any("outside MAIN_SCRIPT" in issue for issue in issues)
