-- Pattern: ROLLBACK_MANUAL
-- A separate, explicitly-invoked procedure to reverse a run that already
-- committed successfully but needs to be undone later -- auto-rollback only
-- covers failures within the same transaction, not a change to undo after
-- the fact.

CREATE OR REPLACE PROCEDURE rollback_payroll_run(p_payroll_run_id INTEGER)
LANGUAGE plpgsql
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM payroll_entries_backup WHERE payroll_run_id = p_payroll_run_id
    ) THEN
        RAISE EXCEPTION 'No backup found for payroll run %, cannot roll back', p_payroll_run_id;
    END IF;

    DELETE FROM payroll_entries WHERE payroll_run_id = p_payroll_run_id;

    INSERT INTO payroll_entries
        (payroll_entry_id, payroll_run_id, employee_id, gross_pay, total_deductions, net_pay, status)
    SELECT payroll_entry_id, payroll_run_id, employee_id, gross_pay, total_deductions, net_pay, status
    FROM payroll_entries_backup
    WHERE payroll_run_id = p_payroll_run_id;

    UPDATE payroll_runs SET status = 'rolled_back' WHERE payroll_run_id = p_payroll_run_id;

    RAISE NOTICE 'Payroll run % rolled back from backup', p_payroll_run_id;
END;
$$;
