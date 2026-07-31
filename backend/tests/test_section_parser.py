from app.rag.section_parser import SECTION_ORDER, parse_sections

WELL_FORMED_SCRIPT = """-- SECTION: PRE_VALIDATION
IF NOT EXISTS (SELECT 1 FROM payroll_runs WHERE payroll_run_id = p_id) THEN
    RAISE EXCEPTION 'not found';
END IF;

-- SECTION: TEMP_TABLES
CREATE TEMP TABLE tmp_calc (employee_id INTEGER);

-- SECTION: BACKUP
INSERT INTO payroll_entries_backup SELECT * FROM payroll_entries;

-- SECTION: MAIN_SCRIPT
UPDATE payroll_entries SET status = 'calculated';

-- SECTION: POST_VALIDATION
IF NOT FOUND THEN
    RAISE EXCEPTION 'post validation failed';
END IF;

-- SECTION: ROLLBACK_AUTO
EXCEPTION WHEN OTHERS THEN RAISE;

-- SECTION: ROLLBACK_MANUAL
CREATE OR REPLACE PROCEDURE rollback_payroll_run(p_id INTEGER) LANGUAGE plpgsql AS $$ BEGIN NULL; END; $$;
"""


def test_well_formed_script_populates_all_seven_sections():
    sections = parse_sections(WELL_FORMED_SCRIPT)

    assert set(sections.keys()) == set(SECTION_ORDER)
    for name in SECTION_ORDER:
        assert sections[name].strip(), f"expected {name} to be non-empty"

    assert "payroll_runs" in sections["pre_validation"]
    assert "CREATE TEMP TABLE" in sections["temp_tables"]
    assert "rollback_payroll_run" in sections["rollback_manual"]


def test_missing_marker_leaves_that_section_empty_without_breaking_others():
    script = "-- SECTION: PRE_VALIDATION\ncheck things\n-- SECTION: MAIN_SCRIPT\ndo the thing\n"

    sections = parse_sections(script)

    assert sections["pre_validation"] == "check things"
    assert sections["main_script"] == "do the thing"
    assert sections["temp_tables"] == ""
    assert sections["rollback_manual"] == ""


def test_empty_script_returns_all_empty_sections():
    sections = parse_sections("")

    assert all(value == "" for value in sections.values())
    assert set(sections.keys()) == set(SECTION_ORDER)


def test_unrecognized_marker_is_ignored_not_crashed_on():
    script = "-- SECTION: NOT_A_REAL_SECTION\nsome text\n-- SECTION: MAIN_SCRIPT\nreal content\n"

    sections = parse_sections(script)

    assert sections["main_script"] == "real content"
    assert "not_a_real_section" not in sections


def test_marker_matching_is_case_insensitive():
    script = "-- section: pre_validation\ncheck\n-- Section: Main_Script\nrun\n"

    sections = parse_sections(script)

    assert sections["pre_validation"] == "check"
    assert sections["main_script"] == "run"
