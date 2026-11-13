-- Pattern: PRE_VALIDATION
-- Opens the procedure. The script is ONE procedure whose body runs from here
-- to the end of ROLLBACK_AUTO, with the section markers as comments inside it.
-- PRE_VALIDATION holds the CREATE OR REPLACE PROCEDURE header, a DECLARE
-- block for every variable the body uses, BEGIN, and then guard checks that
-- fail fast before any data is touched. Parameters and variables are in scope
-- in every later section.

CREATE OR REPLACE PROCEDURE reassign_department_employees(
    p_from_department_id INTEGER,
    p_to_department_id   INTEGER
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_staged_count INTEGER;
    v_moved_count  INTEGER;
BEGIN
    IF p_from_department_id IS NULL OR p_to_department_id IS NULL THEN
        RAISE EXCEPTION 'Both source and target department are required';
    END IF;

    IF p_from_department_id = p_to_department_id THEN
        RAISE EXCEPTION 'Source and target department are both %', p_from_department_id;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM departments WHERE department_id = p_from_department_id) THEN
        RAISE EXCEPTION 'Source department % does not exist', p_from_department_id;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM departments WHERE department_id = p_to_department_id) THEN
        RAISE EXCEPTION 'Target department % does not exist', p_to_department_id;
    END IF;
