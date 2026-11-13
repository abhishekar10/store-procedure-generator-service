-- Pattern: MAIN_SCRIPT
-- Statements inside the procedure body: the change the request actually
-- asks for, run against the staged rows. Statements only -- the procedure
-- header is in PRE_VALIDATION and the closing handler is in ROLLBACK_AUTO.

    UPDATE employees e
    SET department_id = p_to_department_id
    FROM tmp_employees_to_move t
    WHERE e.employee_id = t.employee_id;

    GET DIAGNOSTICS v_moved_count = ROW_COUNT;

    RAISE NOTICE 'Moved % employees from department % to %',
        v_moved_count, p_from_department_id, p_to_department_id;
