-- Pattern: ROLLBACK_AUTO
-- The exception handler that closes the MAIN_SCRIPT procedure body. It is
-- not a procedure of its own: it is the EXCEPTION clause already present at
-- the end of MAIN_SCRIPT, restated here so the auto-rollback behaviour is
-- reviewable on its own. Any failure (a raised pre/post-validation error, or
-- an unexpected runtime error) lands here, and PostgreSQL reverts everything
-- the procedure did in this transaction. Re-raising keeps the failure
-- visible to the caller instead of swallowing it.

EXCEPTION
    WHEN OTHERS THEN
        RAISE NOTICE 'process_payroll_run failed for run %: %', p_payroll_run_id, SQLERRM;
        RAISE;
