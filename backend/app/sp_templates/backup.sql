-- Pattern: BACKUP
-- Statements inside the procedure body. Snapshot the rows about to be
-- mutated, so ROLLBACK_MANUAL can restore them after a committed run. now()
-- is fixed for the whole transaction, so every row from one run shares the
-- same backed_up_at and can be restored as one batch.

    CREATE TABLE IF NOT EXISTS employees_department_backup (
        backup_id      SERIAL PRIMARY KEY,
        employee_id    INTEGER NOT NULL,
        department_id  INTEGER NOT NULL,
        backed_up_at   TIMESTAMP NOT NULL DEFAULT now()
    );

    INSERT INTO employees_department_backup (employee_id, department_id)
    SELECT e.employee_id, e.department_id
    FROM employees e
    JOIN tmp_employees_to_move t ON t.employee_id = e.employee_id;
