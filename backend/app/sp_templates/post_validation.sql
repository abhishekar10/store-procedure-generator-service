-- Pattern: POST_VALIDATION
-- Assert the mutation left the data in a consistent state before the
-- procedure commits to it. Raise on failure rather than continuing silently
-- -- the raise is what triggers the auto-rollback exception block.

DO $$
DECLARE
    v_calculated_total NUMERIC;
    v_committed_total   NUMERIC;
BEGIN
    SELECT SUM(net_pay) INTO v_calculated_total FROM tmp_payroll_calc;

    SELECT SUM(net_pay) INTO v_committed_total
    FROM payroll_entries
    WHERE payroll_run_id = p_payroll_run_id;

    IF v_committed_total IS DISTINCT FROM v_calculated_total THEN
        RAISE EXCEPTION
            'Post-validation failed: committed net pay total % does not match calculated total %',
            v_committed_total, v_calculated_total;
    END IF;

    IF EXISTS (
        SELECT 1 FROM payroll_entries
        WHERE payroll_run_id = p_payroll_run_id AND net_pay < 0
    ) THEN
        RAISE EXCEPTION 'Post-validation failed: negative net_pay found for run %', p_payroll_run_id;
    END IF;
END $$;
