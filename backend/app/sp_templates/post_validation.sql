-- Pattern: POST_VALIDATION
-- Statements inside the procedure body. Assert the mutation left the data
-- consistent. A RAISE here jumps to the ROLLBACK_AUTO handler, which undoes
-- the whole run.

    IF v_moved_count <> v_staged_count THEN
        RAISE EXCEPTION 'Moved % employees but staged %', v_moved_count, v_staged_count;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM employees e
        JOIN tmp_employees_to_move t ON t.employee_id = e.employee_id
        WHERE e.department_id <> p_to_department_id
    ) THEN
        RAISE EXCEPTION 'Some staged employees are not in department %', p_to_department_id;
    END IF;
