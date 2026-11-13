-- Pattern: ROLLBACK_AUTO
-- Closes the procedure: its EXCEPTION handler, END, and the closing dollar quote.
-- Any RAISE in an earlier section, or any unexpected error, lands here, and
-- PostgreSQL rolls back everything the procedure did in this transaction.
-- Re-raising keeps the failure visible to the caller instead of swallowing it.

EXCEPTION
    WHEN OTHERS THEN
        RAISE NOTICE 'reassign_department_employees failed: %', SQLERRM;
        RAISE;
END;
$$;
