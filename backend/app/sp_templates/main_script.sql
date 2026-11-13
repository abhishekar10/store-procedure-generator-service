-- Pattern: MAIN_SCRIPT
-- The deployable procedure itself, and the largest section of the script.
-- It carries the main mutation, run against the rows staged in TEMP_TABLES
-- after the BACKUP snapshot, and it ends with the exception handler that
-- ROLLBACK_AUTO documents. Any RAISE inside the body, or any unexpected
-- error, makes PostgreSQL roll back everything this procedure did.

CREATE OR REPLACE PROCEDURE process_payroll_run(p_payroll_run_id INTEGER)
LANGUAGE plpgsql
AS $$
DECLARE
    v_rows_written INTEGER;
BEGIN
    UPDATE payroll_runs
    SET status = 'processing'
    WHERE payroll_run_id = p_payroll_run_id;

    DELETE FROM payroll_entries WHERE payroll_run_id = p_payroll_run_id;

    INSERT INTO payroll_entries
        (payroll_run_id, employee_id, gross_pay, total_deductions, net_pay, status)
    SELECT p_payroll_run_id, employee_id, gross_pay, total_deductions, net_pay, 'calculated'
    FROM tmp_payroll_calc;

    GET DIAGNOSTICS v_rows_written = ROW_COUNT;

    UPDATE payroll_runs
    SET status = 'finalized'
    WHERE payroll_run_id = p_payroll_run_id;

    RAISE NOTICE 'Payroll run % processed: % entries written', p_payroll_run_id, v_rows_written;
EXCEPTION
    WHEN OTHERS THEN
        RAISE NOTICE 'process_payroll_run failed for run %: %', p_payroll_run_id, SQLERRM;
        RAISE;
END;
$$;
