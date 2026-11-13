-- Pattern: ROLLBACK_MANUAL
-- A separate procedure, defined after the main one has closed, to undo a run
-- that already committed. Auto-rollback only covers failures inside the same
-- transaction, not a change that needs undoing later. Restores the most
-- recent BACKUP batch.

CREATE OR REPLACE PROCEDURE rollback_reassign_department_employees(p_from_department_id INTEGER)
LANGUAGE plpgsql
AS $$
DECLARE
    v_batch         TIMESTAMP;
    v_restored_count INTEGER;
BEGIN
    SELECT MAX(backed_up_at) INTO v_batch
    FROM employees_department_backup
    WHERE department_id = p_from_department_id;

    IF v_batch IS NULL THEN
        RAISE EXCEPTION 'No backup found for department %, cannot roll back', p_from_department_id;
    END IF;

    UPDATE employees e
    SET department_id = b.department_id
    FROM employees_department_backup b
    WHERE b.employee_id = e.employee_id
      AND b.backed_up_at = v_batch
      AND b.department_id = p_from_department_id;

    GET DIAGNOSTICS v_restored_count = ROW_COUNT;

    RAISE NOTICE 'Restored % employees to department % from backup taken at %',
        v_restored_count, p_from_department_id, v_batch;
END;
$$;
