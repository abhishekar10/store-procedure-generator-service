-- Pattern: ROLLBACK_AUTO
-- Wrap the mutating work in a PL/pgSQL exception block so any failure (a
-- raised pre/post-validation error, or an unexpected runtime error)
-- automatically reverts everything the procedure did in this transaction.

CREATE OR REPLACE PROCEDURE process_payroll_run(p_payroll_run_id INTEGER)
LANGUAGE plpgsql
AS $$
BEGIN
    -- pre-validation, temp table staging, backup, and the main mutation go here

    -- any RAISE EXCEPTION above (or any unhandled error) is caught below,
    -- and PostgreSQL automatically rolls back everything this procedure did
EXCEPTION
    WHEN OTHERS THEN
        RAISE NOTICE 'process_payroll_run failed for run %: %', p_payroll_run_id, SQLERRM;
        RAISE;
END;
$$;
