-- Pattern: TEMP_TABLES
-- Statements inside the procedure body. Stage the exact rows the procedure
-- will change, so BACKUP, MAIN_SCRIPT and POST_VALIDATION all work from the
-- same fixed set. ON COMMIT DROP cleans the temp table up with the
-- transaction. No DO blocks here: their dollar quote would end the body early.

    CREATE TEMP TABLE tmp_employees_to_move (
        employee_id INTEGER PRIMARY KEY
    ) ON COMMIT DROP;

    INSERT INTO tmp_employees_to_move (employee_id)
    SELECT employee_id
    FROM employees
    WHERE department_id = p_from_department_id
      AND employment_status IN ('active', 'on_leave');

    SELECT COUNT(*) INTO v_staged_count FROM tmp_employees_to_move;

    IF v_staged_count = 0 THEN
        RAISE EXCEPTION 'Department % has no employees to move', p_from_department_id;
    END IF;
