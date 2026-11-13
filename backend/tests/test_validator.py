from app.rag.validator import validate_script

SECTIONS = {
    "pre_validation": (
        "CREATE OR REPLACE PROCEDURE approve_timesheet(p_id INTEGER) LANGUAGE plpgsql AS $$\n"
        "DECLARE v_count INTEGER;\n"
        "BEGIN\n"
        "    IF NOT EXISTS (SELECT 1 FROM timesheets WHERE timesheet_id = p_id) THEN\n"
        "        RAISE EXCEPTION 'not found';\n"
        "    END IF;"
    ),
    "temp_tables": "    CREATE TEMP TABLE tmp_ids (id INTEGER) ON COMMIT DROP;",
    "backup": "    INSERT INTO timesheets_backup SELECT * FROM timesheets WHERE timesheet_id = p_id;",
    "main_script": "    UPDATE timesheets SET status = 'approved' WHERE timesheet_id = p_id;",
    "post_validation": "    IF NOT FOUND THEN RAISE EXCEPTION 'not updated'; END IF;",
    "rollback_auto": "EXCEPTION\n    WHEN OTHERS THEN\n        RAISE;\nEND;\n$$;",
    "rollback_manual": (
        "CREATE OR REPLACE PROCEDURE rollback_approve_timesheet(p_id INTEGER) LANGUAGE plpgsql AS $$\n"
        "BEGIN UPDATE timesheets SET status = 'pending' WHERE timesheet_id = p_id; END;\n$$;"
    ),
}


def _script(**overrides: str) -> str:
    bodies = {**SECTIONS, **overrides}
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
    script = VALID_SCRIPT.replace("approve_timesheet(p_id INTEGER)", "approve_timesheet(p_id INTEGER", 1)

    valid, issues = validate_script(script)

    assert valid is False
    assert any("parentheses" in issue for issue in issues)


def test_unbalanced_dollar_quotes_flagged():
    valid, issues = validate_script(_script(rollback_auto="EXCEPTION WHEN OTHERS THEN RAISE; END;"))

    assert valid is False
    assert any("Unbalanced $$" in issue for issue in issues)


def test_missing_section_flagged_by_name():
    script = "-- SECTION: PRE_VALIDATION\nonly one section\n"

    valid, issues = validate_script(script)

    assert valid is False
    missing_issue = next(issue for issue in issues if issue.startswith("Missing or empty sections"))
    assert "temp_tables" in missing_issue
    assert "rollback_manual" in missing_issue
    assert "pre_validation" not in missing_issue


def test_observed_defect_whole_procedure_under_rollback_auto_flagged():
    """The real failure: a short fragment where the procedure should be, and
    the full CREATE PROCEDURE emitted under ROLLBACK_AUTO."""
    misplaced = (
        "CREATE OR REPLACE PROCEDURE approve_timesheet(p_id INTEGER) LANGUAGE plpgsql AS $$ BEGIN "
        "UPDATE timesheets SET status = 'approved' WHERE timesheet_id = p_id; "
        "EXCEPTION WHEN OTHERS THEN RAISE; END; $$;"
    )
    script = _script(pre_validation="SELECT 1;", rollback_auto=misplaced)

    valid, issues = validate_script(script)

    assert valid is False
    assert any("must not declare one: ROLLBACK_AUTO" in issue for issue in issues)
    assert "PRE_VALIDATION does not open the main procedure." in issues


def test_separate_rollback_procedure_in_rollback_manual_allowed():
    valid, issues = validate_script(VALID_SCRIPT)

    assert "rollback_approve_timesheet" in VALID_SCRIPT
    assert valid is True, issues


def test_procedure_declared_in_main_script_flagged():
    script = _script(main_script="CREATE OR REPLACE PROCEDURE helper() LANGUAGE sql AS 'SELECT 1';")

    valid, issues = validate_script(script)

    assert valid is False
    assert any("must not declare one: MAIN_SCRIPT" in issue for issue in issues)


def test_rollback_auto_without_handler_flagged():
    valid, issues = validate_script(_script(rollback_auto="END;\n$$;"))

    assert valid is False
    assert any(issue.startswith("ROLLBACK_AUTO does not close the procedure") for issue in issues)


def test_do_block_inside_procedure_body_flagged():
    """The old habit: standalone DO $$ ... $$ blocks for validation. Inside the
    procedure body their $$ would terminate the body."""
    script = _script(post_validation="    DO $$ BEGIN NULL; END $$;")

    valid, issues = validate_script(script)

    assert valid is False
    assert any("would end it early, in: POST_VALIDATION" in issue for issue in issues)
