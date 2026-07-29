-- Pattern: PRE_VALIDATION
-- Fail fast before touching any data: confirm the target row(s) exist, are
-- in an expected state, and that inputs are sane.

DO $$
DECLARE
    v_run_status TEXT;
BEGIN
    SELECT status INTO v_run_status
    FROM payroll_runs
    WHERE payroll_run_id = p_payroll_run_id;

    IF v_run_status IS NULL THEN
        RAISE EXCEPTION 'Payroll run % does not exist', p_payroll_run_id;
    END IF;

    IF v_run_status NOT IN ('draft', 'processing') THEN
        RAISE EXCEPTION 'Payroll run % is in status %, expected draft or processing',
            p_payroll_run_id, v_run_status;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM employees WHERE employment_status = 'active'
    ) THEN
        RAISE EXCEPTION 'No active employees found, nothing to process';
    END IF;
END $$;
